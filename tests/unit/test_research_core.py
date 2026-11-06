from datetime import UTC, datetime

from enterprise_agent.agents.research.core import ResearchCore, merge_evidence, outcome
from enterprise_agent.agents.research.planning import ResearchPlanner
from enterprise_agent.agents.research.schemas import (
    ResearchEvidence,
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
        run_id="core-run",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_research",
        workspace_root=".",
    )


class _PlanCompletion:
    def __init__(self, draft: ResearchPlanDraft) -> None:
        self.draft = draft

    def complete(self, messages, *, request):
        del messages, request
        return self.draft


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
                    title="source",
                    url=f"https://example.com/{len(self.calls)}",
                    snippet=request.query,
                    provider=self.name,
                    rank=1,
                    domain="example.com",
                    retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
                ),
            ),
        )


def _core(provider: _Search, draft: ResearchPlanDraft) -> ResearchCore:
    planner = ResearchPlanner(
        _PlanCompletion(draft),
        max_search_calls=3,
        max_fetch_calls=0,
        min_verified_sources=1,
        max_candidate_sources=3,
    )
    return ResearchCore(
        provider,
        planner=planner,
        loop_policy=ResearchLoopPolicy(require_fetch=False, min_usable_sources=1),
    )


def test_core_generates_plan_before_search_and_executes_only_planned_queries():
    provider = _Search()
    draft = ResearchPlanDraft(
        goal="研究幂等性",
        tasks=[
            ResearchPlanTaskDraft(
                task_id="a",
                title="定义",
                query="幂等性定义",
                purpose="获取定义",
            ),
            ResearchPlanTaskDraft(
                task_id="b",
                title="实践",
                query="幂等性实践",
                purpose="获取实践",
            ),
        ],
    )

    result = _core(provider, draft).run(ResearchRequest(query="研究幂等性"), _context())

    assert result.status == ResultStatus.SUCCESS
    assert sorted(provider.calls) == ["幂等性定义", "幂等性实践"]
    assert result.structured_data["plan"]["queries"][0]["query_id"] == "query-1"
    assert result.structured_data["research"]["planned_query_count"] == 2


def test_core_rejects_invalid_plan_without_calling_provider():
    provider = _Search()
    draft = ResearchPlanDraft(
        goal="研究",
        tasks=[
            ResearchPlanTaskDraft(
                task_id="a",
                title="循环",
                query="循环",
                purpose="验证循环",
                depends_on=["b"],
            ),
            ResearchPlanTaskDraft(
                task_id="b",
                title="循环二",
                query="循环二",
                purpose="验证循环",
                depends_on=["a"],
            ),
        ],
    )

    result = _core(provider, draft).run(ResearchRequest(query="研究"), _context())

    assert result.status == ResultStatus.FAILED
    assert provider.calls == []
    assert result.structured_data["research"]["plan_status"] == "failed"


def test_dependent_queries_are_executed_in_dependency_order():
    provider = _Search()
    draft = ResearchPlanDraft(
        goal="研究依赖",
        tasks=[
            ResearchPlanTaskDraft(
                task_id="first",
                title="第一步",
                query="第一步查询",
                purpose="先收集基础信息",
            ),
            ResearchPlanTaskDraft(
                task_id="second",
                title="第二步",
                query="第二步查询",
                purpose="在基础信息之后继续研究",
                depends_on=["first"],
            ),
        ],
    )

    result = _core(provider, draft).run(ResearchRequest(query="研究依赖"), _context())

    assert result.status == ResultStatus.SUCCESS
    assert provider.calls == ["第一步查询", "第二步查询"]


def test_core_merges_historical_evidence_and_deduplicates_urls():
    historical = [ResearchEvidence(title="old", url="https://example.com/a")]
    merged = merge_evidence(
        historical,
        [
            {"title": "duplicate", "url": "https://example.com/a"},
            {"title": "new", "url": "https://example.com/b"},
        ],
    )

    assert [item["url"] for item in merged] == [
        "https://example.com/a",
        "https://example.com/b",
    ]
    assert outcome([], []) == ResultStatus.FAILED
    assert outcome(merged, ["partial failure"]) == ResultStatus.PARTIAL
