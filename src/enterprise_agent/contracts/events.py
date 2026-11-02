from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    RUN_CREATED = "run.created"
    RUN_STATUS = "run.status"
    ROUTED = "run.routed"
    PLAN_CREATED = "plan.created"
    TASK_STARTED = "task.started"
    TASK_FINISHED = "task.finished"
    TOOL_CALLED = "tool.called"
    DELEGATED = "agent.delegated"
    ARTIFACT_CREATED = "artifact.created"
    INPUT_REQUESTED = "input.requested"
    RUN_CANCELLED = "run.cancelled"
    RUN_FAILED = "run.failed"
    RUN_COMPLETED = "run.completed"
    LEASE_LOST = "lease.lost"
    WORK_REQUEUED = "work.requeued"
    WORK_DEAD = "work.dead"


class RunEvent(BaseModel):
    sequence: int
    event_type: EventType
    stage: str
    message: str
    created_at: datetime
    data: dict[str, Any] = Field(default_factory=dict)

