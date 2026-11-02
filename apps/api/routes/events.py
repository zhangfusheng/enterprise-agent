import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from apps.api.dependencies import container, identity
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunStatus
from enterprise_agent.infrastructure.repositories.runs import TenantNotFound

router = APIRouter(prefix="/api/v1/runs", tags=["events"])


@router.get("/{run_id}/events")
def events(run_id: str, after: int = Query(default=0, ge=0), app=Depends(container), user: IdentityContext = Depends(identity)):
    try:
        app.runs.get_run_view(user.tenant_id, run_id)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc

    def stream():
        cursor = after
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            batch = app.runs.events(user.tenant_id, run_id, cursor)
            for event in batch:
                cursor = event.sequence
                yield f"id: {event.sequence}\nevent: {event.event_type}\ndata: {json.dumps(event.model_dump(mode='json'), ensure_ascii=False)}\n\n"
            view = app.runs.get_run_view(user.tenant_id, run_id)
            if view.status in {RunStatus.SUCCEEDED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED} and not batch:
                break
            time.sleep(0.25)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

