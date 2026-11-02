from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from enterprise_agent.agents.research.schemas import (
    EvidenceCitation,
    EvidenceClaim,
    EvidencePack,
    ResearchEvidence,
    ResearchPlan,
    ResearchQuery,
    ResearchRequest,
    SearchCandidate,
    SourceType,
    SupportStatus,
    candidate_from_evidence,
    search_candidate_from_legacy,
)
from enterprise_agent.tools.web.provider import SearchItem

_NOW = datetime(2026, 11, 2, 0, 0, tzinfo=UTC)


def test_existing_research_request_shape_remains_compatible():
    request = ResearchRequest(query="最新 Agent 信息")

    assert request.query == "最新 Agent 信息"
    assert request.preferred_domains == []
    assert request.required_source_types == []


def test_research_plan_contains_query_purposes_and_limits():
    plan = ResearchPlan(
        goal="查找最新 Agent 信息",
        subtopics=["LangGraph"],
        queries=[
            ResearchQuery(
                query="LangGraph latest release",
                subtopic="LangGraph",
                purpose="查找官方版本变化",
            )
        ],
        min_verified_sources=2,
        min_unique_domains=2,
    )

    payload = plan.model_dump(mode="json")

    assert payload["version"] == "1"
    assert payload["queries"][0]["purpose"] == "查找官方版本变化"
    assert payload["min_unique_domains"] == 2


def test_legacy_search_item_maps_to_candidate_with_stable_identity():
    item = SearchItem(
        title="Official release",
        url="https://example.com/releases/1",
        snippet="A release note",
        metadata={"provider": "legacy", "source_type": "official"},
    )

    first = search_candidate_from_legacy(item, rank=1, retrieved_at=_NOW)
    second = search_candidate_from_legacy(item, rank=1, retrieved_at=_NOW)

    assert first == second
    assert first.candidate_id.startswith("candidate-")
    assert first.domain == "example.com"
    assert first.provider == "legacy"
    assert first.source_type == SourceType.OFFICIAL
    assert first.retrieved_at == _NOW


def test_historical_evidence_maps_to_candidate():
    evidence = ResearchEvidence(
        title="Historical release",
        url="https://example.com/release",
        snippet="Old release",
        canonical_url="https://example.com/release",
        source_type=SourceType.OFFICIAL,
        retrieved_at=_NOW,
    )

    candidate = candidate_from_evidence(evidence)

    assert candidate.provider == "historical"
    assert candidate.source_type == SourceType.OFFICIAL
    assert candidate.domain == "example.com"
    assert candidate.retrieved_at == _NOW


def test_contracts_require_timezone_aware_timestamps():
    with pytest.raises(ValidationError, match="timezone"):
        SearchCandidate(
            candidate_id="candidate-1",
            title="Source",
            url="https://example.com",
            provider="test",
            domain="example.com",
            retrieved_at=datetime(2026, 11, 2),
        )


def test_evidence_pack_serializes_claims_and_citations():
    pack = EvidencePack(
        query="最新 Agent 信息",
        claims=[
            EvidenceClaim(
                claim_id="claim-1",
                text="项目发布了新版本",
                support_status=SupportStatus.SUPPORTED,
                confidence=0.9,
                citations=[
                    EvidenceCitation(
                        document_id="doc-1",
                        url="https://example.com/release",
                        quote="Release notes",
                        locator="paragraph:2",
                    )
                ],
                source_count=1,
                independent_domain_count=1,
            )
        ],
    )

    payload = pack.model_dump(mode="json")

    assert payload["claims"][0]["support_status"] == "supported"
    assert payload["claims"][0]["citations"][0]["locator"] == "paragraph:2"


def test_strict_research_contract_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        ResearchPlan(goal="test", unexpected="not allowed")
