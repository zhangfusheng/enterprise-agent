import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select, update

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.bootstrap.container import Container
from enterprise_agent.bootstrap.settings import Settings
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.events import EventType
from enterprise_agent.contracts.runs import RunCreate, RunStatus
from enterprise_agent.infrastructure.database.models import WorkItemRow
from enterprise_agent.infrastructure.repositories.runs import (
    OutcomeRecord,
    RunOutcome,
    RunRepository,
)
from enterprise_agent.runtime.lease_heartbeat import LeaseHeartbeat
from enterprise_agent.runtime.ownership import (
    LeaseGuard,
    LeaseLost,
    RenewResult,
    raise_if_lease_lost,
)


def _submit(container) -> str:
    return submit_run(
        container.runs,
        IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True),
        RunCreate(input="请直接回答一个问题"),
    )


def _expire(container, run_id: str) -> None:
    with container.sessions() as session:
        session.execute(
            update(WorkItemRow)
            .where(WorkItemRow.run_id == run_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        session.commit()


def test_heartbeat_renews_until_stopped():
    calls: list[int] = []
    guard = LeaseGuard()

    def renew() -> RenewResult:
        calls.append(1)
        return RenewResult.RENEWED

    with LeaseHeartbeat(renew, 0.05, guard):
        time.sleep(0.18)

    assert len(calls) >= 2
    assert not guard.lost


def test_rejected_renew_marks_lease_lost():
    guard = LeaseGuard()

    def renew() -> RenewResult:
        return RenewResult.NOT_OWNER

    with LeaseHeartbeat(renew, 0.05, guard):
        time.sleep(0.18)

    assert guard.lost
    with pytest.raises(LeaseLost):
        guard.raise_if_lost()


def test_heartbeat_extends_a_short_database_lease(container):
    run_id = _submit(container)
    assert container.runs.claim_work("worker-1", 2) == run_id
    lease = container.runs.current_lease("worker-1", run_id)
    assert lease is not None
    guard = LeaseGuard()

    def renew() -> RenewResult:
        return container.runs.renew_lease("worker-1", run_id, lease.token, 2)

    with LeaseHeartbeat(renew, 0.4, guard):
        time.sleep(2.6)

    assert not guard.lost
    assert container.runs.owns_lease("worker-1", run_id, lease.token)


def test_lost_heartbeat_cannot_commit_terminal_state(container, monkeypatch):
    run_id = _submit(container)
    assert container.runs.claim_work("worker-old", 30) == run_id
    monkeypatch.setattr(
        "enterprise_agent.application.execute_run.renew_interval_seconds",
        lambda *_args: 0.05,
    )
    monkeypatch.setattr(container.runs, "renew_lease", lambda *_args, **_kwargs: RenewResult.NOT_OWNER)
    monkeypatch.setattr(
        "enterprise_agent.application.execute_run.build_graph",
        lambda *_args, **_kwargs: _SlowGraph({"status": RunStatus.SUCCEEDED, "summary": "stale"}, 0.6),
    )

    with pytest.raises(LeaseLost):
        execute_run(container, run_id, "worker-old")

    view = container.runs.get_run_view("tenant-a", run_id)
    assert view.status == RunStatus.RUNNING
    assert view.summary != "stale"
    with container.sessions() as session:
        item = session.scalar(select(WorkItemRow).where(WorkItemRow.run_id == run_id))
    assert item is not None
    assert item.status != "DONE"


def test_fencing_token_blocks_the_previous_worker(container):
    run_id = _submit(container)
    assert container.runs.claim_work("worker-old", 30) == run_id
    original = container.runs.current_lease("worker-old", run_id)
    assert original is not None
    _expire(container, run_id)
    assert container.runs.claim_work("worker-new", 30) == run_id
    replacement = container.runs.current_lease("worker-new", run_id)
    assert replacement is not None
    assert replacement.token != original.token
    assert replacement.generation == original.generation + 1

    with pytest.raises(LeaseLost):
        container.runs.commit_outcome(
            "tenant-a",
            run_id,
            "worker-old",
            original.token,
            RunOutcome(status=RunStatus.SUCCEEDED, state={}, summary="stale"),
        )

    container.runs.mark_running("tenant-a", run_id, "worker-new", replacement.token)
    container.runs.commit_outcome(
        "tenant-a",
        run_id,
        "worker-new",
        replacement.token,
        RunOutcome(status=RunStatus.SUCCEEDED, state={}, summary="fresh"),
    )
    view = container.runs.get_run_view("tenant-a", run_id)
    assert view.status == RunStatus.SUCCEEDED
    assert view.summary == "fresh"
    with container.sessions() as session:
        item = session.scalar(select(WorkItemRow).where(WorkItemRow.run_id == run_id))
    assert item is not None
    assert item.status == "DONE"


def test_outcome_and_done_roll_back_together(container, monkeypatch):
    run_id = _submit(container)
    assert container.runs.claim_work("worker-a", 30) == run_id
    lease = container.runs.current_lease("worker-a", run_id)
    assert lease is not None
    container.runs.mark_running("tenant-a", run_id, "worker-a", lease.token)
    original = RunRepository._append_event

    def boom(self, session, *args, **kwargs):
        original(self, session, *args, **kwargs)
        raise RuntimeError("boom")

    monkeypatch.setattr(RunRepository, "_append_event", boom)
    with pytest.raises(RuntimeError, match="boom"):
        container.runs.commit_outcome(
            "tenant-a",
            run_id,
            "worker-a",
            lease.token,
            RunOutcome(
                status=RunStatus.SUCCEEDED,
                state={"summary": "partial"},
                summary="partial",
                events=(OutcomeRecord(EventType.RUN_COMPLETED, "lifecycle", "完成"),),
            ),
        )

    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.RUNNING
    with container.sessions() as session:
        item = session.scalar(select(WorkItemRow).where(WorkItemRow.run_id == run_id))
    assert item is not None
    assert item.status == "RUNNING"


def test_execution_longer_than_the_lease_still_commits(tmp_path: Path, monkeypatch):
    container = Container(
        Settings(
            environment="development",
            database_url=f"sqlite:///{tmp_path}/app.sqlite",
            checkpoint_url=f"sqlite:///{tmp_path}/checkpoint.sqlite",
            artifact_root=tmp_path / "artifacts",
            offline_mode=True,
            dev_identity=True,
            lease_seconds=5,
            lease_renew_interval_seconds=1,
        )
    )
    monkeypatch.setattr(
        "enterprise_agent.application.execute_run.build_graph",
        lambda *_args, **_kwargs: _SlowGraph(
            {"status": RunStatus.SUCCEEDED, "summary": "done"},
            6,
        ),
    )
    try:
        run_id = _submit(container)
        assert container.runs.claim_work("worker-a", 5) == run_id
        execute_run(container, run_id, "worker-a")
        view = container.runs.get_run_view("tenant-a", run_id)
        assert view.status == RunStatus.SUCCEEDED
        assert view.summary == "done"
    finally:
        container.close()


def test_expired_worker_is_replaced_by_a_new_token(container):
    run_id = _submit(container)
    assert container.runs.claim_work("worker-1", 5) == run_id
    _expire(container, run_id)
    assert container.runs.claim_work("worker-2", 30) == run_id
    assert container.runs.current_lease("worker-1", run_id) is None
    assert container.runs.current_lease("worker-2", run_id) is not None


def test_claim_limit_moves_work_to_dead(container):
    run_id = _submit(container)
    with container.sessions() as session:
        item = session.scalar(select(WorkItemRow).where(WorkItemRow.run_id == run_id))
        assert item is not None
        item.claim_attempts = 8
        session.commit()

    assert container.runs.claim_work("worker-a", 30, max_claim_attempts=8) is None
    view = container.runs.get_run_view("tenant-a", run_id)
    assert view.status == RunStatus.FAILED
    assert view.summary == "超过重试上限"
    with container.sessions() as session:
        item = session.scalar(select(WorkItemRow).where(WorkItemRow.run_id == run_id))
    assert item is not None
    assert item.status == "DEAD"


class _SlowGraph:
    def __init__(self, result, delay: float):
        self.result = result
        self.delay = delay

    def invoke(self, *_args, **_kwargs):
        time.sleep(self.delay)
        raise_if_lease_lost()
        return self.result
