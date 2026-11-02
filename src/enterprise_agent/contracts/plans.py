from enum import StrEnum

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TaskSpec(BaseModel):
    task_id: str = Field(pattern=r"^task_[a-z0-9_-]+$")
    goal_ids: list[str] = Field(min_length=1)
    agent_role: str
    task_description: str = Field(min_length=3)
    inputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result_ref: str | None = None
    attempts: int = Field(default=0, ge=0)
    # ``task_id`` identifies one concrete execution record. ``task_key`` is
    # the stable logical identity used when a later plan revision changes the
    # execution id.
    task_key: str | None = Field(default=None, min_length=1, max_length=128)


class GlobalPlan(BaseModel):
    plan_version: int = Field(ge=1)
    task_id: str
    tasks: list[TaskSpec] = Field(min_length=1)
    created_from_route: str


class PlanHistoryEntry(BaseModel):
    version: int
    reason: str
    plan: GlobalPlan
    task_mapping: dict[str, str] = Field(default_factory=dict)
    task_actions: dict[str, str] = Field(default_factory=dict)
    retired_task_ids: list[str] = Field(default_factory=list)

