from fastapi import APIRouter, Depends

from apps.api.dependencies import container, identity
from enterprise_agent.contracts.context import IdentityContext

router = APIRouter(prefix="/api/v1/models", tags=["models"])


@router.get("")
def list_models(app=Depends(container), _user: IdentityContext = Depends(identity)):
    """Expose the configured, non-secret model metadata for the chat picker."""
    return {
        "items": app.models.list_options(offline_mode=app.settings.offline_mode),
        "offline_mode": app.settings.offline_mode,
    }
