"""Offline replay of the c66bdb27 research/replan failure trace.

This is an offline replay of the incident captured in the local trace database.
It deliberately does not call a model or the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.contracts.plans import GlobalPlan, TaskSpec, TaskStatus
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.governance.authorization import AuthorizationPolicy, RolePolicy
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.orchestration.execution.workers import WorkerRegistry
from enterprise_agent.orchestration.graph import GraphDependencies, build_graph
from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.runtime.budgets import BudgetExceeded

_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "c66bdb27-1aef-4ecb-8852-87c45f2f357b.json"
)


def _trace() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


class _ReplayParser:
    """Replay the LLM parser's multi-agent decision for the incident input."""

    def parse(self, text, inputs=None, constraints=None) -> GoalSpec:
        del text, inputs, constraints
        return GoalParser().parse("请搜索网页并分析，生成报告")


class _ReplayPlanner:
    def __init__(self, fixture: dict[str, Any]) -> None:
        self.fixture = fixture

    def create(
        self,
        run_id,
        spec,
        route,
        plan_version=1,
        completed=None,
        **kwargs,
    ) -> GlobalPlan:
        del spec, kwargs
        completed = completed or set()
        raw = self.fixture["plans"][str(plan_version)]
        tasks = [
            TaskSpec(
                task_id=item["task_id"],
                goal_ids=["goal_main"],
                agent_role=item["agent_role"],
                task_description=item["task_id"],
                expected_output="replayed result",
                depends_on=item["depends_on"],
                status=(
                    TaskStatus.SUCCEEDED
                    if item["task_id"] in completed
                    else TaskStatus.PENDING
                ),
            )
            for item in raw["tasks"]
        ]
        return GlobalPlan(
            plan_version=plan_version,
            task_id=run_id,
            tasks=tasks,
            created_from_route=route.route,
        )


class _ReplayResearchWorker:
    role = "research"

    def __init__(self, fixture: dict[str, Any], *, fail_on_retry: bool) -> None:
        self.fixture = fixture
        self.fail_on_retry = fail_on_retry
        self.calls: list[tuple[str, int]] = []

    @property
    def bound_agents(self) -> tuple[()]:
        return ()

    def execute(self, task, context, results, *, delegate, agent_count):
        del context, results, delegate, agent_count
        self.calls.append((task.task_id, task.attempts))
        if len(self.calls) > 1 and self.fail_on_retry:
            raise BudgetExceeded(self.fixture["second_attempt"]["error"])
        if len(self.calls) == 1:
            attempt = self.fixture["first_attempt"]
            return AgentResult(
                status=ResultStatus.PARTIAL,
                summary=f"收集到 {attempt['unique_sources']} 条来源。",
                sources=[
                    {
                        "title": f"source-{index}",
                        "url": f"https://example.test/source-{index}",
                        "snippet": "replayed evidence",
                    }
                    for index in range(attempt["unique_sources"])
                ],
                errors=[f"fetch-error-{index}" for index in range(attempt["fetch_errors"])],
            )
        return AgentResult(
            status=ResultStatus.SUCCESS,
            summary="retry completed",
            sources=[{"url": "https://example.test/retry"}],
        )


class _ReplayAnalysisWorker:
    role = "analysis"

    def __init__(self, *, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.calls: list[tuple[str, int]] = []

    @property
    def bound_agents(self) -> tuple[()]:
        return ()

    def execute(self, task, context, results, *, delegate, agent_count):
        del context, results, delegate, agent_count
        self.calls.append((task.task_id, task.attempts))
        if self.fail_first and len(self.calls) == 1:
            return AgentResult(status=ResultStatus.FAILED, summary="analysis failed", errors=["replayed"])
        return AgentResult(status=ResultStatus.SUCCESS, summary="analysis completed")


def _policy() -> AuthorizationPolicy:
    return AuthorizationPolicy(
        [
            RolePolicy(
                "research",
                frozenset({"web_search", "web_fetch"}),
                frozenset({"web_search", "web_fetch"}),
                frozenset(),
            ),
            RolePolicy(
                "analysis",
                frozenset({"analysis"}),
                frozenset(),
                frozenset(),
            ),
        ]
    )


def _state(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": fixture["run_id"],
        "tenant_id": "local-tenant",
        "user_id": "local-user",
        "input_text": fixture["input"],
        "inputs": [],
        "constraints": {},
    }


def _graph(fixture: dict[str, Any], research, analysis):
    return build_graph(
        GraphDependencies(
            WorkerRegistry([research, analysis]),
            _policy(),
            BudgetLimits(
                model_calls=40,
                tool_calls=80,
                agents=8,
                steps=100,
                runtime_seconds=300,
            ),
            max_tasks=12,
            max_replans=2,
            parser=_ReplayParser(),
            draft_planner=_ReplayPlanner(fixture),
        )
    )


def test_c66_trace_replay_accepts_usable_partial_without_replan():
    fixture = _trace()
    research = _ReplayResearchWorker(fixture, fail_on_retry=True)
    analysis = _ReplayAnalysisWorker()

    final = _graph(fixture, research, analysis).invoke(_state(fixture))

    assert fixture["first_attempt"]["unique_sources"] == 75
    assert fixture["first_attempt"]["aggregate_status"] == "PARTIAL"
    assert fixture["second_attempt"]["error"].startswith("BudgetExceeded")
    assert final.get("replan_count", 0) == 0
    assert research.calls == [("task_research_latest_agent_info", 1)]
    assert analysis.calls == [("task_analyze_agent_progress", 1)]
    assert final["status"] == "PARTIAL"


def test_c66_trace_replay_replans_incrementally_when_analysis_fails():
    fixture = _trace()
    research = _ReplayResearchWorker(fixture, fail_on_retry=True)
    analysis = _ReplayAnalysisWorker(fail_first=True)

    final = _graph(fixture, research, analysis).invoke(_state(fixture))

    assert research.calls == [("task_research_latest_agent_info", 1)]
    assert analysis.calls == [
        ("task_analyze_agent_progress", 1),
        ("task_analyze_agent_trends", 2),
    ]
    assert final["plan_history"][1]["task_mapping"] == {
        "task_research_latest_agent_info": "task_research_latest_agent_info",
        "task_analyze_agent_progress": "task_analyze_agent_trends",
    }
    assert final["plan_history"][1]["task_actions"]["task_research_latest_agent_info"] == "reused"
    assert final["plan_history"][1]["task_actions"]["task_analyze_agent_trends"] == "repaired"
    assert final["status"] == "PARTIAL"
