from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ResearchContract(BaseModel):
    """Strict, versionable data exchanged inside the research pipeline."""

    model_config = ConfigDict(extra="forbid")


class SourceType(StrEnum):
    OFFICIAL = "official"
    OFFICIAL_DOCS = "official_docs"
    GITHUB_RELEASE = "github_release"
    PAPER = "paper"
    NEWS = "news"
    BLOG = "blog"
    COMMUNITY = "community"
    UNKNOWN = "unknown"


class SupportStatus(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNCLEAR = "unclear"


class ResearchEvidence(BaseModel):
    title: str
    url: str
    snippet: str = ""
    content: str | None = None
    canonical_url: str | None = None
    source_type: SourceType | None = None
    published_at: datetime | None = None
    modified_at: datetime | None = None
    retrieved_at: datetime | None = None
    content_hash: str | None = None


class ResearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)
    as_of: datetime | None = None
    freshness_days: int | None = Field(default=None, ge=0)
    language: str | None = None
    preferred_domains: list[str] = Field(default_factory=list)
    required_source_types: list[SourceType] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    historical_evidence: list[ResearchEvidence] = Field(default_factory=list)
    excluded_urls: list[str] = Field(default_factory=list)

    @field_validator("as_of")
    @classmethod
    def normalize_as_of(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)


class ResearchQuery(ResearchContract):
    query: str
    subtopic: str
    purpose: str
    language: str | None = None
    freshness_days: int | None = Field(default=None, ge=0)
    preferred_domains: list[str] = Field(default_factory=list)


class ResearchPlan(ResearchContract):
    version: str = "1"
    goal: str
    subtopics: list[str] = Field(default_factory=list)
    queries: list[ResearchQuery] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    min_verified_sources: int = Field(default=1, ge=1)
    min_unique_domains: int = Field(default=1, ge=1)
    max_search_calls: int = Field(default=3, ge=1)
    max_fetch_calls: int = Field(default=8, ge=0)


class SearchCandidate(ResearchContract):
    candidate_id: str
    title: str
    url: str
    canonical_url: str | None = None
    snippet: str = ""
    provider: str
    rank: int | None = Field(default=None, ge=1)
    domain: str
    source_type: SourceType = SourceType.UNKNOWN
    published_at: datetime | None = None
    modified_at: datetime | None = None
    retrieved_at: datetime

    @field_validator("published_at", "modified_at", "retrieved_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)

    @field_validator("url", "canonical_url")
    @classmethod
    def validate_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("source URL must be an http(s) URL")
        return value


class PageDocument(ResearchContract):
    document_id: str
    original_url: str
    final_url: str
    canonical_url: str | None = None
    title: str = ""
    content: str
    paragraphs: list[str] = Field(default_factory=list)
    content_hash: str
    content_type: str
    extraction_method: str
    published_at: datetime | None = None
    modified_at: datetime | None = None
    fetched_at: datetime

    @field_validator("published_at", "modified_at", "fetched_at")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return _aware_utc(value)

    @field_validator("original_url", "final_url", "canonical_url")
    @classmethod
    def validate_http_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("document URL must be an http(s) URL")
        return value


class EvidenceCitation(ResearchContract):
    document_id: str
    url: str
    quote: str = Field(min_length=1)
    locator: str | None = None


class EvidenceClaim(ResearchContract):
    claim_id: str
    text: str
    support_status: SupportStatus
    confidence: float = Field(ge=0.0, le=1.0)
    citations: list[EvidenceCitation] = Field(default_factory=list)
    source_count: int = Field(default=0, ge=0)
    independent_domain_count: int = Field(default=0, ge=0)


class EvidencePack(ResearchContract):
    version: str = "1"
    query: str
    plan: ResearchPlan | None = None
    claims: list[EvidenceClaim] = Field(default_factory=list)
    sources: list[SearchCandidate] = Field(default_factory=list)
    coverage: dict[str, bool] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)
    verification_summary: dict[str, int] = Field(default_factory=dict)


def search_candidate_from_legacy(
    item: Any,
    *,
    rank: int | None = None,
    retrieved_at: datetime | None = None,
) -> SearchCandidate:
    """Adapt a legacy SearchItem or mapping without changing its provider."""

    title = _legacy_value(item, "title")
    url = _legacy_value(item, "url")
    snippet = _legacy_value(item, "snippet") or ""
    metadata = _legacy_value(item, "metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    provider = str(metadata.get("provider") or "unknown")
    source_type = _source_type(metadata.get("source_type"))
    received = retrieved_at or datetime.now(UTC)
    domain = (urlsplit(url).hostname or "").casefold()
    candidate_id = _stable_id("candidate", f"{provider}:{url}")
    return SearchCandidate(
        candidate_id=candidate_id,
        title=title,
        url=url,
        snippet=snippet,
        provider=provider,
        rank=rank,
        domain=domain,
        source_type=source_type,
        retrieved_at=received,
    )


def candidate_from_evidence(
    evidence: ResearchEvidence,
    *,
    rank: int | None = None,
    provider: str = "historical",
    retrieved_at: datetime | None = None,
) -> SearchCandidate:
    """Represent historical evidence using the new candidate contract."""

    url = evidence.canonical_url or evidence.url
    return SearchCandidate(
        candidate_id=_stable_id("candidate", f"{provider}:{url}"),
        title=evidence.title,
        url=evidence.url,
        canonical_url=evidence.canonical_url,
        snippet=evidence.snippet,
        provider=provider,
        rank=rank,
        domain=(urlsplit(url).hostname or "").casefold(),
        source_type=evidence.source_type or SourceType.UNKNOWN,
        published_at=evidence.published_at,
        modified_at=evidence.modified_at,
        retrieved_at=evidence.retrieved_at or retrieved_at or datetime.now(UTC),
    )


def _legacy_value(item: Any, field: str) -> Any:
    if isinstance(item, Mapping):
        value = item.get(field)
    else:
        value = getattr(item, field, None)
    if field in {"title", "url"} and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"legacy search item requires a non-empty {field}")
    return value.strip() if isinstance(value, str) else value


def _source_type(value: Any) -> SourceType:
    try:
        return SourceType(str(value))
    except ValueError:
        return SourceType.UNKNOWN


def _stable_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _aware_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime values must include a timezone")
    return value.astimezone(UTC)

