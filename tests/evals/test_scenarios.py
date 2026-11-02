"""Control-plane evals for the scenarios the router and graph must keep.

Negation, multiple goals, composite capabilities, privilege escalation, and
missing materials are decided before a worker runs. Failure replans and
succeeded tasks are decided by the graph. These cases do not call a provider.
"""

import pytest

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.goals import Goal, GoalSpec
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.contracts.routing import RouteKind
from enterprise_agent.contracts.runs import RunCreate, RunStatus
from enterprise_agent.governance.authorization import (
    AuthorizationError,
    AuthorizationPolicy,
    RolePolicy,
)
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.infrastructure.observability.metrics import (
    begin_run_metrics,
    metrics_snapshot,
    reset_run_metrics,
)
from enterprise_agent.orchestration.execution.workers import WorkerRegistry
from enterprise_agent.orchestration.graph import GraphDependencies, build_graph
from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.orchestration.routing.router import Router
from enterprise_agent.orchestration.routing.validation import validate_route


def _policy() -> AuthorizationPolicy:
    return AuthorizationPolicy(
        [
            RolePolicy(
                "research",
                frozenset({"web_search", "web_fetch", "workspace_read"}),
                frozenset({"web_search", "web_fetch", "workspace_read"}),
                frozenset({"analysis"}),
            ),
            RolePolicy(
                "analysis",
                frozenset({"analysis", "calculate", "save_report"}),
                frozenset({"calculate", "save_report"}),
                frozenset(),
            ),
        ]
    )


class _ScriptedWorker:
    def __init__(self, role: str, script: dict[str, list[AgentResult]]):
        self.role = role
        self.script = script
        self.seen: list[tuple[str, int]] = []

    @property
    def bound_agents(self) -> tuple:
        return ()

    def execute(self, task, context, results, *, delegate, agent_count) -> AgentResult:
        del context, results, delegate, agent_count
        self.seen.append((task.task_id, task.attempts))
        queued = self.script.get(task.task_id, [])
        if queued:
            return queued.pop(0)
        return AgentResult(status=ResultStatus.SUCCESS, summary=task.task_id)


def _graph(*workers: _ScriptedWorker):
    limits = BudgetLimits(model_calls=10, tool_calls=10, agents=8, steps=20, runtime_seconds=100)
    return build_graph(
        GraphDependencies(WorkerRegistry(workers), _policy(), limits, max_tasks=12, max_replans=2)
    )


def test_negated_search_stays_a_direct_answer():
    spec = GoalParser().parse("不要搜索网页，只解释幂等性")
    decision = Router().decide(spec)
    assert "web_search" not in spec.goals[0].required_capabilities
    assert decision.route == RouteKind.DIRECT
    validate_route(decision, _policy())


def test_a_real_search_request_is_not_treated_as_negation():
    spec = GoalParser().parse("请搜索网页并返回来源")
    decision = Router().decide(spec)
    assert decision.route == RouteKind.SINGLE
    assert decision.selected_role == "research"
    validate_route(decision, _policy())


def test_negated_calculation_stays_on_analysis():
    spec = GoalParser().parse("请分析但不要计算增长率")
    decision = Router().decide(spec)
    assert spec.goals[0].required_capabilities == ["analysis"]
    assert decision.route == RouteKind.SINGLE
    assert decision.selected_role == "analysis"
    validate_route(decision, _policy())


def test_multiple_goals_split_across_roles():
    spec = GoalSpec(
        goals=[
            Goal(id="goal_search", description="搜索竞品本周动态", required_capabilities=["web_search"]),
            Goal(id="goal_calc", description="计算增长率", required_capabilities=["calculate"]),
        ]
    )
    decision = Router().decide(spec)
    assert decision.route == RouteKind.MULTI
    assert decision.selected_role is None
    assert set(decision.required_capabilities) == {"web_search", "calculate"}
    validate_route(decision, _policy())


def test_composite_analysis_and_calculate_stay_on_one_worker():
    spec = GoalParser().parse("请计算增长率并写出报告")
    decision = Router().decide(spec)
    assert decision.route == RouteKind.SINGLE
    assert decision.selected_role == "analysis"
    assert {"analysis", "calculate"} <= set(decision.required_capabilities)
    validate_route(decision, _policy())


def test_requested_role_cannot_escalate_privileges():
    spec = GoalParser().parse("请比较方案")
    decision = Router().decide(spec, "research")
    with pytest.raises(AuthorizationError, match="lacks capabilities"):
        validate_route(decision, _policy())


def test_missing_material_is_not_routed():
    spec = GoalParser().parse("请分析我的资料")
    assert spec.is_waiting_input
    with pytest.raises(ValueError, match="waiting input"):
        Router().decide(spec)


def test_failed_task_replans_without_rerunning_the_succeeded_task():
    research = _ScriptedWorker(
        "research",
        {"task_research": [_result(ResultStatus.SUCCESS, "来源", sources=[{"url": "https://a.example"}])]},
    )
    analysis = _ScriptedWorker("analysis", {"task_analysis": [_result(ResultStatus.FAILED, "报告失败")]})
    token = begin_run_metrics()
    try:
        final = _graph(research, analysis).invoke(_state("请搜索网页并分析，生成报告"))
        snapshot = metrics_snapshot()
    finally:
        reset_run_metrics(token)
    assert research.seen == [("task_research", 1)]
    assert [attempts for _, attempts in analysis.seen] == [1, 2]
    assert snapshot["retries"] == 1
    assert final["status"] == RunStatus.SUCCEEDED


def test_run_pins_versions_and_empty_model_metrics(container):
    identity = IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True)
    run_id = submit_run(
        container.runs,
        identity,
        RunCreate(input="请直接回答什么是幂等性？"),
        container.config_snapshot,
    )
    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    state = container.runs.get_run_unscoped(run_id).state_json
    versions = state["config_snapshot"]["versions"]
    assert versions["policy"] == "agents-v1"
    assert versions["orchestration"] == "orchestration-v1"
    assert versions["goal_prompt"] == "goal-parser-v2"
    assert versions["plan_prompt"] == "plan-draft-v1"
    metrics = state["metrics"]
    assert metrics["model_calls"] == 0
    assert metrics["structured_output_failures"] == 0
    assert metrics["fallbacks"] == 0
    assert metrics["retries"] == 0
    assert metrics["cost_usd"] == 0
    assert state["resolved_versions"]["parser"] == "rules"
    assert "planner" not in state["resolved_versions"]


def _result(status: ResultStatus, summary: str, **updates) -> AgentResult:
    payload = {"status": status, "summary": summary}
    payload.update(updates)
    return AgentResult(**payload)


def _state(text: str) -> dict:
    return {
        "run_id": "run-eval",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "input_text": text,
        "inputs": [],
        "constraints": {},
    }
