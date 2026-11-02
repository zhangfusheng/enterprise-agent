from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ResultStatus(StrEnum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class AgentResult(BaseModel):
    status: ResultStatus
    summary: str = ""
    structured_data: dict[str, Any] = Field(default_factory=dict)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    unmet_requirements: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    agent_id: str | None = None
    parent_agent_id: str | None = None

