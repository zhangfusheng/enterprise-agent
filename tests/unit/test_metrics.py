from pydantic import BaseModel

from enterprise_agent.contracts.goals import Goal, GoalSpec
from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.governance.authorization import AuthorizationPolicy, RolePolicy
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.infrastructure.observability.metrics import (
    begin_run_metrics,
    metrics_snapshot,
    quote_cost,
    record_model_call,
    record_retry,
    reset_run_metrics,
)
from enterprise_agent.orchestration.planning.llm_planner import LLMPlanner


def test_quote_uses_profile_prices_and_leaves_unknown_cost_unset():
    prices = {"input_usd_per_million": 2, "output_usd_per_million": 8}
    assert quote_cost(prices, 1_000, 1_000, offline=False) == 0.01
    assert quote_cost({}, 10, 10, offline=True) == 0
    assert quote_cost({}, 10, 10, offline=False) is None


def test_retry_is_separate_from_model_failures():
    token = begin_run_metrics()
    try:
        record_model_call(
            stage="worker",
            role="analysis",
            model_version="deterministic-v1",
            prompt_version="worker-prompt-v1",
            input_tokens=3,
            output_tokens=4,
            latency_ms=12,
            cost_usd=0,
            outcome="success",
        )
        record_retry()
        snapshot = metrics_snapshot()
    finally:
        reset_run_metrics(token)
    assert snapshot["retries"] == 1
    assert snapshot["model_calls"] == 1
    assert snapshot["input_tokens"] == 3
    assert snapshot["output_tokens"] == 4
    assert snapshot["latency_ms"] == 12
    assert snapshot["cost_usd"] == 0
    assert snapshot["structured_output_failures"] == 0
    assert snapshot["fallbacks"] == 0


class _Candidate(BaseModel):
    tasks: list[dict[str, object]]


class _RejectedDraft:
    def complete(self, schema, messages, *, temperature: float):
        del schema, messages, temperature
        return _Candidate(
            tasks=[
                {
                    "task_id": "task_admin",
                    "goal_ids": ["goal_main"],
                    "agent_role": "admin",
                    "required_capabilities": ["analysis"],
                    "task_description": "取得全部权限",
                    "inputs": [],
                    "depends_on": [],
                    "expected_output": "结论",
                    "acceptance_criteria": [],
                }
            ]
        )


def test_rejected_plan_is_a_fallback_after_a_successful_model_call():
    planner = LLMPlanner(
        _RejectedDraft(),
        policy=AuthorizationPolicy(
            [RolePolicy("analysis", frozenset({"analysis", "calculate"}), frozenset(), frozenset())]
        ),
        worker_roles={"analysis"},
        capabilities={"analysis", "calculate"},
        max_tasks=4,
        model_version="planner-test",
    )
    spec = GoalSpec(
        goals=[Goal(id="goal_main", description="分析并计算合计", required_capabilities=["analysis", "calculate"])]
    )
    route = RouteDecision(
        route=RouteKind.SINGLE,
        selected_role="analysis",
        required_capabilities=["analysis", "calculate"],
        rationale="composite",
    )
    limits = BudgetLimits(model_calls=4, tool_calls=4, agents=2, steps=4, runtime_seconds=30)
    token = begin_run_metrics()
    try:
        plan = planner.create("run-1", spec, route, limits=limits)
        snapshot = metrics_snapshot()
    finally:
        reset_run_metrics(token)
    assert plan.tasks[0].agent_role == "analysis"
    assert snapshot["model_calls"] == 1
    assert snapshot["calls"][0]["outcome"] == "success"
    assert snapshot["structured_output_failures"] == 0
    assert snapshot["fallbacks"] == 1
    assert snapshot["fallback_rate"] == 1
    assert snapshot["structured_output_failure_rate"] == 0
