import pytest
from pydantic import ValidationError

from enterprise_agent.contracts.goals import Goal, GoalSpec, InputRequirement
from enterprise_agent.governance.authorization import AuthorizationPolicy, RolePolicy
from enterprise_agent.orchestration.routing.candidates import InputRequirementCandidate
from enterprise_agent.orchestration.routing.spec_validation import validate_goal_spec

_KINDS = (
    "material",
    "comparison_subject",
    "calculation_operand",
    "time_range",
    "template",
    "destination",
    "clarification",
)


def _policy() -> AuthorizationPolicy:
    return AuthorizationPolicy(
        [
            RolePolicy(
                "analysis",
                frozenset({"analysis", "calculate", "save_report"}),
                frozenset({"calculate", "save_report"}),
                frozenset(),
            )
        ]
    )


def _requirement(**updates) -> dict:
    payload = {
        "id": "requirement_material",
        "kind": "material",
        "description": "需要用户资料",
        "goal_ids": ["goal_main"],
        "status": "missing",
        "source": "rules",
    }
    payload.update(updates)
    return payload


@pytest.mark.parametrize("kind", _KINDS)
def test_every_input_requirement_kind_is_supported(kind):
    requirement = InputRequirement.model_validate(_requirement(kind=kind))
    assert requirement.kind == kind


def test_input_requirement_rejects_invalid_kind_and_minimum():
    with pytest.raises(ValidationError):
        InputRequirement.model_validate(_requirement(kind="permission"))
    with pytest.raises(ValidationError):
        InputRequirement.model_validate(_requirement(minimum_items=0))


def test_missing_requirement_cannot_claim_satisfaction():
    with pytest.raises(ValidationError, match="missing input requirement"):
        InputRequirement.model_validate(
            _requirement(satisfied_by=["notes"])
        )


def test_satisfied_requirement_must_meet_minimum_items():
    with pytest.raises(ValidationError, match="minimum_items"):
        InputRequirement.model_validate(
            _requirement(
                status="satisfied",
                minimum_items=2,
                satisfied_by=["notes"],
            )
        )

    requirement = InputRequirement.model_validate(
        _requirement(
            status="satisfied",
            minimum_items=2,
            satisfied_by=["notes", "appendix"],
        )
    )
    assert requirement.satisfied_by == ["notes", "appendix"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("goal_ids", ["goal_main", "goal_main"]),
        ("satisfied_by", ["notes", "notes"]),
    ],
)
def test_input_requirement_references_are_unique(field, value):
    payload = _requirement(**{field: value})
    if field == "satisfied_by":
        payload["status"] = "satisfied"
    with pytest.raises(ValidationError, match="references must be unique"):
        InputRequirement.model_validate(payload)


def test_candidate_excludes_server_owned_resolution_fields():
    base = {
        "id": "requirement_material",
        "kind": "material",
        "description": "需要用户资料",
        "goal_ids": ["goal_main"],
    }
    candidate = InputRequirementCandidate.model_validate(base)
    assert candidate.minimum_items == 1

    for field in ("status", "satisfied_by", "source"):
        with pytest.raises(ValidationError, match="server-owned fields"):
            InputRequirementCandidate.model_validate({**base, field: "forbidden"})


def test_goal_spec_validator_checks_requirement_ids_and_goal_references():
    goal = Goal(id="goal_main", description="分析用户资料")
    requirement = InputRequirement.model_validate(_requirement())
    spec = GoalSpec(goals=[goal], input_requirements=[requirement])
    assert validate_goal_spec(spec, _policy()) == spec

    duplicate = GoalSpec(
        goals=[goal],
        input_requirements=[requirement, requirement.model_copy()],
    )
    with pytest.raises(ValueError, match="duplicate input requirement"):
        validate_goal_spec(duplicate, _policy())

    unknown = GoalSpec(
        goals=[goal],
        input_requirements=[
            requirement.model_copy(update={"goal_ids": ["goal_unknown"]})
        ],
    )
    with pytest.raises(ValueError, match="unknown goals"):
        validate_goal_spec(unknown, _policy())


def test_legacy_goal_spec_without_requirements_round_trips():
    legacy = {"goals": [{"id": "goal_main", "description": "解释幂等性"}]}
    spec = GoalSpec.model_validate(legacy)

    assert spec.input_requirements == []
    restored = GoalSpec.model_validate(spec.model_dump(mode="json"))
    assert restored == spec
