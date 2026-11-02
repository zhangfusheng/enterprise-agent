"""Server-side checks for a candidate plan. These cases never call a model."""

import pytest
from pydantic import BaseModel, Field

from enterprise_agent.contracts.goals import Goal, GoalSpec
from enterprise_agent.contracts.plans import TaskStatus
from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.governance.authorization import AuthorizationPolicy, RolePolicy
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.orchestration.planning.llm_planner import _messages, compile_draft
from enterprise_agent.orchestration.planning.validation import PlanValidationError

_ROLES = frozenset({"research", "analysis"})
_LIMITS = BudgetLimits(model_calls=4, tool_calls=4, agents=2, steps=4, runtime_seconds=30)


class _Task(BaseModel):
    task_id: str
    goal_ids: list[str]
    agent_role: str
    required_capabilities: list[str] = Field(default_factory=list)
    task_description: str = "完成这项任务"
    inputs: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    expected_output: str = "结论"
    acceptance_criteria: list[str] = Field(default_factory=list)


class _Draft(BaseModel):
    tasks: list[_Task]


@pytest.fixture
def policy() -> AuthorizationPolicy:
    return AuthorizationPolicy(
        [
            RolePolicy("research", frozenset({"web_search", "web_fetch"}), frozenset(), frozenset()),
            RolePolicy("analysis", frozenset({"analysis", "calculate"}), frozenset(), frozenset()),
        ]
    )


def _spec(*capabilities: str) -> GoalSpec:
    return GoalSpec(goals=[Goal(id="goal_main", description="完成用户给出的目标", required_capabilities=list(capabilities))])


def _route(kind: RouteKind) -> RouteDecision:
    return RouteDecision(route=kind, rationale="server gate")


def _compile(draft: _Draft, policy: AuthorizationPolicy, **kwargs: object):
    values = {
        "run_id": "run-1",
        "spec": _spec("web_search", "analysis"),
        "route": _route(RouteKind.MULTI),
        "plan_version": 1,
        "completed": set(),
        "policy": policy,
        "worker_roles": _ROLES,
        "max_tasks": 4,
        "limits": _LIMITS,
        "budget_used": {},
    }
    values.update(kwargs)
    return compile_draft(draft, **values)  # type: ignore[arg-type]


def test_compile_accepts_research_before_analysis(policy: AuthorizationPolicy) -> None:
    plan = _compile(
        _Draft(
            tasks=[
                _Task(task_id="task_research", goal_ids=["goal_main"], agent_role="research", required_capabilities=["web_search"]),
                _Task(
                    task_id="task_analysis",
                    goal_ids=["goal_main"],
                    agent_role="analysis",
                    required_capabilities=["analysis"],
                    depends_on=["task_research"],
                    inputs=["result:task_research"],
                ),
            ]
        ),
        policy,
    )
    assert [task.status for task in plan.tasks] == [TaskStatus.PENDING, TaskStatus.PENDING]


def test_compile_rejects_an_unknown_role(policy: AuthorizationPolicy) -> None:
    draft = _Draft(tasks=[_Task(task_id="task_admin", goal_ids=["goal_main"], agent_role="admin")])
    with pytest.raises(PlanValidationError, match="not a worker"):
        _compile(draft, policy, route=_route(RouteKind.SINGLE), spec=_spec("analysis"))


def test_compile_rejects_a_capability_the_role_lacks(policy: AuthorizationPolicy) -> None:
    draft = _Draft(
        tasks=[_Task(task_id="task_worker", goal_ids=["goal_main"], agent_role="analysis", required_capabilities=["web_search"])]
    )
    with pytest.raises(PlanValidationError, match="lacks capabilities"):
        _compile(draft, policy, route=_route(RouteKind.SINGLE))


def test_compile_rejects_a_capability_the_goal_did_not_request(policy: AuthorizationPolicy) -> None:
    draft = _Draft(
        tasks=[_Task(task_id="task_worker", goal_ids=["goal_main"], agent_role="research", required_capabilities=["web_search"])]
    )
    with pytest.raises(PlanValidationError, match="did not"):
        _compile(draft, policy, route=_route(RouteKind.SINGLE), spec=_spec("analysis"))


def test_compile_rejects_a_cycle(policy: AuthorizationPolicy) -> None:
    draft = _Draft(
        tasks=[
            _Task(task_id="task_a", goal_ids=["goal_main"], agent_role="analysis", required_capabilities=["analysis"], depends_on=["task_b"]),
            _Task(task_id="task_b", goal_ids=["goal_main"], agent_role="analysis", required_capabilities=["calculate"], depends_on=["task_a"]),
        ]
    )
    with pytest.raises(PlanValidationError, match="cycle"):
        _compile(draft, policy, route=_route(RouteKind.SINGLE), spec=_spec("analysis", "calculate"))


def test_compile_rejects_a_plan_over_the_remaining_budget(policy: AuthorizationPolicy) -> None:
    draft = _Draft(
        tasks=[
            _Task(task_id="task_a", goal_ids=["goal_main"], agent_role="analysis", required_capabilities=["analysis"]),
            _Task(task_id="task_b", goal_ids=["goal_main"], agent_role="analysis", required_capabilities=["calculate"]),
        ]
    )
    limits = BudgetLimits(model_calls=4, tool_calls=4, agents=1, steps=4, runtime_seconds=30)
    with pytest.raises(PlanValidationError, match="agent budget"):
        _compile(draft, policy, route=_route(RouteKind.SINGLE), spec=_spec("analysis", "calculate"), limits=limits)


def test_compile_rejects_a_dropped_completed_task(policy: AuthorizationPolicy) -> None:
    draft = _Draft(
        tasks=[_Task(task_id="task_analysis", goal_ids=["goal_main"], agent_role="analysis", required_capabilities=["analysis"])]
    )
    with pytest.raises(PlanValidationError, match="dropped completed"):
        _compile(draft, policy, route=_route(RouteKind.SINGLE), spec=_spec("analysis"), completed={"task_research"})


def test_compile_rejects_an_unknown_input(policy: AuthorizationPolicy) -> None:
    draft = _Draft(
        tasks=[
            _Task(
                task_id="task_analysis",
                goal_ids=["goal_main"],
                agent_role="analysis",
                required_capabilities=["analysis"],
                inputs=["secret"],
            )
        ]
    )
    with pytest.raises(PlanValidationError, match="unknown input"):
        _compile(draft, policy, route=_route(RouteKind.SINGLE), spec=_spec("analysis"))


def test_compile_keeps_a_completed_task(policy: AuthorizationPolicy) -> None:
    draft = _Draft(
        tasks=[
            _Task(task_id="task_research", goal_ids=["goal_main"], agent_role="research", required_capabilities=["web_search"]),
            _Task(
                task_id="task_analysis",
                goal_ids=["goal_main"],
                agent_role="analysis",
                required_capabilities=["analysis"],
                depends_on=["task_research"],
            ),
        ]
    )
    plan = _compile(draft, policy, completed={"task_research"})
    by_id = {task.task_id: task.status for task in plan.tasks}
    assert by_id["task_research"] == TaskStatus.SUCCEEDED
    assert by_id["task_analysis"] == TaskStatus.PENDING


def test_prompt_hides_capabilities_the_goal_did_not_request() -> None:
    policy = AuthorizationPolicy(
        [
            RolePolicy("research", frozenset({"web_search", "web_fetch"}), frozenset(), frozenset()),
            RolePolicy("analysis", frozenset({"analysis", "calculate", "save_report"}), frozenset(), frozenset()),
        ]
    )
    spec = GoalSpec(
        goals=[
            Goal(
                id="goal_main",
                description="请搜索网页并分析这家公司的变化，生成报告",
                required_capabilities=["web_search", "analysis"],
            )
        ]
    )
    messages = _messages(
        spec,
        _route(RouteKind.MULTI),
        set(),
        (),
        policy,
        _ROLES,
        ("analysis", "web_search"),
    )
    body = str(messages[1].content)
    assert "save_report" not in body
    assert "web_fetch" not in body
    assert "calculate" not in body
    assert "research: web_search" in body
    assert "analysis: analysis" in body
