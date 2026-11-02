from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RunStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_INPUT = "WAITING_INPUT"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


ReasoningEffort = Literal["minimal", "low", "medium", "high", "xhigh", "max"]


class RunCreate(BaseModel):
    input: str = Field(min_length=1)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    requested_role: str | None = None
    model_profile: str | None = Field(default=None, min_length=1, max_length=128)
    reasoning_effort: ReasoningEffort | None = None
    idempotency_key: str | None = None
    session_id: str | None = None
    # The user-visible turn can differ from the context-enriched input sent to
    # the agent when a run continues an existing conversation.
    message: str | None = None


class RunView(BaseModel):
    run_id: str
    session_id: str | None = None
    tenant_id: str
    status: RunStatus
    input: str
    model_profile: str | None = None
    reasoning_effort: ReasoningEffort | None = None
    route: str | None = None
    summary: str | None = None
    missing_information: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    budget: dict[str, Any] = Field(default_factory=dict)


class RunState(BaseModel):
    run_id: str
    tenant_id: str
    status: RunStatus
    route: str | None = None
    goal_spec: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] | None = None
    results: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    missing_information: list[str] = Field(default_factory=list)
    plan_history: list[dict[str, Any]] = Field(default_factory=list)

