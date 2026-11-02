from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from apps.api.dependencies import container, identity
from enterprise_agent.application.query_trace import query_trace
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.traces import (
    TraceListResponse,
    TraceStatus,
    TraceSummary,
    TraceView,
)
from enterprise_agent.infrastructure.repositories.runs import TenantNotFound

router = APIRouter(prefix="/api/v1", tags=["traces"])


@router.get("/runs/{run_id}/trace", response_model=TraceView)
def get_trace(
    run_id: str,
    app=Depends(container),
    user: IdentityContext = Depends(identity),
):
    try:
        return query_trace(app.runs, app.traces, user.tenant_id, run_id)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.get("/traces", response_model=TraceListResponse)
def get_traces(
    q: str | None = Query(None, max_length=256),
    status: TraceStatus | None = None,
    name: str | None = Query(None, max_length=256),
    tag: str | None = Query(None, max_length=256),
    has_error: bool | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = Query(None, max_length=1024),
    app=Depends(container),
    user: IdentityContext = Depends(identity),
):
    try:
        return app.traces.list(
            user.tenant_id,
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
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/traces/{trace_id}/summary", response_model=TraceSummary)
def get_summary(trace_id: str, app=Depends(container), user: IdentityContext = Depends(identity)):
    summary = app.traces.summary(user.tenant_id, trace_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return summary


@router.get("/runs/{run_id}/trace/export")
def export_trace(run_id: str, app=Depends(container), user: IdentityContext = Depends(identity)):
    try:
        view = query_trace(app.runs, app.traces, user.tenant_id, run_id)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    return Response(
        view.model_dump_json(indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="trace.json"'},
    )
