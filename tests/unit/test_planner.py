import pytest

from enterprise_agent.contracts.goals import AcceptanceCriterion, Goal, GoalSpec, InputMaterial
from enterprise_agent.contracts.plans import TaskSpec, TaskStatus
from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.orchestration.planning.planner import (
    GlobalPlanner,
    PipelineStrategy,
    PlanningError,
    PlanRequest,
    TaskBlueprint,
    carry_attempts,
)


def _spec(*descriptions: str, inputs: list[str] | None = None) -> GoalSpec:
    goals = [
        Goal(id="goal_main" if index == 0 else f"goal_{index}", description=description)
        for index, description in enumerate(descriptions)
    ]
    return GoalSpec(
        goals=goals,
        inputs=[InputMaterial(name=name) for name in (inputs or [])],
        acceptance_criteria=[AcceptanceCriterion(id="criterion_goal", description="完成用户目标并返回可核验结果")],
    )


def _route(kind: RouteKind, role: str | None = None) -> RouteDecision:
    return RouteDecision(route=kind, rationale="test", selected_role=role)


def test_single_plan_uses_selected_role_and_spec_inputs():
    plan = GlobalPlanner().create(
        "run-1",
        _spec("请搜索网页并返回来源", inputs=["notes"]),
        _route(RouteKind.SINGLE, "research"),
        2,
    )
    task = plan.tasks[0]
    assert plan.plan_version == 2
    assert plan.task_id == "run-1"
    assert plan.created_from_route == RouteKind.SINGLE
    assert task.task_id == "task_worker"
    assert task.agent_role == "research"
    assert task.task_description == "请搜索网页并返回来源"
    assert task.inputs == ["notes"]
    assert task.expected_output == "AgentResult with summary and evidence"
    assert task.acceptance_criteria == ["完成用户目标并返回可核验结果"]
    assert task.status == TaskStatus.PENDING


def test_single_plan_falls_back_when_role_is_missing():
    plan = GlobalPlanner().create("run-1", _spec("比较方案"), _route(RouteKind.SINGLE))
    assert plan.tasks[0].agent_role == "analysis"


def test_multi_plan_links_research_to_analysis():
    plan = GlobalPlanner().create("run-1", _spec("比较方案并生成报告"), _route(RouteKind.MULTI))
    research, analysis = plan.tasks
    assert research.task_id == "task_research"
    assert research.agent_role == "research"
    assert research.task_description == "为以下目标收集可核验网页证据：比较方案并生成报告"
    assert research.acceptance_criteria == ["至少返回来源或明确说明搜索不可用"]
    assert research.depends_on == []
    assert analysis.task_id == "task_analysis"
    assert analysis.agent_role == "analysis"
    assert analysis.task_description == "基于研究结果分析并交付：比较方案并生成报告"
    assert analysis.inputs == ["result:task_research"]
    assert analysis.depends_on == ["task_research"]
    assert analysis.acceptance_criteria == ["完成用户目标并返回可核验结果"]
    assert analysis.expected_output == "带结论和来源的 Markdown 报告"


def test_rebuilt_plan_keeps_attempt_counts():
    previous = GlobalPlanner().create("run-1", _spec("比较方案并生成报告"), _route(RouteKind.MULTI))
    previous = previous.model_copy(update={
        "tasks": [
            previous.tasks[0].model_copy(update={"attempts": 2, "status": TaskStatus.FAILED}),
            previous.tasks[1].model_copy(update={"attempts": 1, "status": TaskStatus.SKIPPED}),
        ]
    })
    rebuilt = GlobalPlanner().create(
        "run-1",
        _spec("比较方案并生成报告"),
        _route(RouteKind.MULTI),
        2,
        completed={"task_research"},
    )
    carried = carry_attempts(rebuilt, previous)
    assert carried.tasks[0].status == TaskStatus.SUCCEEDED
    assert carried.tasks[0].attempts == 2
    assert carried.tasks[1].status == TaskStatus.PENDING
    assert carried.tasks[1].attempts == 1


def test_completed_tasks_stay_succeeded_on_replan():
    plan = GlobalPlanner().create(
        "run-1",
        _spec("比较方案并生成报告"),
        _route(RouteKind.MULTI),
        completed={"task_research"},
    )
    assert plan.tasks[0].status == TaskStatus.SUCCEEDED
    assert plan.tasks[1].status == TaskStatus.PENDING


def test_every_goal_is_attached_and_the_first_description_is_the_objective():
    plan = GlobalPlanner().create(
        "run-1",
        _spec("第一目标", "第二目标描述"),
        _route(RouteKind.SINGLE, "analysis"),
    )
    assert plan.tasks[0].goal_ids == ["goal_main", "goal_1"]
    assert plan.tasks[0].task_description == "第一目标"


def test_user_text_with_braces_is_copied_literally():
    plan = GlobalPlanner().create(
        "run-1",
        _spec("{not_a_field}"),
        _route(RouteKind.MULTI),
    )
    assert plan.tasks[0].task_description == "为以下目标收集可核验网页证据：{not_a_field}"


def test_direct_route_has_no_builtin_strategy():
    with pytest.raises(PlanningError, match="no plan strategy"):
        GlobalPlanner().create("run-1", _spec("请解释幂等性"), _route(RouteKind.DIRECT))


def test_empty_goals_are_rejected():
    with pytest.raises(PlanningError, match="at least one goal"):
        GlobalPlanner().create("run-1", GoalSpec(), _route(RouteKind.SINGLE, "analysis"))


def test_registered_strategy_handles_a_route_without_editing_the_planner():
    planner = GlobalPlanner()
    planner.register(
        PipelineStrategy(
            RouteKind.DIRECT,
            (
                TaskBlueprint(
                    task_id="task_reply",
                    role="analysis",
                    expected_output="direct answer",
                ),
            ),
        )
    )
    plan = planner.create("run-1", _spec("请解释幂等性"), _route(RouteKind.DIRECT))
    assert plan.created_from_route == RouteKind.DIRECT
    assert plan.tasks[0].task_id == "task_reply"
    assert plan.tasks[0].task_description == "请解释幂等性"


def test_duplicate_strategy_is_rejected():
    planner = GlobalPlanner()
    with pytest.raises(PlanningError, match="duplicate plan strategy"):
        planner.register(
            PipelineStrategy(
                RouteKind.SINGLE,
                (TaskBlueprint(task_id="task_other", role="analysis", expected_output="x"),),
            )
        )


def test_pipeline_rejects_unknown_dependency_at_registration():
    with pytest.raises(PlanningError, match="unknown dependency"):
        PipelineStrategy(
            RouteKind.DIRECT,
            (
                TaskBlueprint(
                    task_id="task_worker",
                    role="analysis",
                    expected_output="x",
                    depends_on=("task_missing",),
                ),
            ),
        )


def test_strategy_that_returns_no_tasks_is_rejected():
    class _Empty:
        route = RouteKind.DIRECT

        def build(self, request: PlanRequest) -> list[TaskSpec]:
            del request
            return []

    planner = GlobalPlanner([_Empty()])
    with pytest.raises(PlanningError, match="produced no tasks"):
        planner.create("run-1", _spec("请解释幂等性"), _route(RouteKind.DIRECT))
