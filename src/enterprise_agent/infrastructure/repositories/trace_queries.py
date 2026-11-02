"""Bounded, tenant-scoped trace discovery. Payloads are never loaded for children."""

import base64
import binascii
import json
from datetime import datetime
from typing import cast

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, aliased

from enterprise_agent.contracts.runs import RunStatus
from enterprise_agent.contracts.traces import (
    RunType,
    TraceListResponse,
    TraceRunView,
    TraceStatus,
    TraceSummary,
    utc,
)
from enterprise_agent.infrastructure.database.models import RunRow, TraceRunRow
from enterprise_agent.infrastructure.observability.logging import redact


def list_traces(
    session: Session,
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
    root = TraceRunRow
    counts = (
        select(
            root.trace_id,
            root.run_id,
            func.count().label("span_count"),
            func.sum(case((and_(root.error.is_not(None), root.error != ""), 1), else_=0)).label(
                "error_count"
            ),
        )
        .where(root.tenant_id == tenant_id)
        .group_by(root.trace_id, root.run_id)
        .subquery()
    )
    state = case(
        (and_(root.error.is_not(None), root.error != ""), "error"),
        (root.outputs_json["interrupted"].as_boolean().is_(True), "interrupted"),
        (and_(root.end_time.is_(None), RunRow.status.not_in(["PENDING", "RUNNING"])), "incomplete"),
        (root.end_time.is_(None), "running"),
        else_="success",
    )
    stmt = (
        select(root, RunRow.status, RunRow.route, counts.c.span_count, counts.c.error_count)
        .join(RunRow, and_(RunRow.run_id == root.run_id, RunRow.tenant_id == root.tenant_id))
        .join(counts, and_(counts.c.trace_id == root.trace_id, counts.c.run_id == root.run_id))
        .where(root.tenant_id == tenant_id, root.parent_run_id.is_(None))
    )
    if q:
        # Escape LIKE metacharacters so ids and names are searched literally.
        pattern = f"%{_escape(q)}%"
        stmt = stmt.where(
            or_(
                root.name.ilike(pattern, escape="\\"),
                root.run_id.ilike(pattern, escape="\\"),
                root.trace_id.ilike(pattern, escape="\\"),
            )
        )
    if name:
        stmt = stmt.where(root.name.ilike(f"%{_escape(name)}%", escape="\\"))
    if tag:
        # JSON arrays need exact element comparison, not substring matching.
        dialect = session.get_bind().dialect.name
        elements = (
            (
                func.json_array_elements_text(root.tags_json)
                if dialect == "postgresql"
                else func.json_each(root.tags_json)
            )
            .table_valued("value")
            .alias("trace_tags")
        )
        stmt = stmt.where(select(1).select_from(elements).where(elements.c.value == tag).exists())
    if status:
        stmt = stmt.where(state == status)
    if has_error is not None:
        stmt = stmt.where(counts.c.error_count > 0 if has_error else counts.c.error_count == 0)
    if start_time:
        stmt = stmt.where(root.start_time >= utc(start_time))
    if end_time:
        stmt = stmt.where(root.start_time <= utc(end_time))
    if start_time and end_time and utc(start_time) > utc(end_time):
        raise ValueError("start_time must not be after end_time")
    if cursor:
        stamp, span_id = _decode_cursor(cursor)
        stmt = stmt.where(
            or_(root.start_time < stamp, and_(root.start_time == stamp, root.id < span_id))
        )
    rows = cast(
        list[tuple[TraceRunRow, str, str | None, int, int]],
        session.execute(
            stmt.order_by(root.start_time.desc(), root.id.desc()).limit(limit + 1)
        ).all(),
    )
    items = [
        _summary(row, run_status, route, span_count, error_count)
        for row, run_status, route, span_count, error_count in rows[:limit]
    ]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1][0]
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([utc(last.start_time).isoformat(), last.id]).encode()
        ).decode()
    return TraceListResponse(items=items, next_cursor=next_cursor)


def trace_summary(session: Session, tenant_id: str, trace_id: str) -> TraceSummary | None:
    root = session.scalar(
        select(TraceRunRow).where(
            TraceRunRow.tenant_id == tenant_id,
            TraceRunRow.id == trace_id,
            TraceRunRow.parent_run_id.is_(None),
        )
    )
    if root is None:
        return None
    run = session.scalar(
        select(RunRow).where(RunRow.tenant_id == tenant_id, RunRow.run_id == root.run_id)
    )
    if run is None:
        return None
    child = aliased(TraceRunRow)
    count, errors = session.execute(
        select(
            func.count(),
            func.sum(case((and_(child.error.is_not(None), child.error != ""), 1), else_=0)),
        ).where(
            child.tenant_id == tenant_id, child.run_id == root.run_id, child.trace_id == trace_id
        )
    ).one()
    return _summary(root, run.status, run.route, cast(int, count), cast(int, errors) or 0)


def _summary(
    row: TraceRunRow, run_status: str, route: str | None, count: int, errors: int
) -> TraceSummary:
    span = TraceRunView(
        id=row.id,
        trace_id=row.trace_id,
        name=row.name,
        run_type=RunType(row.run_type),
        start_time=row.start_time,
        end_time=row.end_time,
        error=row.error,
        inputs=row.inputs_json,
        extra=row.extra_json,
        outputs={"interrupted": row.outputs_json.get("interrupted")},
        dotted_order=row.dotted_order,
        incomplete=run_status not in {"PENDING", "RUNNING"},
    )
    return TraceSummary(
        run_id=row.run_id,
        trace_id=row.trace_id,
        root_name=redact(row.name),
        status=span.status,
        run_status=RunStatus(run_status),
        start_time=span.start_time,
        end_time=span.end_time,
        duration_ms=span.duration_ms,
        span_count=count,
        error_count=errors,
        has_error=errors > 0,
        tags=[redact(str(tag)) for tag in row.tags_json],
        route=route,
    )


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        value = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if (
            not isinstance(value, list)
            or len(value) != 2
            or not all(isinstance(part, str) for part in value)
        ):
            raise ValueError
        stamp = utc(datetime.fromisoformat(value[0]))
        if not value[1] or len(value[1]) > 64:
            raise ValueError
        return stamp, value[1]
    except (ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise ValueError("invalid trace cursor") from exc
