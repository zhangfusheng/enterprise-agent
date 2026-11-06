"""Evidence indexing and deterministic citation validation for Research output."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from pydantic import ValidationError

from enterprise_agent.agents.research.ranking import normalize_source_url
from enterprise_agent.agents.research.schemas import (
    EvidenceCitation,
    EvidenceClaim,
    EvidenceIndexEntry,
    EvidencePack,
    EvidenceValidationIssue,
    EvidenceValidationReport,
    FreshnessStatus,
    PageDocument,
    SearchCandidate,
    SourceAssessment,
    SourceType,
)

_MAX_QUOTE_CHARS = 2_000
_LOCATOR_PATTERN = re.compile(r"^(paragraph|offset):([0-9]+)(?:-([0-9]+))?$")
_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "co.uk",
        "com.au",
        "com.cn",
        "com.hk",
        "com.sg",
        "co.jp",
        "co.kr",
        "co.nz",
        "co.za",
        "gov.cn",
        "github.io",
    }
)
_INELIGIBLE_FRESHNESS = frozenset(
    {FreshnessStatus.STALE, FreshnessStatus.FUTURE, FreshnessStatus.INVALID}
)


class EvidenceIndex:
    """Resolve citations against fetched, deduplicated documents only."""

    def __init__(self, entries: Sequence[EvidenceIndexEntry] = ()) -> None:
        self._entries = tuple(entries)
        self._by_document_id: dict[str, EvidenceIndexEntry] = {}
        self._by_url: dict[str, EvidenceIndexEntry] = {}
        for entry in self._entries:
            if entry.document.document_id in self._by_document_id:
                raise ValueError(f"duplicate evidence document_id: {entry.document.document_id}")
            self._by_document_id[entry.document.document_id] = entry
            for url in entry.normalized_urls:
                existing = self._by_url.get(url)
                if existing is not None and existing.document.document_id != entry.document.document_id:
                    raise ValueError(f"evidence URL maps to multiple documents: {url}")
                self._by_url[url] = entry

    @classmethod
    def from_documents(
        cls,
        documents: Sequence[PageDocument | Mapping[str, Any]],
        *,
        assessments: Sequence[SourceAssessment | Mapping[str, Any]] = (),
    ) -> EvidenceIndex:
        assessment_map = _assessment_map(assessments)
        entries: list[EvidenceIndexEntry] = []
        for raw in documents:
            document = _coerce_document(raw)
            if document is None:
                continue
            assessment = assessment_map.get(document.document_id)
            entries.append(
                _entry(
                    document,
                    candidate_id=(assessment.candidate_id if assessment else document.document_id),
                    source_type=SourceType.UNKNOWN,
                    assessment=assessment,
                )
            )
        return cls(entries)

    @classmethod
    def from_sources(
        cls,
        sources: Sequence[Mapping[str, Any]],
        *,
        assessments: Sequence[SourceAssessment | Mapping[str, Any]] = (),
    ) -> EvidenceIndex:
        assessment_map = _assessment_map(assessments)
        entries: list[EvidenceIndexEntry] = []
        for source in sources:
            document = _document_from_source(source)
            if document is None:
                continue
            candidate_id = _text(source.get("candidate_id")) or document.document_id
            assessment = assessment_map.get(candidate_id)
            source_type = _source_type(source.get("source_type"))
            entries.append(
                _entry(
                    document,
                    candidate_id=candidate_id,
                    source_type=source_type,
                    assessment=assessment,
                )
            )
        return cls(entries)

    @property
    def entries(self) -> tuple[EvidenceIndexEntry, ...]:
        return self._entries

    def get(self, document_id: str) -> EvidenceIndexEntry | None:
        return self._by_document_id.get(document_id)

    def resolve_url(self, url: str) -> EvidenceIndexEntry | None:
        return self._by_url.get(normalize_source_url(url))

    def snapshot(self) -> dict[str, Any]:
        return {
            "document_count": len(self._entries),
            "documents": [
                {
                    "document_id": entry.document.document_id,
                    "candidate_id": entry.candidate_id,
                    "urls": list(entry.normalized_urls),
                    "content_hash": entry.document.content_hash,
                    "domain_key": entry.domain_key,
                    "source_type": entry.source_type.value,
                    "freshness_status": entry.freshness_status.value,
                    "eligible_for_citation": entry.eligible_for_citation,
                }
                for entry in self._entries
            ],
        }


class EvidencePackValidationError(ValueError):
    """Raised when a pack cannot be safely published as verified evidence."""

    def __init__(self, report: EvidenceValidationReport) -> None:
        super().__init__("evidence pack citation validation failed")
        self.report = report


class EvidencePackValidator:
    """Perform structural citation checks without asking an LLM to verify them."""

    def __init__(self, index: EvidenceIndex) -> None:
        self.index = index

    def validate_claims(
        self,
        claims: Sequence[EvidenceClaim],
    ) -> EvidenceValidationReport:
        validated: list[EvidenceClaim] = []
        issues: list[EvidenceValidationIssue] = []
        citation_count = 0
        valid_citation_count = 0
        source_ids: set[str] = set()
        domain_keys: set[str] = set()

        for claim in claims:
            claim_issues: list[EvidenceValidationIssue] = []
            if not claim.text.strip():
                claim_issues.append(
                    _issue("empty_claim", "claim text must not be blank", claim.claim_id)
                )
            valid_entries: list[EvidenceIndexEntry] = []
            citation_count += len(claim.citations)
            for citation_index, citation in enumerate(claim.citations):
                entry = self._validate_citation(
                    claim.claim_id,
                    citation_index,
                    citation,
                    claim_issues,
                )
                if entry is not None:
                    valid_entries.append(entry)
                    valid_citation_count += 1

            if claim.support_status.value in {"supported", "contradicted"} and not valid_entries:
                claim_issues.append(
                    _issue(
                        "missing_valid_citation",
                        "supported or contradicted claims require at least one valid citation",
                        claim.claim_id,
                    )
                )
            if claim_issues:
                issues.extend(claim_issues)
                continue

            unique_entries = {
                entry.document.document_id: entry
                for entry in valid_entries
            }
            claim_source_ids = set(unique_entries)
            claim_domains = {entry.domain_key for entry in unique_entries.values()}
            source_ids.update(claim_source_ids)
            domain_keys.update(claim_domains)
            validated.append(
                claim.model_copy(
                    update={
                        "citations": list(claim.citations),
                        "source_count": len(claim_source_ids),
                        "independent_domain_count": len(claim_domains),
                    }
                )
            )

        summary = {
            "claim_count": len(claims),
            "validated_claim_count": len(validated),
            "invalid_claim_count": len(claims) - len(validated),
            "citation_count": citation_count,
            "valid_citation_count": valid_citation_count,
            "source_count": len(source_ids),
            "independent_domain_count": len(domain_keys),
            "issue_count": len(issues),
        }
        return EvidenceValidationReport(
            valid=not issues,
            validated_claims=validated,
            issues=issues,
            verification_summary=summary,
        )

    def validate_pack(self, pack: EvidencePack) -> EvidenceValidationReport:
        return self.validate_claims(pack.claims)

    def build_pack(
        self,
        *,
        query: str,
        claims: Sequence[EvidenceClaim],
        sources: Sequence[SearchCandidate] = (),
        plan: Any | None = None,
        coverage: Mapping[str, bool] | None = None,
        unresolved_questions: Sequence[str] = (),
    ) -> EvidencePack:
        report = self.validate_claims(claims)
        if not report.valid:
            raise EvidencePackValidationError(report)
        return EvidencePack(
            query=query,
            plan=plan,
            claims=report.validated_claims,
            sources=list(sources),
            coverage=dict(coverage or {}),
            unresolved_questions=list(unresolved_questions),
            verification_summary=dict(report.verification_summary),
        )

    def _validate_citation(
        self,
        claim_id: str,
        citation_index: int,
        citation: EvidenceCitation,
        issues: list[EvidenceValidationIssue],
    ) -> EvidenceIndexEntry | None:
        entry = self.index.get(citation.document_id)
        if entry is None:
            issues.append(
                _issue(
                    "document_not_found",
                    f"citation document does not exist: {citation.document_id}",
                    claim_id,
                    citation_index,
                )
            )
            return None
        if not entry.eligible_for_citation:
            issues.append(
                _issue(
                    "source_not_eligible",
                    f"source freshness status is not eligible: {entry.freshness_status.value}",
                    claim_id,
                    citation_index,
                )
            )
            return None
        if normalize_source_url(citation.url) not in entry.normalized_urls:
            issues.append(
                _issue(
                    "citation_url_mismatch",
                    "citation URL does not match the indexed document",
                    claim_id,
                    citation_index,
                )
            )
            return None
        quote = citation.quote
        if len(quote) > _MAX_QUOTE_CHARS:
            issues.append(
                _issue(
                    "quote_too_long",
                    f"citation quote exceeds {_MAX_QUOTE_CHARS} characters",
                    claim_id,
                    citation_index,
                )
            )
            return None
        if quote not in entry.document.content:
            issues.append(
                _issue(
                    "quote_not_found",
                    "citation quote does not occur verbatim in the document",
                    claim_id,
                    citation_index,
                )
            )
            return None
        if citation.locator is not None and not _locator_matches(citation, entry.document):
            issues.append(
                _issue(
                    "locator_mismatch",
                    "citation locator does not identify the quoted text",
                    claim_id,
                    citation_index,
                )
            )
            return None
        return entry


def independent_domain_key(url: str) -> str:
    """Return a stable organization-level domain key for source independence."""

    try:
        hostname = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    if not hostname:
        return ""
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        suffix = ".".join(labels[-2:]) if len(labels) >= 2 else hostname
        if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
            suffix = ".".join(labels[-3:])
        return suffix
    return hostname


def _entry(
    document: PageDocument,
    *,
    candidate_id: str,
    source_type: SourceType,
    assessment: SourceAssessment | None,
) -> EvidenceIndexEntry:
    freshness_status = assessment.freshness_status if assessment else FreshnessStatus.UNKNOWN
    return EvidenceIndexEntry(
        document=document,
        candidate_id=candidate_id,
        normalized_urls=_document_urls(document),
        domain_key=independent_domain_key(document.canonical_url or document.final_url),
        source_type=source_type,
        freshness_status=freshness_status,
        eligible_for_citation=freshness_status not in _INELIGIBLE_FRESHNESS,
    )


def _assessment_map(
    assessments: Sequence[SourceAssessment | Mapping[str, Any]],
) -> dict[str, SourceAssessment]:
    result: dict[str, SourceAssessment] = {}
    for item in assessments:
        try:
            assessment = item if isinstance(item, SourceAssessment) else SourceAssessment.model_validate(item)
        except ValidationError:
            continue
        result[assessment.candidate_id] = assessment
    return result


def _coerce_document(raw: PageDocument | Mapping[str, Any]) -> PageDocument | None:
    if isinstance(raw, PageDocument):
        return raw
    try:
        return PageDocument.model_validate(raw)
    except ValidationError:
        return None


def _document_from_source(source: Mapping[str, Any]) -> PageDocument | None:
    raw_document = source.get("document")
    if isinstance(raw_document, Mapping):
        return _coerce_document(raw_document)
    required = (
        "document_id",
        "content",
        "content_hash",
        "content_type",
        "extraction_method",
        "fetched_at",
    )
    if any(field not in source for field in required):
        return None
    payload = {
        "document_id": source["document_id"],
        "original_url": source.get("original_url") or source.get("url"),
        "final_url": source.get("final_url") or source.get("url"),
        "canonical_url": source.get("canonical_url"),
        "title": source.get("title", ""),
        "content": source["content"],
        "paragraphs": source.get("paragraphs", []),
        "content_hash": source["content_hash"],
        "content_type": source["content_type"],
        "extraction_method": source["extraction_method"],
        "published_at": source.get("published_at"),
        "modified_at": source.get("modified_at"),
        "fetched_at": source["fetched_at"],
    }
    return _coerce_document(payload)


def _document_urls(document: PageDocument) -> list[str]:
    values = [document.original_url, document.final_url, document.canonical_url]
    return list(
        dict.fromkeys(
            normalize_source_url(value)
            for value in values
            if isinstance(value, str) and value.strip()
        )
    )


def _locator_matches(citation: EvidenceCitation, document: PageDocument) -> bool:
    if citation.locator is None:
        return True
    match = _LOCATOR_PATTERN.fullmatch(citation.locator)
    if match is None:
        return False
    kind, first, second = match.groups()
    start = int(first)
    if kind == "paragraph":
        if second is not None or start < 1 or start > len(document.paragraphs):
            return False
        return citation.quote in document.paragraphs[start - 1]
    end = int(second) if second is not None else start + len(citation.quote)
    return 0 <= start < end <= len(document.content) and document.content[start:end] == citation.quote


def _issue(
    code: str,
    message: str,
    claim_id: str,
    citation_index: int | None = None,
) -> EvidenceValidationIssue:
    return EvidenceValidationIssue(
        code=code,
        message=message,
        claim_id=claim_id,
        citation_index=citation_index,
    )


def _source_type(value: object) -> SourceType:
    try:
        return SourceType(str(value))
    except ValueError:
        return SourceType.UNKNOWN


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


__all__ = [
    "EvidenceIndex",
    "EvidencePackValidationError",
    "EvidencePackValidator",
    "independent_domain_key",
]
