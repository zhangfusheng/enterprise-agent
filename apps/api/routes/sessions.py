from fastapi import APIRouter, Depends, HTTPException, Query

from apps.api.dependencies import container, identity
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.infrastructure.repositories.runs import TenantNotFound

router = APIRouter(prefix="/api/v1/sessions", tags=["sessions"])


@router.get("")
def list_sessions(
    q: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=100, ge=1, le=100),
    app=Depends(container),
    user: IdentityContext = Depends(identity),
):
    return {
        "items": [
            item.model_dump(mode="json")
            for item in app.runs.list_session_views(
                user.tenant_id, user.user_id, query=q, limit=limit
            )
        ]
    }


@router.get("/{session_id}")
def get_session(
    session_id: str,
    app=Depends(container),
    user: IdentityContext = Depends(identity),
):
    try:
        return app.runs.get_session_view(user.tenant_id, user.user_id, session_id).model_dump(
            mode="json"
        )
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
