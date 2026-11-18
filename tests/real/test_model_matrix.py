"""Cross-provider smoke matrix for explicit model profiles.

Set ``EA_REAL_TESTS=1`` and provide comma-separated profile names through
``EA_REAL_MODEL_PROFILES``. Secrets are resolved by each profile's
``credential_ref`` and are never part of pytest parameters or output.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from enterprise_agent.application.execute_run import _request_parser, _request_planner
from enterprise_agent.bootstrap.container import Container
from enterprise_agent.bootstrap.settings import Settings
from enterprise_agent.contracts.goals import Goal, GoalSpec
from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.orchestration.planning.llm_planner import LLMPlanner
from enterprise_agent.orchestration.planning.validation import validate_plan
from enterprise_agent.orchestration.routing.llm_parser import PROMPT_VERSION, LLMGoalParser


def _profiles() -> list[str]:
    return [
        value.strip()
        for value in os.environ.get("EA_REAL_MODEL_PROFILES", "").split(",")
        if value.strip()
    ]


pytestmark = [
    pytest.mark.skipif(
        os.environ.get("EA_REAL_TESTS", "").strip().lower() not in {"1", "true", "yes"},
        reason="set EA_REAL_TESTS=1 to call configured models",
    ),
    pytest.mark.skipif(
        not _profiles(),
        reason="set EA_REAL_MODEL_PROFILES to explicit profile names",
    ),
]


@pytest.fixture(params=_profiles(), ids=lambda profile: profile)
def live_profile(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Iterator[tuple[Container, str]]:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    profile = str(request.param)
    container = Container(
        Settings(
            database_url=f"sqlite:///{tmp_path}/app.sqlite",
            checkpoint_url=f"sqlite:///{tmp_path}/checkpoint.sqlite",
            artifact_root=tmp_path / "artifacts",
            offline_mode=False,
            allow_external_network=True,
        )
    )
    try:
        yield container, profile
    finally:
        container.close()


def test_profile_parser_closes_material_requirement(
    live_profile: tuple[Container, str],
) -> None:
    container, profile = live_profile
    parser = _request_parser(container, profile)
    assert isinstance(parser, LLMGoalParser)

    waiting = parser.parse_result("请分析我的资料")
    supplied = parser.parse_result(
        "请分析我的资料",
        [{"name": "notes", "content": "正文"}],
    )

    expected_model = container.models.profile_for_role("router", profile).model_name
    assert waiting.provenance.parser == "llm"
    assert waiting.provenance.model_version == expected_model
    assert waiting.provenance.prompt_version == PROMPT_VERSION
    assert any(
        item.kind == "material" and item.status == "missing"
        for item in waiting.spec.input_requirements
    )
    assert supplied.spec.missing_information == []
    assert any(
        item.kind == "material" and item.status == "satisfied"
        for item in supplied.spec.input_requirements
    )


def test_profile_planner_returns_valid_explicit_provenance(
    live_profile: tuple[Container, str],
) -> None:
    container, profile = live_profile
    planner = _request_planner(container, profile)
    assert isinstance(planner, LLMPlanner)
    spec = GoalSpec(
        goals=[
            Goal(
                id="goal_main",
                description="搜索公开变化并分析",
                required_capabilities=["web_search", "analysis"],
            )
        ]
    )
    route = RouteDecision(
        route=RouteKind.MULTI,
        required_capabilities=["web_search", "analysis"],
        rationale="cross-provider eval",
        requires_external_operation=True,
        requires_realtime_information=True,
    )

    result = planner.create_result(
        "run-model-matrix",
        spec,
        route,
        limits=container.budget_limits,
    )

    expected_model = container.models.profile_for_role("planner", profile).model_name
    assert result.provenance.name == "llm"
    assert result.provenance.model_version == expected_model
    validate_plan(
        result.plan,
        {"goal_main"},
        container.policy,
        container.settings.max_tasks,
    )
