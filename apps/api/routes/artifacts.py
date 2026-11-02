from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from apps.api.dependencies import container, identity
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.infrastructure.repositories.runs import TenantNotFound

router = APIRouter(prefix="/api/v1/runs", tags=["artifacts"])


@router.get("/{run_id}/artifacts")
def artifacts(run_id: str, app=Depends(container), user: IdentityContext = Depends(identity)):
    try:
        app.runs.get_run_view(user.tenant_id, run_id)
        return {"artifacts": [item.model_dump(mode="json") for item in app.artifacts.list(user.tenant_id, run_id)]}
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.get("/{run_id}/artifacts/{artifact_id}/download")
def download(run_id: str, artifact_id: str, app=Depends(container), user: IdentityContext = Depends(identity)):
    items = app.artifacts.list(user.tenant_id, run_id)
    selected = next((item for item in items if item.artifact_id == artifact_id), None)
    if not selected:
        raise HTTPException(status_code=404, detail="artifact not found")
    root = app.settings.artifact_root.resolve()
    path = Path(selected.path).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="artifact unavailable")
    return FileResponse(path, media_type=selected.media_type, filename=selected.name)

