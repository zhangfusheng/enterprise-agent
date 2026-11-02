from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Goal(BaseModel):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    description: str = Field(min_length=3)
    required_capabilities: list[str] = Field(default_factory=list)


class InputMaterial(BaseModel):
    name: str
    content: str = ""
    uri: str | None = None


class ConstraintSet(BaseModel):
    values: list[str] = Field(default_factory=list)
    max_runtime_seconds: int | None = Field(default=None, ge=1)
    require_sources: bool = False
    allow_external_network: bool = False
    allow_delegation: bool = True
    request_delegation: bool = False


class Deliverable(BaseModel):
    id: str
    description: str
    format: Literal["text", "markdown", "json"] = "text"
    path: str | None = None


class AcceptanceCriterion(BaseModel):
    id: str
    description: str
    required: bool = True


class GoalSpec(BaseModel):
    goals: list[Goal] = Field(default_factory=list)
    inputs: list[InputMaterial] = Field(default_factory=list)
    constraints: ConstraintSet = Field(default_factory=ConstraintSet)
    deliverables: list[Deliverable] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)

    @field_validator("goals")
    @classmethod
    def unique_goal_ids(cls, value: list[Goal]) -> list[Goal]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("goal ids must be unique")
        return value

    @property
    def is_waiting_input(self) -> bool:
        return bool(self.missing_information)

