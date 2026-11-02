from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, func, inspect, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from enterprise_agent.contracts.artifacts import ArtifactRef
from enterprise_agent.contracts.delegations import DelegationRecord
from enterprise_agent.contracts.events import EventType, RunEvent
from enterprise_agent.contracts.plans import GlobalPlan
from enterprise_agent.contracts.runs import RunCreate, RunStatus, RunView
from enterprise_agent.contracts.sessions import (
    SessionMessage,
    SessionMessageRole,
    SessionSummary,
    SessionView,
)
from enterprise_agent.infrastructure.database.models import (
    ArtifactRow,
    ChatMessageRow,
    ChatSessionRow,
    DelegationRow,
    EventRow,
    PlanRow,
    RunRow,
    WorkItemRow,
)
from enterprise_agent.runtime.lifecycle import ALLOWED_TRANSITIONS, assert_transition
from enterprise_agent.runtime.ownership import (
    LeaseLost,
    RenewResult,
    RetryExhausted,
    lease_deadline,
    lease_expired,
    retry_delay_seconds,
)

_TERMINAL = frozenset(status for status, allowed in ALLOWED_TRANSITIONS.items() if not allowed)
_QUEUED = {
    "status": "QUEUED",
    "lease_expires_at": None,
    "worker_id": None,
    "lease_token": None,
    "last_heartbeat_at": None,
}
_DEFAULT_MAX_CLAIM_ATTEMPTS = 8
_DEFAULT_MAX_EXECUTION_ATTEMPTS = 5
_DEFAULT_MAX_LEASE_LOSSES = 5


@dataclass(frozen=True)
class WorkLease:
    run_id: str
    worker_id: str
    token: str
    generation: int


@dataclass(frozen=True)
class OutcomeRecord:
    event_type: EventType
    stage: str
    message: str
    data: dict | None = None


@dataclass(frozen=True)
class RunOutcome:
    """One fenced terminal write for a run and its work item."""

    status: RunStatus
    state: dict
    route: str | None = None
    summary: str | None = None
    budget: dict | None = None
    events: tuple[OutcomeRecord, ...] = ()
    plan: GlobalPlan | None = None
    artifacts: tuple[ArtifactRef, ...] = ()
    delegations: tuple[DelegationRecord, ...] = ()


class TenantNotFound(PermissionError):
    pass


class RunRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory
        self._backfill_legacy_sessions()

    def _backfill_legacy_sessions(self) -> None:
        """Make pre-chat runs visible without changing their agent semantics."""
        with self._transaction() as session:
            bind = session.get_bind()
            tables = set(inspect(bind).get_table_names())
            if "runs" not in tables or "chat_messages" not in tables:
                return
            rows = session.scalars(
                select(RunRow)
                .outerjoin(
                    ChatMessageRow,
                    (ChatMessageRow.run_id == RunRow.run_id)
                    & (ChatMessageRow.role == SessionMessageRole.USER),
                )
                .where(ChatMessageRow.message_id.is_(None))
            ).all()
            for row in rows:
                now = row.updated_at or row.created_at
                conversation = _get_or_create_chat_session(
                    session,
                    f"session_{row.run_id}",
                    row.tenant_id,
                    row.user_id,
                    row.input_text,
                    row.created_at,
                )
                session.add(
                    _chat_message(
                        session,
                        conversation,
                        row.run_id,
                        SessionMessageRole.USER,
                        row.input_text,
                        row.created_at,
                    )
                )
                session.flush()
                if row.summary:
                    _upsert_assistant_message(
                        session,
                        conversation,
                        row.run_id,
                        row.summary,
                        now,
                    )
                conversation.updated_at = now

    def create(
        self, tenant_id: str, user_id: str, request: RunCreate, run_id: str | None = None
    ) -> str:
        try:
            with self._transaction() as session:
                if request.idempotency_key:
                    existing = _run_id_for_idempotency_key(session, tenant_id, request.idempotency_key)
                    if existing:
                        return existing
                created = run_id or _new_id("run")
                now = datetime.now(UTC)
                session_id = request.session_id or _new_id("session")
                conversation = _get_or_create_chat_session(
                    session,
                    session_id,
                    tenant_id,
                    user_id,
                    _conversation_message(request),
                    now,
                )
                session.add(_run_row(created, tenant_id, user_id, request, now))
                session.add(_work_row(created, now))
                session.add(
                    _chat_message(
                        session,
                        conversation,
                        created,
                        SessionMessageRole.USER,
                        _conversation_message(request),
                        now,
                    )
                )
                session.flush()
                _upsert_assistant_message(
                    session,
                    conversation,
                    created,
                    _status_message(RunStatus.PENDING, None),
                    now,
                )
                self._append_event(
                    session, created, tenant_id, EventType.RUN_CREATED, "submit", "Run 已创建"
                )
                return created
        except IntegrityError:
            if not request.idempotency_key:
                raise
            with self.session_factory() as session:
                existing = _run_id_for_idempotency_key(session, tenant_id, request.idempotency_key)
            if existing:
                return existing
            raise

    def session_id_for_run(self, tenant_id: str, user_id: str, run_id: str) -> str:
        with self.session_factory() as session:
            session_id = session.scalar(
                select(ChatMessageRow.session_id).where(
                    ChatMessageRow.run_id == run_id,
                    ChatMessageRow.tenant_id == tenant_id,
                    ChatMessageRow.user_id == user_id,
                    ChatMessageRow.role == SessionMessageRole.USER,
                )
            )
            if session_id is None:
                raise TenantNotFound("run not found")
            return session_id

    def list_session_views(
        self,
        tenant_id: str,
        user_id: str,
        *,
        query: str | None = None,
        limit: int = 100,
    ) -> list[SessionSummary]:
        with self.session_factory() as session:
            statement = (
                select(ChatSessionRow)
                .where(ChatSessionRow.tenant_id == tenant_id, ChatSessionRow.user_id == user_id)
                .order_by(ChatSessionRow.updated_at.desc(), ChatSessionRow.session_id.desc())
                .limit(limit)
            )
            if query:
                statement = statement.where(ChatSessionRow.title.ilike(f"%{query}%"))
            rows = session.scalars(statement).all()
            return [_session_summary(session, row) for row in rows]

    def get_session_view(self, tenant_id: str, user_id: str, session_id: str) -> SessionView:
        with self.session_factory() as session:
            row = _get_chat_session(session, session_id, tenant_id, user_id)
            return _session_view(session, row)

    def conversation_context(
        self, tenant_id: str, user_id: str, session_id: str, limit: int = 8
    ) -> str:
        with self.session_factory() as session:
            _get_chat_session(session, session_id, tenant_id, user_id)
            messages = session.scalars(
                select(ChatMessageRow)
                .where(
                    ChatMessageRow.session_id == session_id,
                    ChatMessageRow.tenant_id == tenant_id,
                    ChatMessageRow.user_id == user_id,
                )
                .order_by(ChatMessageRow.sequence.desc())
                .limit(limit)
            ).all()
            return "\n".join(
                f"{('用户' if item.role == SessionMessageRole.USER else 'Agent')}：{item.content}"
                for item in reversed(messages)
            )

    def get_run_view(self, tenant_id: str, run_id: str) -> RunView:
        with self.session_factory() as session:
            row = _get_run(session, run_id, tenant_id)
            return _run_view(row, _session_id_for_run(session, row))

    def list_run_views(
        self,
        tenant_id: str,
        *,
        query: str | None = None,
        status: RunStatus | None = None,
        limit: int = 50,
    ) -> list[RunView]:
        with self.session_factory() as session:
            statement = (
                select(RunRow)
                .where(RunRow.tenant_id == tenant_id)
                .order_by(RunRow.updated_at.desc(), RunRow.run_id.desc())
                .limit(limit)
            )
            if status is not None:
                statement = statement.where(RunRow.status == status)
            if query:
                pattern = f"%{query}%"
                statement = statement.where(
                    or_(
                        RunRow.run_id.ilike(pattern),
                        RunRow.input_text.ilike(pattern),
                        RunRow.summary.ilike(pattern),
                    )
                )
            return [_run_view(row, _session_id_for_run(session, row)) for row in session.scalars(statement).all()]

    def get_run(self, tenant_id: str, run_id: str) -> RunRow:
        with self.session_factory() as session:
            row = _get_run(session, run_id, tenant_id)
            session.expunge(row)
            return row

    def get_run_unscoped(self, run_id: str) -> RunRow:
        with self.session_factory() as session:
            row = _get_run_unscoped(session, run_id)
            session.expunge(row)
            return row

    def transition(
        self,
        tenant_id: str,
        run_id: str,
        target: RunStatus,
        *,
        summary: str | None = None,
        state: dict | None = None,
        event_type: EventType | None = None,
        message: str | None = None,
    ) -> None:
        with self._transaction() as session:
            row = _lock_run(session, run_id, tenant_id)
            current = RunStatus(row.status)
            if current != target:
                assert_transition(current, target)
            row.status = target
            if summary is not None:
                row.summary = summary
            _merge_run_state(row, state)
            row.updated_at = datetime.now(UTC)
            conversation = _chat_session_for_run(session, run_id, tenant_id)
            if conversation is not None:
                content = summary or row.summary or _status_message(target, row)
                _upsert_assistant_message(session, conversation, run_id, content, row.updated_at)
                conversation.updated_at = row.updated_at
            self._append_event(
                session,
                run_id,
                tenant_id,
                event_type or EventType.RUN_STATUS,
                "lifecycle",
                message or f"状态变更为 {target}",
            )

    def update_run(
        self,
        tenant_id: str,
        run_id: str,
        state: dict,
        route: str | None = None,
        summary: str | None = None,
        budget: dict | None = None,
    ) -> None:
        with self._transaction() as session:
            row = _lock_run(session, run_id, tenant_id)
            _merge_run_state(row, state)
            if route is not None:
                row.route = route
            if summary is not None:
                row.summary = summary
            if budget is not None:
                row.budget_json = budget
            row.updated_at = datetime.now(UTC)
            _touch_chat_session(session, run_id, tenant_id, row.updated_at)

    def append_input(self, tenant_id: str, run_id: str, content: str) -> None:
        """Append one user answer to a run waiting for input."""
        value = content.strip()
        if not value:
            raise ValueError("input must not be blank")
        with self._transaction() as session:
            row = _lock_run(session, run_id, tenant_id)
            if RunStatus(row.status) != RunStatus.WAITING_INPUT:
                raise ValueError("run is not waiting for input")
            state = row.state_json if isinstance(row.state_json, dict) else {}
            raw_inputs = state.get("inputs", [])
            if not isinstance(raw_inputs, list):
                raise ValueError("run inputs must be a list")
            inputs = list(raw_inputs)
            legacy_additional = state.get("additional_input")
            if isinstance(legacy_additional, str) and legacy_additional.strip():
                if not any(
                    item.get("name") == "user_input"
                    and item.get("content") == legacy_additional
                    for item in inputs
                    if isinstance(item, dict)
                ):
                    inputs.append({"name": "user_input", "content": legacy_additional})
            inputs.append({"name": "user_input", "content": value})
            row.state_json = {**state, "inputs": inputs, "additional_input": None}
            now = datetime.now(UTC)
            row.updated_at = now
            conversation = _chat_session_for_run(session, run_id, tenant_id)
            if conversation is not None:
                session.add(
                    _chat_message(
                        session,
                        conversation,
                        run_id,
                        SessionMessageRole.USER,
                        value,
                        now,
                    )
                )
                conversation.updated_at = now

    def request_cancel(self, tenant_id: str, run_id: str) -> None:
        with self._transaction() as session:
            row = _lock_run(session, run_id, tenant_id)
            if RunStatus(row.status) in _TERMINAL:
                return
            row.cancel_requested = True
            row.updated_at = datetime.now(UTC)
            self._append_event(
                session, run_id, tenant_id, EventType.RUN_CANCELLED, "lifecycle", "已请求取消"
            )

    def is_cancel_requested(self, tenant_id: str, run_id: str) -> bool:
        with self.session_factory() as session:
            requested = session.scalar(
                select(RunRow.cancel_requested).where(
                    RunRow.run_id == run_id, RunRow.tenant_id == tenant_id
                )
            )
            return bool(requested)

    def events(
        self, tenant_id: str, run_id: str, after: int = 0, limit: int = 100
    ) -> list[RunEvent]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(EventRow)
                .where(
                    EventRow.run_id == run_id,
                    EventRow.tenant_id == tenant_id,
                    EventRow.sequence > after,
                )
                .order_by(EventRow.sequence)
                .limit(limit)
            ).all()
            return [_event_view(row) for row in rows]

    def record_event(
        self,
        tenant_id: str,
        run_id: str,
        event_type: EventType,
        stage: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        with self._transaction() as session:
            _lock_run(session, run_id, tenant_id)
            self._append_event(session, run_id, tenant_id, event_type, stage, message, data)

    def save_plan(self, tenant_id: str, run_id: str, plan: GlobalPlan) -> None:
        with self._transaction() as session:
            session.merge(
                PlanRow(
                    plan_id=f"{run_id}:v{plan.plan_version}",
                    run_id=run_id,
                    tenant_id=tenant_id,
                    version=plan.plan_version,
                    plan_json=plan.model_dump(mode="json"),
                    created_at=datetime.now(UTC),
                )
            )

    def save_delegation(self, tenant_id: str, run_id: str, record: DelegationRecord) -> None:
        with self._transaction() as session:
            session.merge(
                DelegationRow(
                    delegation_id=record.delegation_id,
                    run_id=run_id,
                    tenant_id=tenant_id,
                    agent_id=record.agent_id,
                    parent_agent_id=record.parent_agent_id,
                    role=record.role,
                    depth=record.depth,
                    status=record.status,
                    input_summary=record.input_summary,
                    result_summary=record.result_summary,
                    metadata_json=record.metadata,
                    created_at=datetime.now(UTC),
                )
            )

    def claim_work(
        self,
        worker_id: str,
        lease_seconds: int,
        *,
        max_claim_attempts: int = _DEFAULT_MAX_CLAIM_ATTEMPTS,
    ) -> str | None:
        now = datetime.now(UTC)
        with self._transaction() as session:
            _begin_write(session)
            self._dead_letter_exhausted(session, now, max_claim_attempts)
            item = session.scalar(
                select(WorkItemRow)
                .where(_claimable(now, max_claim_attempts))
                .order_by(WorkItemRow.available_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if item is None:
                return None
            item.status = "RUNNING"
            item.worker_id = worker_id
            item.lease_expires_at = lease_deadline(lease_seconds)
            item.lease_token = uuid4().hex
            item.lease_generation = int(item.lease_generation or 0) + 1
            item.last_heartbeat_at = now
            item.claim_attempts = int(item.claim_attempts or 0) + 1
            item.attempts = item.claim_attempts
            return item.run_id

    def current_lease(self, worker_id: str, run_id: str) -> WorkLease | None:
        with self.session_factory() as session:
            item = session.scalar(select(WorkItemRow).where(WorkItemRow.run_id == run_id))
            if not _holds_lease(item, worker_id, item.lease_token if item is not None else None):
                return None
            assert item is not None and item.lease_token is not None
            return WorkLease(
                run_id=item.run_id,
                worker_id=worker_id,
                token=item.lease_token,
                generation=int(item.lease_generation or 0),
            )

    def renew_lease(
        self, worker_id: str, run_id: str, lease_token: str, lease_seconds: int
    ) -> RenewResult:
        now = datetime.now(UTC)
        with self._transaction() as session:
            _begin_write(session)
            changed = _update_work(
                session,
                run_id,
                {
                    "lease_expires_at": lease_deadline(lease_seconds),
                    "last_heartbeat_at": now,
                },
                worker_id=worker_id,
                status="RUNNING",
                lease_token=lease_token,
                unexpired_after=now,
            )
            return RenewResult.RENEWED if changed == 1 else RenewResult.NOT_OWNER

    def begin_execution(
        self,
        worker_id: str,
        run_id: str,
        lease_token: str,
        *,
        max_execution_attempts: int = _DEFAULT_MAX_EXECUTION_ATTEMPTS,
    ) -> None:
        exhausted = False
        with self._transaction() as session:
            _begin_write(session)
            item = _lock_work(session, run_id)
            if not _holds_lease(item, worker_id, lease_token):
                raise LeaseLost(f"lease lost before execution for {run_id}")
            assert item is not None
            item.execution_attempts = int(item.execution_attempts or 0) + 1
            if item.execution_attempts > max_execution_attempts:
                self._dead_letter(session, item, "执行次数超过上限")
                exhausted = True
        if exhausted:
            raise RetryExhausted(run_id)

    def mark_running(self, tenant_id: str, run_id: str, worker_id: str, lease_token: str) -> None:
        with self._transaction() as session:
            _begin_write(session)
            item = _lock_work(session, run_id)
            if not _holds_lease(item, worker_id, lease_token):
                raise LeaseLost(f"lease lost before marking {run_id} running")
            row = _lock_run(session, run_id, tenant_id)
            current = RunStatus(row.status)
            if current == RunStatus.RUNNING:
                return
            assert_transition(current, RunStatus.RUNNING)
            row.status = RunStatus.RUNNING
            row.updated_at = datetime.now(UTC)
            conversation = _chat_session_for_run(session, run_id, tenant_id)
            if conversation is not None:
                _upsert_assistant_message(session, conversation, run_id, "正在执行…", row.updated_at)
                conversation.updated_at = row.updated_at
            self._append_event(
                session,
                run_id,
                tenant_id,
                EventType.RUN_STATUS,
                "lifecycle",
                "Worker 开始执行",
            )

    def commit_outcome(
        self,
        tenant_id: str,
        run_id: str,
        worker_id: str,
        lease_token: str,
        outcome: RunOutcome,
    ) -> None:
        """Write the run result and finish the work item in one transaction.

        The fencing token is checked in the same statement sequence as the
        writes. A worker that lost the lease cannot leave a partial outcome.
        """
        now = datetime.now(UTC)
        with self._transaction() as session:
            _begin_write(session)
            item = _lock_work(session, run_id)
            if not _holds_lease(item, worker_id, lease_token):
                raise LeaseLost(f"lease lost before commit for {run_id}")
            assert item is not None
            row = _lock_run(session, run_id, tenant_id)
            current = RunStatus(row.status)
            if current != outcome.status:
                assert_transition(current, outcome.status)
            _merge_run_state(row, outcome.state)
            if outcome.route is not None:
                row.route = outcome.route
            if outcome.summary is not None:
                row.summary = outcome.summary
            if outcome.budget is not None:
                row.budget_json = outcome.budget
            row.status = outcome.status
            row.updated_at = now
            conversation = _chat_session_for_run(session, run_id, tenant_id)
            if conversation is not None:
                content = outcome.summary or row.summary or _status_message(outcome.status, row)
                _upsert_assistant_message(session, conversation, run_id, content, now)
                conversation.updated_at = now
            for event in outcome.events:
                self._append_event(
                    session,
                    run_id,
                    tenant_id,
                    event.event_type,
                    event.stage,
                    event.message,
                    event.data,
                )
            if outcome.plan is not None:
                session.merge(
                    PlanRow(
                        plan_id=f"{run_id}:v{outcome.plan.plan_version}",
                        run_id=run_id,
                        tenant_id=tenant_id,
                        version=outcome.plan.plan_version,
                        plan_json=outcome.plan.model_dump(mode="json"),
                        created_at=now,
                    )
                )
            for artifact in outcome.artifacts:
                _add_artifact(session, artifact)
            for record in outcome.delegations:
                session.merge(_delegation_row(tenant_id, run_id, record, now))
            item.status = "DONE"
            item.lease_expires_at = None
            item.lease_token = None
            item.last_heartbeat_at = None

    def note_lease_loss(
        self,
        worker_id: str,
        run_id: str,
        lease_token: str,
        *,
        max_lease_losses: int = _DEFAULT_MAX_LEASE_LOSSES,
    ) -> None:
        """Requeue or dead-letter only when this token still owns the row."""
        with self._transaction() as session:
            _begin_write(session)
            item = _lock_work(session, run_id)
            if not _token_matches(item, worker_id, lease_token):
                return
            assert item is not None
            item.lease_loss_count = int(item.lease_loss_count or 0) + 1
            run = session.scalar(select(RunRow).where(RunRow.run_id == run_id))
            if item.lease_loss_count >= max_lease_losses:
                self._dead_letter(session, item, "租约丢失次数超过上限")
                return
            delay = retry_delay_seconds(item.lease_loss_count)
            _return_to_queue(item, datetime.now(UTC) + timedelta(seconds=delay))
            if run is not None:
                self._append_event(
                    session,
                    run_id,
                    run.tenant_id,
                    EventType.LEASE_LOST,
                    "recovery",
                    "租约丢失，工作项已延迟重新排队",
                    {"lease_loss_count": item.lease_loss_count},
                )

    def finish_work(self, worker_id: str, run_id: str) -> None:
        with self._transaction() as session:
            _update_work(
                session,
                run_id,
                {
                    "status": "DONE",
                    "lease_expires_at": None,
                    "lease_token": None,
                    "last_heartbeat_at": None,
                },
                worker_id=worker_id,
            )

    def release_work(self, worker_id: str, run_id: str) -> None:
        with self._transaction() as session:
            _begin_write(session)
            item = session.scalar(
                select(WorkItemRow)
                .where(
                    WorkItemRow.run_id == run_id,
                    WorkItemRow.worker_id == worker_id,
                    WorkItemRow.status == "RUNNING",
                )
                .with_for_update()
            )
            if item is None:
                return
            delay = retry_delay_seconds(int(item.claim_attempts or 1))
            _return_to_queue(item, datetime.now(UTC) + timedelta(seconds=delay))

    def requeue_work(self, run_id: str, tenant_id: str | None = None) -> None:
        with self._transaction() as session:
            if tenant_id is not None:
                _lock_run(session, run_id, tenant_id)
            _update_work(session, run_id, {**_QUEUED, "available_at": datetime.now(UTC)})

    def requeue_expired(self, run_id: str | None = None) -> list[str]:
        """Return expired RUNNING work to the queue and record an audit event.

        A live lease is left alone. Stop the worker that still holds the run
        before calling this, so the old execution cannot overlap a new one.
        """
        now = datetime.now(UTC)
        with self._transaction() as session:
            _begin_write(session)
            statement = select(WorkItemRow).where(
                WorkItemRow.status == "RUNNING",
                WorkItemRow.lease_expires_at < now,
            )
            if run_id is not None:
                statement = statement.where(WorkItemRow.run_id == run_id)
            rows = list(session.scalars(statement).all())
            requeued: list[str] = []
            for item in rows:
                _return_to_queue(item, now)
                requeued.append(item.run_id)
                owner = session.scalar(select(RunRow).where(RunRow.run_id == item.run_id))
                if owner is None:
                    continue
                self._append_event(
                    session,
                    item.run_id,
                    owner.tenant_id,
                    EventType.WORK_REQUEUED,
                    "recovery",
                    "过期工作项已重新排队",
                )
            return requeued

    def owns_lease(self, worker_id: str, run_id: str, lease_token: str | None = None) -> bool:
        with self.session_factory() as session:
            item = session.scalar(
                select(WorkItemRow).where(
                    WorkItemRow.run_id == run_id,
                    WorkItemRow.worker_id == worker_id,
                    WorkItemRow.status == "RUNNING",
                )
            )
            if lease_token is None:
                return item is not None and not lease_expired(item.lease_expires_at)
            return _holds_lease(item, worker_id, lease_token)

    def _dead_letter_exhausted(self, session: Session, now: datetime, max_claim_attempts: int) -> None:
        rows = session.scalars(
            select(WorkItemRow).where(
                WorkItemRow.claim_attempts >= max_claim_attempts,
                or_(
                    and_(WorkItemRow.status == "QUEUED", WorkItemRow.available_at <= now),
                    and_(
                        WorkItemRow.status == "RUNNING",
                        WorkItemRow.lease_expires_at < now,
                    ),
                ),
            )
        ).all()
        for item in rows:
            self._dead_letter(session, item, "认领次数超过上限")

    def _dead_letter(self, session: Session, item: WorkItemRow, reason: str) -> None:
        run = session.scalar(select(RunRow).where(RunRow.run_id == item.run_id).with_for_update())
        now = datetime.now(UTC)
        if run is not None and RunStatus(run.status) not in _TERMINAL:
            current = RunStatus(run.status)
            if current in {RunStatus.PENDING, RunStatus.WAITING_INPUT}:
                assert_transition(current, RunStatus.RUNNING)
                current = RunStatus.RUNNING
            assert_transition(current, RunStatus.FAILED)
            run.status = RunStatus.FAILED
            run.summary = "超过重试上限"
            run.updated_at = now
            state = dict(run.state_json or {})
            state["error"] = "RetryExhausted"
            state["error_message"] = reason
            run.state_json = state
            conversation = _chat_session_for_run(session, run.run_id, run.tenant_id)
            if conversation is not None:
                _upsert_assistant_message(session, conversation, run.run_id, "超过重试上限", now)
                conversation.updated_at = now
            self._append_event(
                session,
                run.run_id,
                run.tenant_id,
                EventType.WORK_DEAD,
                "recovery",
                reason,
            )
        item.status = "DEAD"
        item.worker_id = None
        item.lease_token = None
        item.lease_expires_at = None
        item.last_heartbeat_at = None

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def _append_event(
        self,
        session: Session,
        run_id: str,
        tenant_id: str,
        event_type: EventType,
        stage: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        sequence = session.scalar(
            select(func.coalesce(func.max(EventRow.sequence), 0) + 1).where(
                EventRow.run_id == run_id
            )
        )
        session.add(
            EventRow(
                event_id=_new_id("evt"),
                run_id=run_id,
                tenant_id=tenant_id,
                sequence=int(sequence or 1),
                event_type=event_type,
                stage=stage,
                message=message,
                data_json=data or {},
                created_at=datetime.now(UTC),
            )
        )


def _get_or_create_chat_session(
    session: Session,
    session_id: str,
    tenant_id: str,
    user_id: str,
    title: str,
    now: datetime,
) -> ChatSessionRow:
    row = session.scalar(
        select(ChatSessionRow).where(
            ChatSessionRow.session_id == session_id,
            ChatSessionRow.tenant_id == tenant_id,
            ChatSessionRow.user_id == user_id,
        )
    )
    if row is not None:
        return row
    if session.scalar(
        select(ChatSessionRow.session_id).where(ChatSessionRow.session_id == session_id)
    ) is not None:
        raise TenantNotFound("session not found")
    row = ChatSessionRow(
        session_id=session_id,
        tenant_id=tenant_id,
        user_id=user_id,
        title=_session_title(title),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    return row


def _get_chat_session(
    session: Session, session_id: str, tenant_id: str, user_id: str
) -> ChatSessionRow:
    row = session.scalar(
        select(ChatSessionRow).where(
            ChatSessionRow.session_id == session_id,
            ChatSessionRow.tenant_id == tenant_id,
            ChatSessionRow.user_id == user_id,
        )
    )
    if row is None:
        raise TenantNotFound("session not found")
    return row


def _chat_session_for_run(session: Session, run_id: str, tenant_id: str) -> ChatSessionRow | None:
    return session.scalar(
        select(ChatSessionRow)
        .join(ChatMessageRow, ChatMessageRow.session_id == ChatSessionRow.session_id)
        .where(
            ChatMessageRow.run_id == run_id,
            ChatMessageRow.tenant_id == tenant_id,
            ChatMessageRow.role == SessionMessageRole.USER,
        )
        .limit(1)
    )


def _session_id_for_run(session: Session, row: RunRow) -> str | None:
    return session.scalar(
        select(ChatMessageRow.session_id)
        .where(
            ChatMessageRow.run_id == row.run_id,
            ChatMessageRow.tenant_id == row.tenant_id,
            ChatMessageRow.user_id == row.user_id,
            ChatMessageRow.role == SessionMessageRole.USER,
        )
        .order_by(ChatMessageRow.sequence)
        .limit(1)
    )


def _touch_chat_session(session: Session, run_id: str, tenant_id: str, updated_at: datetime) -> None:
    conversation = _chat_session_for_run(session, run_id, tenant_id)
    if conversation is not None:
        conversation.updated_at = updated_at


def _chat_message(
    session: Session,
    conversation: ChatSessionRow,
    run_id: str,
    role: SessionMessageRole,
    content: str,
    created_at: datetime,
) -> ChatMessageRow:
    return ChatMessageRow(
        message_id=_new_id("message"),
        session_id=conversation.session_id,
        tenant_id=conversation.tenant_id,
        user_id=conversation.user_id,
        run_id=run_id,
        role=role.value,
        sequence=_next_chat_sequence(session, conversation.session_id),
        content=content,
        created_at=created_at,
    )


def _next_chat_sequence(session: Session, session_id: str) -> int:
    value = session.scalar(
        select(func.coalesce(func.max(ChatMessageRow.sequence), 0)).where(
            ChatMessageRow.session_id == session_id
        )
    )
    return int(value or 0) + 1


def _upsert_assistant_message(
    session: Session,
    conversation: ChatSessionRow,
    run_id: str,
    content: str,
    created_at: datetime,
) -> None:
    row = session.scalar(
        select(ChatMessageRow).where(
            ChatMessageRow.session_id == conversation.session_id,
            ChatMessageRow.run_id == run_id,
            ChatMessageRow.role == SessionMessageRole.ASSISTANT,
        )
    )
    if row is not None:
        row.content = content
        return
    session.add(
        _chat_message(
            session,
            conversation,
            run_id,
            SessionMessageRole.ASSISTANT,
            content,
            created_at,
        )
    )


def _session_summary(session: Session, row: ChatSessionRow) -> SessionSummary:
    latest_run_id = session.scalar(
        select(ChatMessageRow.run_id)
        .where(
            ChatMessageRow.session_id == row.session_id,
            ChatMessageRow.role == SessionMessageRole.USER,
        )
        .order_by(ChatMessageRow.sequence.desc())
        .limit(1)
    )
    return SessionSummary(
        session_id=row.session_id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        latest_run_id=latest_run_id,
    )


def _session_view(session: Session, row: ChatSessionRow) -> SessionView:
    messages = session.scalars(
        select(ChatMessageRow)
        .where(
            ChatMessageRow.session_id == row.session_id,
            ChatMessageRow.tenant_id == row.tenant_id,
            ChatMessageRow.user_id == row.user_id,
        )
        .order_by(ChatMessageRow.sequence)
    ).all()
    return SessionView(
        **_session_summary(session, row).model_dump(),
        messages=[
            SessionMessage(
                message_id=item.message_id,
                role=SessionMessageRole(item.role),
                content=item.content,
                created_at=item.created_at,
                run_id=item.run_id,
            )
            for item in messages
        ],
    )


def _conversation_message(request: RunCreate) -> str:
    return (request.message or request.input).strip()


def _session_title(value: str) -> str:
    return " ".join(value.split())[:80] or "新建聊天"


def _status_message(status: RunStatus, row: RunRow | None) -> str:
    if status == RunStatus.WAITING_INPUT:
        missing = (
            row.state_json.get("missing_information", [])
            if row is not None and isinstance(row.state_json, dict)
            else []
        )
        suffix = f"：{'；'.join(missing)}" if missing else "。"
        return f"Agent 需要更多信息{suffix}"
    if status == RunStatus.RUNNING:
        return "正在执行…"
    if status == RunStatus.PENDING:
        return "任务已提交，等待执行…"
    if status == RunStatus.CANCELLED:
        return "任务已取消。"
    return f"任务状态：{status}"


def _get_run(session: Session, run_id: str, tenant_id: str) -> RunRow:
    row = session.scalar(
        select(RunRow).where(RunRow.run_id == run_id, RunRow.tenant_id == tenant_id)
    )
    if row is None:
        raise TenantNotFound("run not found")
    return row


def _get_run_unscoped(session: Session, run_id: str) -> RunRow:
    row = session.scalar(select(RunRow).where(RunRow.run_id == run_id))
    if row is None:
        raise TenantNotFound("run not found")
    return row


def _lock_run(session: Session, run_id: str, tenant_id: str) -> RunRow:
    row = session.scalar(
        select(RunRow)
        .where(RunRow.run_id == run_id, RunRow.tenant_id == tenant_id)
        .with_for_update()
    )
    if row is None:
        raise TenantNotFound("run not found")
    return row


def _run_id_for_idempotency_key(session: Session, tenant_id: str, key: str) -> str | None:
    return session.scalar(
        select(RunRow.run_id).where(RunRow.tenant_id == tenant_id, RunRow.idempotency_key == key)
    )


def _begin_write(session: Session) -> None:
    # SQLite drops FOR UPDATE, so take the write lock before the claim or commit.
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _claimable(now: datetime, max_claim_attempts: int):
    return and_(
        WorkItemRow.claim_attempts < max_claim_attempts,
        or_(
            and_(WorkItemRow.status == "QUEUED", WorkItemRow.available_at <= now),
            and_(WorkItemRow.status == "RUNNING", WorkItemRow.lease_expires_at < now),
        ),
    )


def _lock_work(session: Session, run_id: str) -> WorkItemRow | None:
    return session.scalar(
        select(WorkItemRow).where(WorkItemRow.run_id == run_id).with_for_update()
    )


def _holds_lease(item: WorkItemRow | None, worker_id: str, lease_token: str | None) -> bool:
    return (
        item is not None
        and item.status == "RUNNING"
        and item.worker_id == worker_id
        and lease_token is not None
        and item.lease_token == lease_token
        and not lease_expired(item.lease_expires_at)
    )


def _token_matches(item: WorkItemRow | None, worker_id: str, lease_token: str) -> bool:
    """Match the owner even after the lease clock has expired."""
    return (
        item is not None
        and item.status == "RUNNING"
        and item.worker_id == worker_id
        and item.lease_token == lease_token
    )


def _return_to_queue(item: WorkItemRow, available_at: datetime) -> None:
    item.status = "QUEUED"
    item.worker_id = None
    item.lease_token = None
    item.lease_expires_at = None
    item.last_heartbeat_at = None
    item.available_at = available_at


def _add_artifact(session: Session, artifact: ArtifactRef) -> None:
    stored = session.scalar(
        select(ArtifactRow.artifact_id).where(ArtifactRow.artifact_id == artifact.artifact_id)
    )
    if stored is not None:
        return
    session.add(
        ArtifactRow(
            artifact_id=artifact.artifact_id,
            run_id=artifact.run_id,
            tenant_id=artifact.tenant_id,
            name=artifact.name,
            media_type=artifact.media_type,
            size_bytes=artifact.size_bytes,
            sha256=artifact.sha256,
            path=artifact.path,
            metadata_json=artifact.metadata,
            created_at=artifact.created_at,
        )
    )


def _delegation_row(
    tenant_id: str, run_id: str, record: DelegationRecord, created_at: datetime
) -> DelegationRow:
    return DelegationRow(
        delegation_id=record.delegation_id,
        run_id=run_id,
        tenant_id=tenant_id,
        agent_id=record.agent_id,
        parent_agent_id=record.parent_agent_id,
        role=record.role,
        depth=record.depth,
        status=record.status,
        input_summary=record.input_summary,
        result_summary=record.result_summary,
        metadata_json=record.metadata,
        created_at=created_at,
    )


def _update_work(
    session: Session,
    run_id: str,
    values: dict,
    *,
    worker_id: str | None = None,
    status: str | None = None,
    lease_token: str | None = None,
    unexpired_after: datetime | None = None,
) -> int:
    stmt = update(WorkItemRow).where(WorkItemRow.run_id == run_id)
    if worker_id is not None:
        stmt = stmt.where(WorkItemRow.worker_id == worker_id)
    if status is not None:
        stmt = stmt.where(WorkItemRow.status == status)
    if lease_token is not None:
        stmt = stmt.where(WorkItemRow.lease_token == lease_token)
    if unexpired_after is not None:
        stmt = stmt.where(WorkItemRow.lease_expires_at > unexpired_after)
    # Bulk queue updates do not need the identity map refreshed.
    result = session.connection().execute(stmt.values(**values))
    return result.rowcount or 0


def _merge_run_state(row: RunRow, state: dict | None) -> None:
    if state:
        row.state_json = {**row.state_json, **state}


def _run_row(
    run_id: str, tenant_id: str, user_id: str, request: RunCreate, now: datetime
) -> RunRow:
    return RunRow(
        run_id=run_id,
        tenant_id=tenant_id,
        user_id=user_id,
        input_text=request.input,
        status=RunStatus.PENDING,
        route=None,
        summary=None,
        state_json={
            "input": request.input,
            "inputs": request.inputs,
            "constraints": request.constraints,
            "requested_role": request.requested_role,
            "model_profile": request.model_profile,
            "reasoning_effort": request.reasoning_effort,
        },
        budget_json={},
        cancel_requested=False,
        idempotency_key=request.idempotency_key,
        created_at=now,
        updated_at=now,
    )


def _work_row(run_id: str, now: datetime) -> WorkItemRow:
    return WorkItemRow(
        work_id=_new_id("work"),
        run_id=run_id,
        status="QUEUED",
        available_at=now,
        lease_expires_at=None,
        worker_id=None,
        attempts=0,
        lease_token=None,
        lease_generation=0,
        last_heartbeat_at=None,
        claim_attempts=0,
        execution_attempts=0,
        lease_loss_count=0,
    )


def _run_view(row: RunRow, session_id: str | None = None) -> RunView:
    return RunView(
        run_id=row.run_id,
        session_id=session_id,
        tenant_id=row.tenant_id,
        status=RunStatus(row.status),
        input=row.input_text,
        model_profile=row.state_json.get("model_profile"),
        reasoning_effort=row.state_json.get("reasoning_effort"),
        route=row.route,
        summary=row.summary,
        missing_information=row.state_json.get("missing_information", []),
        created_at=row.created_at,
        updated_at=row.updated_at,
        budget=row.budget_json,
    )


def _event_view(row: EventRow) -> RunEvent:
    return RunEvent(
        sequence=row.sequence,
        event_type=EventType(row.event_type),
        stage=row.stage,
        message=row.message,
        created_at=row.created_at,
        data=row.data_json,
    )


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:20]}"
