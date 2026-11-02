"""Durable tool-operation records.

The in-memory gateway cache does not survive a process restart. This table is
the source of truth for whether a side-effecting call may run again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from enterprise_agent.infrastructure.database.models import OperationRow
from enterprise_agent.tools.contracts import ToolResult, ToolStatus

_STARTED = "STARTED"


class OperationRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def lookup(
        self,
        operation_id: str,
        *,
        has_side_effect: bool,
        stale_after: timedelta,
    ) -> ToolResult | None:
        """Return a terminal result, or None when the caller may execute."""
        with self.session_factory() as session:
            row = session.get(OperationRow, operation_id)
            if row is None:
                return None
            if row.status == _STARTED:
                return self._started(session, row, has_side_effect, stale_after)
            session.expunge(row)
            return _result(row)

    def try_start(
        self,
        *,
        operation_id: str,
        tenant_id: str,
        run_id: str,
        task_id: str | None,
        tool_name: str,
        arguments_hash: str,
        has_side_effect: bool,
    ) -> bool:
        """Insert a STARTED row. False means another worker already owns the key."""
        with self.session_factory() as session:
            session.add(
                OperationRow(
                    operation_id=operation_id,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    task_id=task_id,
                    tool_name=tool_name,
                    arguments_hash=arguments_hash,
                    has_side_effect=has_side_effect,
                    status=_STARTED,
                    result_json={},
                    created_at=datetime.now(UTC),
                )
            )
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                return False
            return True

    def complete(self, operation_id: str, result: ToolResult) -> None:
        with self.session_factory() as session:
            row = session.get(OperationRow, operation_id)
            if row is None:
                return
            if row.status not in {_STARTED, result.status.value}:
                return
            row.status = result.status.value
            row.result_json = result.model_dump(mode="json")
            session.commit()

    def _started(
        self,
        session: Session,
        row: OperationRow,
        has_side_effect: bool,
        stale_after: timedelta,
    ) -> ToolResult | None:
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        fresh = datetime.now(UTC) - created < stale_after
        if fresh and not has_side_effect:
            return None
        if not has_side_effect and not fresh:
            return None
        if fresh:
            return ToolResult(
                status=ToolStatus.UNKNOWN,
                error="operation in progress",
                operation_id=row.operation_id,
            )
        unknown = ToolResult(
            status=ToolStatus.UNKNOWN,
            error="operation outcome unknown after interruption",
            operation_id=row.operation_id,
        )
        row.status = ToolStatus.UNKNOWN.value
        row.result_json = unknown.model_dump(mode="json")
        session.commit()
        return unknown


def _result(row: OperationRow) -> ToolResult:
    payload = dict(row.result_json or {})
    payload.setdefault("status", row.status)
    payload.setdefault("operation_id", row.operation_id)
    return ToolResult.model_validate(payload)
