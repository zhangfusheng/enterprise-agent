from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

InputRequirementKind = Literal[
    "material",
    "comparison_subject",
    "calculation_operand",
    "time_range",
    "template",
    "destination",
    "clarification",
]


class Goal(BaseModel):
    id: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    description: str = Field(min_length=3)
    required_capabilities: list[str] = Field(default_factory=list)


class InputMaterial(BaseModel):
    name: str
    content: str = ""
    uri: str | None = None


class InputRequirement(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    kind: InputRequirementKind
    description: str = Field(min_length=1)
    goal_ids: list[str] = Field(min_length=1)
    required: bool = True
    minimum_items: int = Field(default=1, ge=1)
    status: Literal["missing", "satisfied"]
    satisfied_by: list[str] = Field(default_factory=list)
    source: Literal["rules", "model", "server", "legacy"]

    @field_validator("goal_ids", "satisfied_by")
    @classmethod
    def unique_references(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("input requirement references must be unique")
        return value

    @model_validator(mode="after")
    def status_matches_satisfaction(self) -> "InputRequirement":
        if self.status == "missing" and self.satisfied_by:
            raise ValueError("missing input requirement cannot have satisfied_by references")
        if self.status == "satisfied" and len(self.satisfied_by) < self.minimum_items:
            raise ValueError("satisfied input requirement does not meet minimum_items")
        return self


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
    delivery_mode: Literal["inline", "artifact"] = "inline"
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
    input_requirements: list[InputRequirement] = Field(default_factory=list)
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

