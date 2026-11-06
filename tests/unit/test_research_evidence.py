from datetime import UTC, datetime

import pytest

from enterprise_agent.agents.research.evidence import (
    EvidenceIndex,
    EvidencePackValidationError,
    EvidencePackValidator,
    independent_domain_key,
)
from enterprise_agent.agents.research.schemas import (
    EvidenceCitation,
    EvidenceClaim,
    FreshnessStatus,
    PageDocument,
    SourceAssessment,
    SourceType,
    SupportStatus,
)

_FETCHED_AT = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)


def _document(
    document_id: str = "doc-1",
    *,
    original_url: str = "https://docs.example.com/article",
    final_url: str | None = None,
    canonical_url: str | None = "https://example.com/article",
    content: str = "Title\n\nAgent systems should preserve evidence.",
    paragraphs: list[str] | None = None,
) -> PageDocument:
    return PageDocument(
        document_id=document_id,
        original_url=original_url,
        final_url=final_url or original_url,
        canonical_url=canonical_url,
        title="Title",
        content=content,
        paragraphs=paragraphs or ["Title", "Agent systems should preserve evidence."],
        content_hash=f"hash-{document_id}",
        content_type="text/html",
        extraction_method="html_main_content",
        fetched_at=_FETCHED_AT,
    )


def _assessment(
    candidate_id: str = "candidate-1",
    status: FreshnessStatus = FreshnessStatus.FRESH,
) -> SourceAssessment:
    return SourceAssessment(
        candidate_id=candidate_id,
        dedup_key="https://example.com/article",
        freshness_status=status,
        rank_score=0.9,
    )


def _source(document: PageDocument, candidate_id: str = "candidate-1") -> dict:
    return {
        **document.model_dump(mode="json"),
        "candidate_id": candidate_id,
        "source_type": SourceType.OFFICIAL_DOCS.value,
    }


def test_index_resolves_original_final_and_canonical_urls():
    document = _document(final_url="https://www.example.com/article")
    index = EvidenceIndex.from_documents([document])

    assert index.get(document.document_id) is not None
    assert index.resolve_url(document.original_url) is not None
    assert index.resolve_url(document.final_url).document.document_id == document.document_id
    assert index.resolve_url(document.canonical_url).document.document_id == document.document_id
    assert index.snapshot()["document_count"] == 1


def test_validator_recomputes_counts_and_accepts_exact_paragraph_quote():
    document = _document()
    index = EvidenceIndex.from_sources(
        [_source(document)],
        assessments=[_assessment()],
    )
    claim = EvidenceClaim(
        claim_id="claim-1",
        text="系统保留证据。",
        support_status=SupportStatus.SUPPORTED,
        confidence=0.9,
        citations=[
            EvidenceCitation(
                document_id=document.document_id,
                url=document.canonical_url,
                quote="Agent systems should preserve evidence.",
                locator="paragraph:2",
            )
        ],
        source_count=99,
        independent_domain_count=99,
    )

    report = EvidencePackValidator(index).validate_claims([claim])

    assert report.valid is True
    assert report.validated_claims[0].source_count == 1
    assert report.validated_claims[0].independent_domain_count == 1
    assert report.verification_summary["valid_citation_count"] == 1


def test_validator_rejects_wrong_quote_locator_and_url():
    document = _document()
    index = EvidenceIndex.from_documents([document])
    claim = EvidenceClaim(
        claim_id="claim-1",
        text="unsupported claim",
        support_status=SupportStatus.SUPPORTED,
        confidence=0.5,
        citations=[
            EvidenceCitation(
                document_id=document.document_id,
                url="https://other.example/claim",
                quote="not in document",
                locator="paragraph:4",
            )
        ],
    )

    report = EvidencePackValidator(index).validate_claims([claim])

    assert report.valid is False
    assert report.validated_claims == []
    assert "citation_url_mismatch" in {issue.code for issue in report.issues}


def test_stale_sources_cannot_support_claims_but_unknown_claims_can_remain_unclear():
    document = _document()
    stale_index = EvidenceIndex.from_sources(
        [_source(document)],
        assessments=[_assessment(status=FreshnessStatus.STALE)],
    )
    supported = EvidenceClaim(
        claim_id="claim-stale",
        text="过期来源不应支持结论。",
        support_status=SupportStatus.SUPPORTED,
        confidence=0.7,
        citations=[
            EvidenceCitation(
                document_id=document.document_id,
                url=document.original_url,
                quote="Title",
            )
        ],
    )
    unclear = EvidenceClaim(
        claim_id="claim-unclear",
        text="尚不能确认。",
        support_status=SupportStatus.UNCLEAR,
        confidence=0.3,
    )

    report = EvidencePackValidator(stale_index).validate_claims([supported, unclear])

    assert report.valid is False
    assert [claim.claim_id for claim in report.validated_claims] == ["claim-unclear"]
    assert any(issue.code == "source_not_eligible" for issue in report.issues)


def test_independent_domain_key_collapses_subdomains():
    assert independent_domain_key("https://docs.example.com/a") == "example.com"
    assert independent_domain_key("https://docs.example.co.uk/a") == "example.co.uk"
    assert independent_domain_key("https://192.0.2.10/a") == "192.0.2.10"


def test_build_pack_fails_closed_when_a_claim_has_invalid_citation():
    document = _document()
    index = EvidenceIndex.from_documents([document])
    claim = EvidenceClaim(
        claim_id="claim-1",
        text="invalid",
        support_status=SupportStatus.SUPPORTED,
        confidence=0.5,
        citations=[
            EvidenceCitation(
                document_id="missing",
                url=document.original_url,
                quote="Title",
            )
        ],
    )

    with pytest.raises(EvidencePackValidationError) as exc_info:
        EvidencePackValidator(index).build_pack(query="test", claims=[claim])

    assert exc_info.value.report.valid is False
    assert exc_info.value.report.issues[0].code == "document_not_found"
