from langchain_core.messages import AIMessage
from tests.support.harness import offline_harness
from tests.support.scripted import ScriptedChatModel, tool_call

from enterprise_agent.agents.harness.roles import RESEARCH
from enterprise_agent.agents.research.core import ResearchCore, merge_evidence, outcome
from enterprise_agent.agents.research.schemas import ResearchEvidence, ResearchRequest
from enterprise_agent.agents.research.tools import ResearchLoopPolicy
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import ResultStatus
from enterprise_agent.tools.web.provider import SearchItem


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="core-run",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_research",
        workspace_root=".",
    )


class _Search:
    def search(self, query: str, limit: int = 5) -> list[SearchItem]:
        return [
            SearchItem(
                title="source",
                url="https://example.com/source",
                snippet=query,
                metadata={},
            )
        ][:limit]


def _core(*steps: AIMessage) -> ResearchCore:
    harness = offline_harness(RESEARCH)
    model = ScriptedChatModel(steps=list(steps))
    harness.models.chat_model_for_role = lambda role, model=model: model
    return ResearchCore(
        _Search(),
        harness=harness,
        loop_policy=ResearchLoopPolicy(require_fetch=False),
    )


def test_core_runs_without_research_agent_adapter():
    core = _core(
        tool_call("web_search", {"query": "幂等性", "limit": 3}, "search-1"),
        AIMessage(content="已完成研究"),
    )

    result = core.run(ResearchRequest(query="幂等性", limit=3), _context())

    assert result.status == ResultStatus.SUCCESS
    assert result.summary == "收集到 1 条来源。"
    assert result.sources[0]["url"] == "https://example.com/source"
    assert result.agent_id == "agent_research"


def test_core_merges_historical_evidence_and_exposes_research_metadata():
    core = _core(
        tool_call("web_search", {"query": "新来源", "limit": 3}, "search-1"),
        AIMessage(content="已完成研究"),
    )
    historical = ResearchEvidence(
        title="历史来源",
        url="https://example.com/old",
        snippet="历史证据",
    )

    result = core.run(
        ResearchRequest(
            query="新来源",
            historical_evidence=[historical],
            excluded_urls=[historical.url],
        ),
        _context(),
    )

    assert [item["url"] for item in result.sources] == [
        "https://example.com/old",
        "https://example.com/source",
    ]
    assert result.structured_data["research"]["historical_evidence_count"] == 1
    assert result.structured_data["research"]["new_evidence_count"] == 1


def test_core_semantics_are_independent_of_agent_orchestration():
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
    assert outcome(merged, ["fetch failed"]) == ResultStatus.PARTIAL
