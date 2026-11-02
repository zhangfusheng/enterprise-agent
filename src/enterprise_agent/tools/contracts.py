from collections.abc import Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ToolStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ToolSpec(BaseModel):
    name: str
    description: str
    required_scopes: set[str] = Field(default_factory=set)
    timeout_seconds: float = Field(default=10, gt=0)
    max_retries: int = Field(default=0, ge=0)
    max_result_bytes: int = Field(default=200_000, ge=1)
    idempotent: bool = True
    has_side_effect: bool = False


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    operation_id: str | None = None


class ToolResult(BaseModel):
    status: ToolStatus
    value: Any = None
    error: str | None = None
    operation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


ToolCallable = Callable[..., Any]

