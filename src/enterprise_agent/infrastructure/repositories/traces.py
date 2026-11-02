"""Persist a LangSmith-shaped run tree for one enterprise run."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from enterprise_agent.contracts.traces import (
    TraceListResponse,
    TraceStatus,
    TraceSummary,
    TraceView,
)
from enterprise_agent.infrastructure.database.models import TraceRunRow
from enterprise_agent.infrastructure.observability.tracing import TraceSpan, build_trace_view
from enterprise_agent.infrastructure.repositories.trace_queries import list_traces, trace_summary


class TraceRepository:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def record(self, span: TraceSpan) -> None:
        if not span.id or not span.tenant_id or not span.run_id:
            return
        with self._transaction() as session:
            session.merge(_row(span))

    def get(self, tenant_id: str, run_id: str) -> TraceView:
        with self.session_factory() as session:
            rows = session.scalars(
                select(TraceRunRow)
                .where(TraceRunRow.tenant_id == tenant_id, TraceRunRow.run_id == run_id)
                .order_by(TraceRunRow.dotted_order)
            ).all()
        return build_trace_view(run_id, [_span(row) for row in rows])

    def list(
        self,
        tenant_id: str,
        *,
        q: str | None = None,
        status: TraceStatus | None = None,
        name: str | None = None,
        tag: str | None = None,
        has_error: bool | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 30,
        cursor: str | None = None,
    ) -> TraceListResponse:
        with self.session_factory() as session:
            return list_traces(
                session,
                tenant_id,
                q=q,
                status=status,
                name=name,
                tag=tag,
                has_error=has_error,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
                cursor=cursor,
            )

    def summary(self, tenant_id: str, trace_id: str) -> TraceSummary | None:
        with self.session_factory() as session:
            return trace_summary(session, tenant_id, trace_id)

    @contextmanager
    def _transaction(self) -> Iterator[Session]:
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise


def _row(span: TraceSpan) -> TraceRunRow:
    return TraceRunRow(
        id=span.id,
        trace_id=span.trace_id,
        parent_run_id=span.parent_run_id,
        name=span.name,
        run_type=span.run_type,
        run_id=span.run_id or "",
        tenant_id=span.tenant_id or "",
        start_time=span.start_time,
        end_time=span.end_time,
        inputs_json=span.inputs,
        outputs_json=span.outputs,
        error=span.error,
        extra_json=span.extra,
        tags_json=list(span.tags),
        dotted_order=span.dotted_order,
    )


def _span(row: TraceRunRow) -> TraceSpan:
    return TraceSpan(
        id=row.id,
        trace_id=row.trace_id,
        parent_run_id=row.parent_run_id,
        name=row.name,
        run_type=row.run_type,
        start_time=row.start_time,
        end_time=row.end_time,
        inputs=dict(row.inputs_json or {}),
        outputs=dict(row.outputs_json or {}),
        error=row.error,
        extra=dict(row.extra_json or {}),
        tags=list(row.tags_json or []),
        dotted_order=row.dotted_order,
        run_id=row.run_id,
        tenant_id=row.tenant_id,
    )
