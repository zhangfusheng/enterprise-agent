from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from enterprise_agent.contracts.goals import InputMaterial, InputRequirement
from enterprise_agent.contracts.state import (
    STATE_SCHEMA_VERSION,
    StateSchemaError,
    migrate_orchestration_state,
)
from enterprise_agent.orchestration.routing.candidates import (
    GoalCandidate,
    GoalIntentCandidate,
    candidate_schema,
)
from enterprise_agent.orchestration.routing.errors import (
    CandidatePolicyError,
    CandidateSemanticError,
    GoalSpecInvariantError,
)
from enterprise_agent.orchestration.routing.input_requirements import (
    resolve_input_requirements,
)
from enterprise_agent.orchestration.routing.llm_parser import (
    LLMGoalParser,
    _compile_goal_spec,
    _validate_candidate_semantics,
)

_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENT_CASES = yaml.safe_load(
    (_ROOT / "evals/datasets/requirements.yaml").read_text(encoding="utf-8")
)
_CAPABILITIES = frozenset({"analysis", "calculate", "web_search"})


@pytest.mark.parametrize(
    "case",
    _REQUIREMENT_CASES,
    ids=[item["id"] for item in _REQUIREMENT_CASES],
)
def test_requirement_resolution_matrix(case):
    requirement = InputRequirement(
        id="requirement_eval",
        kind=case["kind"],
        description=f"missing {case['kind']}",
        goal_ids=["goal_main"],
        minimum_items=case.get("minimum_items", 1),
        status="missing",
        source="server",
    )

    resolution = resolve_input_requirements(
        [requirement],
        case["request"],
        [InputMaterial.model_validate(item) for item in case["inputs"]],
    )

    assert resolution.requirements[0].status == case["expected_status"]
    assert bool(resolution.missing_information) == (
        case["expected_status"] == "missing"
    )


def test_candidate_schema_semantic_policy_and_invariant_boundaries(
    monkeypatch: pytest.MonkeyPatch,
):
    with pytest.raises(ValidationError):
        candidate_schema(tuple(sorted(_CAPABILITIES))).model_validate({"goals": "invalid"})

    semantic = GoalIntentCandidate.model_construct(goals=[])
    with pytest.raises(CandidateSemanticError):
        _validate_candidate_semantics(semantic)

    policy = GoalIntentCandidate.model_construct(
        goals=[
            GoalCandidate.model_construct(
                id="goal_main",
                description="执行未知能力",
                required_capabilities=["shell"],
            )
        ],
        deliverables=[],
        acceptance_criteria=[],
        input_requirements=[],
    )
    with pytest.raises(CandidatePolicyError):
        _compile_goal_spec(policy, "执行未知能力", [], None, _CAPABILITIES)

    parser = LLMGoalParser(_Completion(_valid_candidate()), _CAPABILITIES)
    monkeypatch.setattr(
        "enterprise_agent.orchestration.routing.llm_parser._compile_goal_spec",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("broken invariant")),
    )
    with pytest.raises(GoalSpecInvariantError):
        parser.parse_result("请解释幂等性")


@pytest.mark.parametrize(
    ("case_id", "state", "expected"),
    [
        ("S01-legacy-v0", {"inputs": []}, STATE_SCHEMA_VERSION),
        (
            "S02-current-v1",
            {"state_version": STATE_SCHEMA_VERSION, "inputs": []},
            STATE_SCHEMA_VERSION,
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_state_migration_matrix(case_id, state, expected):
    del case_id
    assert migrate_orchestration_state(state)["state_version"] == expected


def test_future_state_version_is_rejected():
    with pytest.raises(StateSchemaError):
        migrate_orchestration_state({"state_version": STATE_SCHEMA_VERSION + 1})


def test_parser_provenance_is_atomic_across_consecutive_outcomes():
    parser = LLMGoalParser(
        _Completion(_valid_candidate(), TimeoutError("timed out")),
        _CAPABILITIES,
        model_version="eval-router",
    )

    accepted = parser.parse_result("请解释幂等性")
    fallback = parser.parse_result("请解释幂等性")

    assert accepted.provenance.parser == "llm"
    assert accepted.provenance.fallback_reason is None
    assert fallback.provenance.parser == "fallback"
    assert fallback.provenance.fallback_reason == "transport"
    assert accepted.provenance.parser == "llm"


class _Completion:
    def __init__(self, *results: object) -> None:
        self.results = list(results)

    def complete(
        self,
        schema: type[BaseModel],
        messages: list[object],
        *,
        temperature: float,
    ) -> BaseModel:
        del messages, temperature
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return schema.model_validate(result)


def _valid_candidate() -> dict:
    return {
        "goals": [
            {
                "id": "goal_main",
                "description": "解释幂等性",
                "required_capabilities": [],
            }
        ]
    }
