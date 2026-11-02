from fastapi import APIRouter, Depends, HTTPException, Query, status

from apps.api.dependencies import container, identity
from enterprise_agent.application.cancel_run import cancel_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate, RunStatus
from enterprise_agent.infrastructure.repositories.runs import TenantNotFound

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_run(request: RunCreate, app=Depends(container), user: IdentityContext = Depends(identity)):
    message = (request.message or request.input).strip()
    if not message:
        raise HTTPException(status_code=422, detail="input must not be blank")
    if request.model_profile:
        try:
            profile = app.models.profile(request.model_profile)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail="unknown model profile") from exc
        provider = next(
            item for item in app.model_config.providers if item.name == profile.provider
        )
        if app.settings.offline_mode and provider.protocol != "fake":
            raise HTTPException(
                status_code=409,
                detail="当前为离线模式，只能选择离线模型。",
            )
    selected_profile = request.model_profile
    if selected_profile is None and app.settings.offline_mode:
        selected_profile = app.models.offline_profile_name()
    if selected_profile is None:
        try:
            selected_profile = app.models.default_profile_for_role("router")
        except KeyError:
            selected_profile = None
    if request.reasoning_effort and selected_profile:
        supported = app.models.supported_reasoning_efforts(selected_profile)
        if request.reasoning_effort not in supported:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"reasoning effort {request.reasoning_effort!r} is not supported "
                    f"by model profile {selected_profile!r}"
                ),
            )
    request = request.model_copy(update={"model_profile": selected_profile})
    if request.session_id:
        try:
            context = app.runs.conversation_context(
                user.tenant_id, user.user_id, request.session_id
            )
        except TenantNotFound as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        enriched = f"{context}\n\n用户的新请求：{message}" if context else message
        request = request.model_copy(update={"input": enriched, "message": message})
    else:
        request = request.model_copy(update={"input": message, "message": message})
    try:
        run_id = submit_run(
            app.runs,
            user,
            request,
            app.config_snapshot_for(
                request.model_profile, request.reasoning_effort
            ),
        )
        session_id = app.runs.session_id_for_run(user.tenant_id, user.user_id, run_id)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="session not found") from exc
    return {
        "run_id": run_id,
        "session_id": session_id,
        "status": "PENDING",
        "model_profile": selected_profile,
        "reasoning_effort": request.reasoning_effort,
    }


@router.get("")
def list_runs(
    q: str | None = Query(default=None, max_length=256),
    run_status: RunStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=100),
    app=Depends(container),
    user: IdentityContext = Depends(identity),
):
    return {
        "items": [
            item.model_dump(mode="json")
            for item in app.runs.list_run_views(
                user.tenant_id, query=q, status=run_status, limit=limit
            )
        ]
    }


@router.get("/{run_id}")
def get_run(run_id: str, app=Depends(container), user: IdentityContext = Depends(identity)):
    try:
        return app.runs.get_run_view(user.tenant_id, run_id)
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc


@router.post("/{run_id}/input")
def add_input(run_id: str, body: dict[str, str], app=Depends(container), user: IdentityContext = Depends(identity)):
    try:
        value = body.get("input", "")
        if not isinstance(value, str):
            raise HTTPException(status_code=422, detail="input must be a string")
        app.runs.append_input(user.tenant_id, run_id, value)
        return {"run_id": run_id, "status": "PENDING", "message": "补充信息已保存，等待 resume"}
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/resume")
def resume(run_id: str, app=Depends(container), user: IdentityContext = Depends(identity)):
    try:
        app.runs.requeue_work(run_id, tenant_id=user.tenant_id)
        return {"run_id": run_id, "status": "PENDING"}
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/cancel")
def cancel(run_id: str, app=Depends(container), user: IdentityContext = Depends(identity)):
    try:
        cancel_run(app.runs, user.tenant_id, run_id)
        return {"run_id": run_id, "status": "CANCEL_REQUESTED"}
    except TenantNotFound as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc

