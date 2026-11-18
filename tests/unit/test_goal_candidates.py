import pytest
from pydantic import ValidationError

from enterprise_agent.orchestration.routing.candidates import (
    CANDIDATE_SCHEMA_VERSION,
    GoalIntentCandidate,
    candidate_schema,
)

_CAPABILITIES = ("analysis", "calculate", "web_search")


def _goal(**updates):
    payload = {
        "id": "goal_analysis",
        "description": "分析用户资料",
        "required_capabilities": ["analysis"],
    }
    payload.update(updates)
    return payload


def test_explicit_candidate_validates_the_stable_business_shape():
    candidate = GoalIntentCandidate.model_validate(
        {
            "goals": [_goal()],
            "deliverables": {
                "id": "result",
                "description": "JSON 结果",
                "format": "json",
                "delivery_mode": "inline",
            },
            "input_requirements": [
                {
                    "id": "requirement_material",
                    "kind": "material",
                    "description": "请提供资料",
                    "goal_ids": ["goal_analysis"],
                }
            ],
        }
    )

    assert CANDIDATE_SCHEMA_VERSION == "goal-candidate-v2"
    assert candidate.goals[0].id == "goal_analysis"
    assert candidate.deliverables[0].format == "json"
    assert candidate.deliverables[0].delivery_mode == "inline"
    assert candidate.input_requirements[0].kind == "material"


@pytest.mark.parametrize(
    "payload",
    [
        {"goals": []},
        {"goals": [_goal(id="BAD ID")]},
        {"goals": [_goal(), _goal()]},
        {
            "goals": [_goal()],
            "deliverables": [{"id": "x", "description": "x", "format": "xml"}],
        },
        {
            "goals": [_goal()],
            "input_requirements": [
                {
                    "id": "requirement_material",
                    "kind": "material",
                    "description": "请提供资料",
                    "goal_ids": ["goal_missing"],
                }
            ],
        },
        {
            "goals": [_goal()],
            "input_requirements": [
                {
                    "id": "requirement_material",
                    "kind": "material",
                    "description": "请提供资料",
                    "goal_ids": ["goal_analysis"],
                    "status": "satisfied",
                }
            ],
        },
        {
            "goals": [_goal()],
            "deliverables": [
                {"id": "x", "description": "x", "delivery_mode": "database"}
            ],
        },
    ],
)
def test_explicit_candidate_rejects_invalid_domain_shapes(payload):
    with pytest.raises(ValidationError):
        GoalIntentCandidate.model_validate(payload)


def test_provider_schema_enforces_runtime_capability_catalog():
    schema = candidate_schema(_CAPABILITIES)
    candidate = schema.model_validate({"goals": [_goal()]})

    assert isinstance(candidate, GoalIntentCandidate)
    with pytest.raises(ValidationError, match="unsupported capabilities"):
        schema.model_validate(
            {"goals": [_goal(required_capabilities=["shell"])]}
        )


def test_provider_json_schema_contains_runtime_capability_enum():
    raw = candidate_schema(_CAPABILITIES).model_json_schema()
    goal_schema = raw["$defs"]["ProviderGoalCandidate"]
    capabilities = goal_schema["properties"]["required_capabilities"]

    assert capabilities["items"]["enum"] == list(_CAPABILITIES)
