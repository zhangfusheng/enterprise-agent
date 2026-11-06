from datetime import UTC, datetime
from pathlib import Path

from langchain_core.messages import AIMessage
from tests.support.harness import offline_harness
from tests.support.scripted import ScriptedChatModel, tool_call

from enterprise_agent.agents.analysis.definition import AnalysisAgent, _prompt
from enterprise_agent.agents.analysis.schemas import AnalysisRequest, ResearchEvidenceBundle
from enterprise_agent.agents.harness.roles import ANALYSIS
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import ResultStatus
from enterprise_agent.tools.save_report import ReportStore

_DOCUMENT = {
    "document_id": "doc-1",
    "url": "https://docs.example.com/article",
    "original_url": "https://docs.example.com/article",
    "final_url": "https://docs.example.com/article",
    "canonical_url": "https://example.com/article",
    "title": "Title",
    "content": "Agent systems should preserve evidence.",
    "paragraphs": ["Agent systems should preserve evidence."],
    "content_hash": "hash-1",
    "content_type": "text/html",
    "extraction_method": "html_main_content",
    "fetched_at": datetime(2026, 11, 6, tzinfo=UTC).isoformat(),
    "candidate_id": "candidate-1",
    "provider": "tavily",
    "domain": "docs.example.com",
    "source_type": "official_docs",
    "snippet": "evidence",
}


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="analysis-run",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_analysis",
        parent_agent_id="agent_supervisor",
        workspace_root=".",
        allowed_tools={"calculate", "submit_claims", "save_report"},
        metadata={"role": "analysis", "scopes": {"report"}},
    )


def _agent(tmp_path: Path, steps: list) -> AnalysisAgent:
    harness = offline_harness(ANALYSIS)
    model = ScriptedChatModel(steps=steps)
    harness.models.chat_model_for_role = lambda role, model=model: model
    return AnalysisAgent(ReportStore(tmp_path), harness=harness)


def _request(quote: str) -> AnalysisRequest:
    return AnalysisRequest(
        objective="分析研究结果",
        research_evidence=ResearchEvidenceBundle(
            query="evidence",
            sources=[_DOCUMENT],
        ),
    )


def _claim(quote: str) -> dict:
    return {
        "claim_id": "claim-1",
        "text": "系统应保留证据。",
        "support_status": "supported",
        "confidence": 0.9,
        "citations": [
            {
                "document_id": "doc-1",
                "url": "https://example.com/article",
                "quote": quote,
                "locator": "paragraph:1",
            }
        ],
    }


def test_research_analysis_requires_validated_claims_before_save(tmp_path: Path):
    result = _agent(
        tmp_path,
        [
            tool_call(
                "submit_claims",
                {"claims": [_claim("Agent systems should preserve evidence.")]},
                "c1",
            ),
            tool_call("save_report", {"markdown": "# Report"}, "s1"),
            AIMessage(content="分析完成"),
        ],
    ).run(_request("Agent systems should preserve evidence."), _context())

    assert result.status == ResultStatus.SUCCESS
    pack = result.structured_data["evidence_pack"]
    assert pack["claims"][0]["source_count"] == 1
    assert pack["claims"][0]["independent_domain_count"] == 1
    assert result.structured_data["citation_validation"]["valid"] is True
    assert result.artifact_refs


def test_research_analysis_accepts_legacy_title_citation_and_saves(tmp_path: Path):
    legacy_claim = _claim("Agent systems should preserve evidence.")
    legacy_claim["citations"][0]["document_id"] = "Title"
    result = _agent(
        tmp_path,
        [
            tool_call("submit_claims", {"claims": [legacy_claim]}, "c1"),
            tool_call("save_report", {"markdown": "# Report"}, "s1"),
            AIMessage(content="分析完成"),
        ],
    ).run(_request("Agent systems should preserve evidence."), _context())

    assert result.status == ResultStatus.SUCCESS
    assert result.artifact_refs
    assert result.structured_data["citation_validation"]["valid"] is True
    assert (
        result.structured_data["evidence_pack"]["claims"][0]["citations"][0]["document_id"]
        == "doc-1"
    )


def test_research_prompt_exposes_document_id_manifest():
    prompt = _prompt("分析研究结果", [_DOCUMENT], require_claims=True)

    assert "可引用文档索引" in prompt
    assert '"document_id": "doc-1"' in prompt
    assert "title 不能代替 document_id" in prompt


def test_invalid_claim_citation_blocks_report_save(tmp_path: Path):
    result = _agent(
        tmp_path,
        [
            tool_call("submit_claims", {"claims": [_claim("not in document")]}, "c1"),
            tool_call("save_report", {"markdown": "# Report"}, "s1"),
            AIMessage(content="无法完成"),
        ],
    ).run(_request("not in document"), _context())

    assert result.status == ResultStatus.FAILED
    assert result.artifact_refs == []
    assert result.structured_data["evidence_pack"] is None
    assert result.structured_data["citation_validation"]["valid"] is False
    assert any("逐字" not in error for error in result.errors)
