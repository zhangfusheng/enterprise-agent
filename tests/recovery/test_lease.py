from concurrent.futures import ThreadPoolExecutor

from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate


def test_second_worker_cannot_claim_live_lease(container):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), RunCreate(input="hello"))
    assert container.runs.claim_work("worker-1", 30) == run_id
    assert container.runs.claim_work("worker-2", 30) is None


def test_concurrent_claim_has_one_winner(container):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), RunCreate(input="hello"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(container.runs.claim_work, ["worker-a", "worker-b"], [30, 30]))
    assert results.count(run_id) == 1
    assert results.count(None) == 1


def test_expired_lease_can_be_reclaimed(container):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), RunCreate(input="hello"))
    assert container.runs.claim_work("worker-1", 5) == run_id
    with container.sessions() as session:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update

        from enterprise_agent.infrastructure.database.models import WorkItemRow
        session.execute(update(WorkItemRow).where(WorkItemRow.run_id == run_id).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        session.commit()
    assert container.runs.claim_work("worker-2", 30) == run_id

