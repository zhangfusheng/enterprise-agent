from enum import StrEnum

from pydantic import BaseModel, Field


class RouteKind(StrEnum):
    DIRECT = "direct"
    SINGLE = "single"
    MULTI = "multi"


class RouteDecision(BaseModel):
    route: RouteKind
    required_capabilities: list[str] = Field(default_factory=list)
    selected_role: str | None = None
    rationale: str
    requires_external_operation: bool = False
    requires_realtime_information: bool = False

