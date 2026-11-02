from enterprise_agent.contracts.plans import GlobalPlan, TaskSpec, TaskStatus
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.orchestration.planning.identity import reconcile_plan, task_matches


def _task(
    task_id: str,
    role: str,
    *,
    key: str,
    depends_on: list[str] | None = None,
    criteria: list[str] | None = None,
    description: str | None = None,
    attempts: int = 0,
) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        task_key=key,
        goal_ids=["goal_main"],
        agent_role=role,
        task_description=description or f"执行 {key}",
        expected_output="结果",
        acceptance_criteria=criteria or ["完成目标"],
        depends_on=depends_on or [],
        attempts=attempts,
    )


def _plan(*tasks: TaskSpec, version: int = 1) -> GlobalPlan:
    return GlobalPlan(
        plan_version=version,
        task_id="run-1",
        tasks=list(tasks),
        created_from_route="multi",
    )


def test_replan_matches_new_execution_ids_by_stable_task_key_and_moves_results():
    previous = _plan(
        _task("task_research_v1", "research", key="research", attempts=2),
        _task("task_analysis_v1", "analysis", key="analysis", depends_on=["task_research_v1"]),
    )
    revised = _plan(
        _task("task_research_v2", "research", key="research"),
        _task("task_analysis_v2", "analysis", key="analysis", depends_on=["task_research_v2"]),
        version=2,
    )
    results = {
        "task_research_v1": AgentResult(
            status=ResultStatus.SUCCESS,
            summary="旧研究",
            sources=[{"url": "https://example.test/source"}],
        ),
        "task_analysis_v1": AgentResult(status=ResultStatus.SUCCESS, summary="旧报告"),
    }

    reconciliation = reconcile_plan(revised, previous, results)

    assert reconciliation.task_mapping == {
        "task_research_v1": "task_research_v2",
        "task_analysis_v1": "task_analysis_v2",
    }
    assert reconciliation.task_actions == {
        "task_research_v2": "reused",
        "task_analysis_v2": "reused",
    }
    assert reconciliation.retired_task_ids == ()
    assert set(reconciliation.task_results) == {"task_research_v2", "task_analysis_v2"}
    assert all(task.status == TaskStatus.SUCCEEDED for task in reconciliation.plan.tasks)
    assert reconciliation.plan.tasks[0].attempts == 2
    assert reconciliation.plan.tasks[1].depends_on == ["task_research_v2"]


def test_replan_repairs_failed_task_and_invalidates_dependents():
    previous = _plan(
        _task("task_research_v1", "research", key="research", attempts=1),
        _task("task_analysis_v1", "analysis", key="analysis", depends_on=["task_research_v1"]),
    )
    revised = _plan(
        _task("task_research_v2", "research", key="research"),
        _task("task_analysis_v2", "analysis", key="analysis", depends_on=["task_research_v2"]),
        version=2,
    )
    results = {
        "task_research_v1": AgentResult(status=ResultStatus.FAILED, summary="搜索失败"),
        "task_analysis_v1": AgentResult(status=ResultStatus.SUCCESS, summary="旧报告"),
    }

    reconciliation = reconcile_plan(revised, previous, results)

    assert reconciliation.task_actions == {
        "task_research_v2": "repaired",
        "task_analysis_v2": "repaired",
    }
    assert all(task.status == TaskStatus.PENDING for task in reconciliation.plan.tasks)
    assert [task.attempts for task in reconciliation.plan.tasks] == [1, 0]
    assert set(reconciliation.task_results) == {"task_research_v2", "task_analysis_v2"}


def test_acceptance_criteria_change_forces_repair_even_when_result_succeeded():
    previous = _plan(_task("task_research_v1", "research", key="research"))
    revised = _plan(
        _task(
            "task_research_v2",
            "research",
            key="research",
            criteria=["至少两个独立来源"],
        ),
        version=2,
    )
    results = {"task_research_v1": AgentResult(status=ResultStatus.SUCCESS, summary="旧")}

    reconciliation = reconcile_plan(revised, previous, results)

    assert reconciliation.task_actions == {"task_research_v2": "repaired"}
    assert reconciliation.plan.tasks[0].status == TaskStatus.PENDING


def test_legacy_plans_can_match_one_research_or_analysis_phase_without_task_key():
    old = _task("task_analyze_agent_progress", "analysis", key="legacy-key")
    new = _task("task_analyze_agent_trends", "analysis", key="legacy-key-2")
    old = old.model_copy(update={"task_key": None, "task_description": old.task_id})
    new = new.model_copy(update={"task_key": None, "task_description": new.task_id})

    assert task_matches([new], [old]) == {new.task_id: old.task_id}


def test_legacy_id_only_description_does_not_force_a_completed_task_to_rerun():
    old = _task("task_research_v1", "research", key="legacy-key")
    new = _task("task_research_v2", "research", key="legacy-key-2")
    old = old.model_copy(update={"task_key": None, "task_description": old.task_id})
    new = new.model_copy(update={"task_key": None, "task_description": new.task_id})
    previous = _plan(old)
    revised = _plan(new, version=2)

    reconciliation = reconcile_plan(
        revised,
        previous,
        {old.task_id: AgentResult(status=ResultStatus.SUCCESS, summary="旧证据")},
    )

    assert reconciliation.task_actions == {new.task_id: "reused"}
    assert reconciliation.plan.tasks[0].status == TaskStatus.SUCCEEDED
