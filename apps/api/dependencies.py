from fastapi import Header, HTTPException

from enterprise_agent.bootstrap.container import Container, get_container
from enterprise_agent.bootstrap.settings import get_settings
from enterprise_agent.contracts.context import IdentityContext


def container() -> Container:
    return get_container()


def identity(x_tenant_id: str | None = Header(default=None), x_user_id: str | None = Header(default=None)) -> IdentityContext:
    settings = get_settings()
    if settings.environment == "production":
        raise HTTPException(status_code=503, detail="production identity adapter is not configured")
    return IdentityContext(tenant_id=x_tenant_id or "local-tenant", user_id=x_user_id or "local-user", auth_source="development-header", is_authenticated=settings.dev_identity)

