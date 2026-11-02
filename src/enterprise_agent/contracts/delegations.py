from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class DelegationStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DENIED = "DENIED"


class DelegationRecord(BaseModel):
    delegation_id: str
    agent_id: str
    parent_agent_id: str
    role: str
    depth: int = Field(ge=1)
    status: DelegationStatus
    input_summary: str = ""
    result_summary: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

