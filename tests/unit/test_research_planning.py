import pytest

from enterprise_agent.agents.research.planning import (
    PlanValidationError,
    ResearchPlanner,
)
from enterprise_agent.agents.research.schemas import (
    ResearchExecutionMode,
    ResearchPlanDraft,
    ResearchPlanTaskDraft,
    ResearchRequest,
)
from enterprise_agent.contracts.context import ExecutionContext


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="plan-run",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_research",
        workspace_root=".",
    )


class _Completion:
    def __init__(self, drafts: list[ResearchPlanDraft]) -> None:
        self.drafts = list(drafts)
        self.calls = 0

    def complete(self, messages, *, request):
        del messages, request
        self.calls += 1
        return self.drafts.pop(0)


def _planner(completion: _Completion, **updates: object) -> ResearchPlanner:
    options = {
        "max_search_calls": 3,
        "max_fetch_calls": 4,
        "min_verified_sources": 1,
        "max_candidate_sources": 3,
        "max_concurrency": 2,
    }
    options.update(updates)
    return ResearchPlanner(completion, **options)


def test_offline_default_plan_has_one_server_bounded_query():
    from enterprise_agent.agents.research.planning import OfflinePlanCompletion

    plan = ResearchPlanner(
        OfflinePlanCompletion(),
        max_search_calls=3,
        max_fetch_calls=0,
        min_verified_sources=1,
        max_candidate_sources=1,
    ).create_plan(ResearchRequest(query="  最新 Agent 信息  "), _context())

    assert plan.goal == "最新 Agent 信息"
    assert len(plan.queries) == 1
    assert plan.queries[0].query == "最新 Agent 信息"
    assert plan.execution_mode == ResearchExecutionMode.BOUNDED_PARALLEL


def test_compiler_deduplicates_queries_and_preserves_dependency_graph():
    completion = _Completion(
        [
            ResearchPlanDraft(
                goal="goal",
                tasks=[
                    ResearchPlanTaskDraft(
                        task_id="a",
                        title="A",
                        query="same query",
                        purpose="one",
                    ),
                    ResearchPlanTaskDraft(
                        task_id="b",
                        title="B",
                        query=" SAME   QUERY ",
                        purpose="two",
                        depends_on=["a"],
                    ),
                ],
            )
        ]
    )

    plan = _planner(completion).create_plan(ResearchRequest(query="goal"), _context())

    assert len(plan.queries) == 1
    assert plan.queries[0].depends_on == []


def test_invalid_cycle_is_repaired_once_then_rejected():
    invalid = ResearchPlanDraft(
        goal="goal",
        tasks=[
            ResearchPlanTaskDraft(
                task_id="a", title="A", query="a", purpose="a", depends_on=["b"]
            ),
            ResearchPlanTaskDraft(
                task_id="b", title="B", query="b", purpose="b", depends_on=["a"]
            ),
        ],
    )
    completion = _Completion([invalid, invalid])

    with pytest.raises(PlanValidationError, match="dependency cycle"):
        _planner(completion).create_plan(ResearchRequest(query="goal"), _context())

    assert completion.calls == 2


def test_search_budget_rejects_too_many_tasks_before_execution():
    draft = ResearchPlanDraft(
        goal="goal",
        tasks=[
            ResearchPlanTaskDraft(title="A", query="a", purpose="a"),
            ResearchPlanTaskDraft(title="B", query="b", purpose="b"),
        ],
    )
    completion = _Completion([draft, draft])

    with pytest.raises(PlanValidationError, match="max_search_calls"):
        _planner(completion, max_search_calls=1).create_plan(
            ResearchRequest(query="goal"), _context()
        )
