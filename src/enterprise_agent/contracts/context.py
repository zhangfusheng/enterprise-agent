from typing import Any

from pydantic import BaseModel, Field


class IdentityContext(BaseModel):
    tenant_id: str
    user_id: str
    auth_source: str = "development"
    scopes: set[str] = Field(default_factory=set)
    is_authenticated: bool = False


class ExecutionContext(BaseModel):
    run_id: str
    tenant_id: str
    user_id: str
    agent_id: str
    parent_agent_id: str | None = None
    delegation_id: str | None = None
    depth: int = 0
    allowed_roles: set[str] = Field(default_factory=set)
    allowed_tools: set[str] = Field(default_factory=set)
    workspace_root: str
    budget: dict[str, int] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

