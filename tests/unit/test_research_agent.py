from datetime import UTC, datetime

from enterprise_agent.agents.research.definition import ResearchAgent
from enterprise_agent.agents.research.planning import ResearchPlanner
from enterprise_agent.agents.research.schemas import (
    ResearchPlanDraft,
    ResearchPlanTaskDraft,
    ResearchRequest,
    SearchCandidate,
)
from enterprise_agent.agents.research.tools import ResearchLoopPolicy
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import ResultStatus
from enterprise_agent.tools.web.provider import SearchRequest, SearchResponse


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_research",
        parent_agent_id="agent_supervisor",
        workspace_root=".",
    )


class _Search:
    name = "test"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls.append(request.query)
        return SearchResponse(
            provider=self.name,
            results=(
                SearchCandidate(
                    candidate_id=f"candidate-{len(self.calls)}",
                    title=f"source-{len(self.calls)}",
                    url=f"https://example.com/{len(self.calls)}",
                    snippet=request.query,
                    provider=self.name,
                    rank=1,
                    domain="example.com",
                    retrieved_at=datetime.now(UTC),
                ),
            ),
        )


class _Completion:
    def complete(self, messages, *, request):
        del messages, request
        return ResearchPlanDraft(
            goal="研究目标",
            tasks=[
                ResearchPlanTaskDraft(
                    task_id="one",
                    title="第一子问题",
                    query="第一个查询",
                    purpose="收集第一类来源",
                ),
                ResearchPlanTaskDraft(
                    task_id="two",
                    title="第二子问题",
                    query="第二个查询",
                    purpose="收集第二类来源",
                ),
            ],
        )


def _agent(provider: _Search) -> ResearchAgent:
    planner = ResearchPlanner(
        _Completion(),
        max_search_calls=2,
        max_fetch_calls=0,
        min_verified_sources=1,
        max_candidate_sources=4,
        max_concurrency=2,
    )
    return ResearchAgent(
        provider,
        planner=planner,
        loop_policy=ResearchLoopPolicy(
            max_search_calls=2,
            max_fetch_calls=0,
            min_usable_sources=1,
            max_candidate_sources=4,
            require_fetch=False,
        ),
    )


def test_agent_executes_compiled_plan_without_model_tool_loop():
    provider = _Search()

    result = _agent(provider).run(ResearchRequest(query="研究目标"), _context())

    assert result.status == ResultStatus.SUCCESS
    assert sorted(provider.calls) == ["第一个查询", "第二个查询"]
    assert result.structured_data["research"]["plan_status"] == "compiled"
    assert result.structured_data["research"]["planned_query_count"] == 2


def test_agent_does_not_search_when_plan_generation_fails():
    class _BrokenCompletion:
        def complete(self, messages, *, request):
            del messages, request
            raise RuntimeError("planner unavailable")

    provider = _Search()
    planner = ResearchPlanner(
        _BrokenCompletion(),
        max_search_calls=2,
        max_fetch_calls=0,
        min_verified_sources=1,
        max_candidate_sources=2,
    )
    agent = ResearchAgent(
        provider,
        planner=planner,
        loop_policy=ResearchLoopPolicy(require_fetch=False),
    )

    result = agent.run(ResearchRequest(query="研究目标"), _context())

    assert result.status == ResultStatus.FAILED
    assert provider.calls == []
    assert result.structured_data["research"]["plan_status"] == "failed"
