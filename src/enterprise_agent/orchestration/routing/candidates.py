"""Typed, untrusted intent candidates proposed by the router model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    WithJsonSchema,
    create_model,
    field_validator,
    model_validator,
)

from enterprise_agent.contracts.goals import InputRequirementKind

CANDIDATE_SCHEMA_VERSION = "goal-candidate-v2"


def _as_list(value: object) -> object:
    """Normalize harmless provider object-vs-array drift."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        return [value]
    return value


class GoalCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    description: str = Field(min_length=3)
    required_capabilities: list[str] = Field(default_factory=list)


class DeliverableCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    format: Literal["text", "markdown", "json"] = "text"
    delivery_mode: Literal["inline", "artifact"] = "inline"


class CriterionCandidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class InputRequirementCandidate(BaseModel):
    """A model-proposed dependency without server-owned resolution state."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    kind: InputRequirementKind
    description: str = Field(min_length=1)
    goal_ids: list[str] = Field(min_length=1)
    required: bool = True
    minimum_items: int = Field(default=1, ge=1)

    @model_validator(mode="before")
    @classmethod
    def reject_server_owned_fields(cls, value: object) -> object:
        if isinstance(value, Mapping):
            forbidden = {"status", "satisfied_by", "source"} & set(value)
            if forbidden:
                raise ValueError("input requirement candidate contains server-owned fields")
        return value

    @field_validator("goal_ids")
    @classmethod
    def unique_goal_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("input requirement candidate goal_ids must be unique")
        return value


class GoalIntentCandidate(BaseModel):
    """Stable business shape; caller-owned and server-owned fields are absent."""

    model_config = ConfigDict(extra="ignore")

    goals: list[GoalCandidate] = Field(min_length=1)
    deliverables: Annotated[list[DeliverableCandidate], BeforeValidator(_as_list)] = Field(
        default_factory=list
    )
    acceptance_criteria: list[CriterionCandidate] = Field(default_factory=list)
    input_requirements: list[InputRequirementCandidate] = Field(default_factory=list)

    @field_validator("goals")
    @classmethod
    def unique_goal_ids(cls, value: list[GoalCandidate]) -> list[GoalCandidate]:
        ids = [item.id for item in value]
        if len(ids) != len(set(ids)):
            raise ValueError("goal candidate ids must be unique")
        return value

    @model_validator(mode="after")
    def requirements_reference_candidate_goals(self) -> GoalIntentCandidate:
        requirement_ids = [item.id for item in self.input_requirements]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("input requirement candidate ids must be unique")
        goal_ids = {item.id for item in self.goals}
        for requirement in self.input_requirements:
            unknown = set(requirement.goal_ids) - goal_ids
            if unknown:
                raise ValueError(
                    "input requirement candidate references unknown goals: "
                    f"{sorted(unknown)}"
                )
        return self


def candidate_schema(capabilities: tuple[str, ...]) -> type[GoalIntentCandidate]:
    """Create a provider schema whose only dynamic contract is capability enum."""

    goal = create_model(
        "ProviderGoalCandidate",
        __base__=GoalCandidate,
        required_capabilities=(_capability_list(capabilities), Field(default_factory=list)),
    )
    schema = create_model(
        "ProviderGoalIntentCandidate",
        __base__=GoalIntentCandidate,
        goals=(list[goal], Field(min_length=1)),  # type: ignore[valid-type]
    )
    return cast(type[GoalIntentCandidate], schema)


def _capability_list(capabilities: tuple[str, ...]) -> Any:
    allowed = frozenset(capabilities)

    def known(value: object) -> list[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("capabilities must be strings")
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unsupported capabilities: {sorted(unknown)}")
        return list(value)

    schema = {"type": "array", "items": {"type": "string", "enum": list(capabilities)}}
    return Annotated[list[str], BeforeValidator(known), WithJsonSchema(schema)]
