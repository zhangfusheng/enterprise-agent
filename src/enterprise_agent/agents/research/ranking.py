"""Deterministic source ranking, deduplication, and freshness evaluation."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from enterprise_agent.agents.research.schemas import (
    FreshnessStatus,
    SourceAssessment,
    SourceType,
)

RANKING_VERSION = "research-ranking-v1"

_TRACKING_PARAMETER_NAMES = frozenset({"fbclid", "gclid", "msclkid"})
_RANK_WEIGHTS = {
    "relevance": 0.55,
    "freshness": 0.20,
    "preferred_domain": 0.10,
    "source_type": 0.05,
    "content_quality": 0.10,
}


@dataclass(frozen=True, slots=True)
class FreshnessEvaluation:
    status: FreshnessStatus
    effective_at: datetime | None
    age_days: float | None


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    sources: tuple[dict[str, Any], ...]
    duplicates: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class RankedSource:
    source: dict[str, Any]
    assessment: SourceAssessment


def normalize_source_url(value: str) -> str:
    """Normalize URL identity without merging potentially different resources."""

    raw = value.strip()
    if not raw:
        return raw
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return raw.casefold().rstrip("/")
        host = parsed.hostname.casefold().rstrip(".")
        try:
            port = parsed.port
        except ValueError:
            return raw.casefold().rstrip("/")
        if port is not None and not _is_default_port(parsed.scheme, port):
            host = f"{host}:{port}"
        if ":" in parsed.hostname and not host.startswith("["):
            host = f"[{host}]"
        path = parsed.path.rstrip("/") or "/"
        query = _normalize_query_string(parsed.query)
        return urlunsplit((parsed.scheme.casefold(), host, path, query, ""))
    except ValueError:
        return raw.casefold().rstrip("/")


def deduplicate_sources(
    sources: Sequence[Mapping[str, Any]],
    *,
    preferred_domains: Sequence[str] = (),
    required_source_types: Sequence[SourceType | str] = (),
    min_content_length: int = 80,
) -> DeduplicationResult:
    """Collapse URL aliases and identical fetched documents deterministically."""

    unique: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    for raw in sources:
        source = dict(raw)
        candidate_id = _candidate_id(source)
        source["candidate_id"] = candidate_id
        aliases = _source_url_aliases(source)
        content_key = _content_key(source)
        keys = [*(f"url:{item}" for item in aliases)]
        if content_key is not None:
            keys.append(f"content:{content_key}")
        matched = [
            index
            for index, existing in enumerate(unique)
            if _source_matches(existing, keys)
        ]
        if not matched:
            unique.append(source)
            continue

        group = [unique[index] for index in matched]
        group.append(source)
        winner = max(
            group,
            key=lambda item: _representative_key(
                item,
                preferred_domains=preferred_domains,
                required_source_types=required_source_types,
                min_content_length=min_content_length,
            ),
        )
        merged = _merge_source_metadata(winner, group)
        insert_at = min(matched)
        unique = [item for index, item in enumerate(unique) if index not in set(matched)]
        unique.insert(insert_at, merged)
        winner_id = _candidate_id(merged)
        for item in group:
            item_id = _candidate_id(item)
            if item_id == winner_id:
                continue
            duplicates.append(
                {
                    "candidate_id": item_id,
                    "duplicate_of": winner_id,
                    "reason": _duplicate_reason(item, merged),
                    "url": item.get("url", ""),
                    "alternate_urls": _source_url_aliases(item),
                }
            )

    return DeduplicationResult(tuple(unique), tuple(duplicates))


def evaluate_freshness(
    source: Mapping[str, Any],
    *,
    as_of: datetime,
    freshness_days: int | None,
) -> FreshnessEvaluation:
    """Evaluate a source date using published then modified date precedence."""

    reference = _aware_utc(as_of)
    raw_date = next(
        (
            source.get(field)
            for field in ("published_at", "modified_at")
            if source.get(field) is not None
        ),
        None,
    )
    if raw_date is None:
        return FreshnessEvaluation(FreshnessStatus.UNKNOWN, None, None)
    effective_at = _parse_datetime(raw_date)
    if effective_at is None:
        return FreshnessEvaluation(FreshnessStatus.INVALID, None, None)
    if effective_at > reference:
        return FreshnessEvaluation(FreshnessStatus.FUTURE, effective_at, None)
    age_days = round((reference - effective_at).total_seconds() / 86_400, 6)
    if freshness_days is None or age_days <= freshness_days:
        status = FreshnessStatus.FRESH
    else:
        status = FreshnessStatus.STALE
    return FreshnessEvaluation(status, effective_at, age_days)


def rank_sources(
    sources: Sequence[Mapping[str, Any]],
    *,
    preferred_domains: Sequence[str] = (),
    required_source_types: Sequence[SourceType | str] = (),
    freshness_days: int | None = None,
    as_of: datetime | None = None,
    min_content_length: int = 80,
) -> list[RankedSource]:
    """Return stable source order and an auditable score breakdown."""

    reference = _aware_utc(as_of or datetime.now(UTC))
    ranked: list[RankedSource] = []
    for raw in sources:
        source = dict(raw)
        candidate_id = _candidate_id(source)
        source["candidate_id"] = candidate_id
        freshness = evaluate_freshness(
            source,
            as_of=reference,
            freshness_days=freshness_days,
        )
        breakdown = {
            "relevance": _relevance_score(source),
            "freshness": _freshness_score(freshness.status),
            "preferred_domain": _preferred_domain_score(source, preferred_domains),
            "source_type": _source_type_score(source, required_source_types),
            "content_quality": _content_quality_score(source, min_content_length),
        }
        score = _weighted_score(breakdown)
        exclusion_reason = {
            FreshnessStatus.STALE: "stale",
            FreshnessStatus.FUTURE: "future_date",
            FreshnessStatus.INVALID: "invalid_date",
        }.get(freshness.status)
        assessment = SourceAssessment(
            candidate_id=candidate_id,
            dedup_key=_dedup_key(source),
            freshness_status=freshness.status,
            effective_at=freshness.effective_at,
            age_days=freshness.age_days,
            rank_score=score,
            score_breakdown=breakdown,
            exclusion_reason=exclusion_reason,
            query_ids=_string_list(source.get("query_ids")),
            alternate_urls=_string_list(source.get("alternate_urls")),
        )
        ranked.append(RankedSource(source=source, assessment=assessment))

    ranked.sort(
        key=lambda item: (
            -item.assessment.rank_score,
            -item.assessment.score_breakdown["preferred_domain"],
            -item.assessment.score_breakdown["freshness"],
            -item.assessment.score_breakdown["content_quality"],
            str(item.source.get("domain", "")).casefold(),
            _candidate_id(item.source),
            normalize_source_url(str(item.source.get("url", ""))),
        )
    )
    return ranked


def evidence_is_eligible(
    assessment: SourceAssessment,
    *,
    freshness_required: bool,
) -> bool:
    """Exclude invalid temporal evidence while retaining unknown dates for diagnostics."""

    if not freshness_required:
        return True
    return assessment.freshness_status not in {
        FreshnessStatus.STALE,
        FreshnessStatus.FUTURE,
        FreshnessStatus.INVALID,
    }


def freshness_counts(assessments: Sequence[SourceAssessment]) -> dict[str, int]:
    counts = {status.value: 0 for status in FreshnessStatus}
    for assessment in assessments:
        counts[assessment.freshness_status.value] += 1
    return counts


def _normalize_query_string(value: str) -> str:
    pairs = [
        (key, item)
        for key, item in parse_qsl(value, keep_blank_values=True)
        if not _is_tracking_parameter(key)
    ]
    pairs.sort(key=lambda item: (item[0].casefold(), item[1]))
    return urlencode(pairs, doseq=True)


def _is_tracking_parameter(value: str) -> bool:
    normalized = value.casefold()
    return normalized.startswith("utm_") or normalized in _TRACKING_PARAMETER_NAMES


def _is_default_port(scheme: str, port: int) -> bool:
    return (scheme.casefold() == "http" and port == 80) or (
        scheme.casefold() == "https" and port == 443
    )


def _source_url_aliases(source: Mapping[str, Any]) -> list[str]:
    aliases: list[str] = []
    for field in ("url", "canonical_url", "final_url"):
        value = source.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        normalized = normalize_source_url(value)
        if normalized and normalized not in aliases:
            aliases.append(normalized)
    return aliases


def _content_key(source: Mapping[str, Any]) -> str | None:
    content_hash = source.get("content_hash")
    if isinstance(content_hash, str) and content_hash.strip():
        return content_hash.strip().casefold()
    content = source.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    normalized = " ".join(content.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _source_matches(source: Mapping[str, Any], keys: Sequence[str]) -> bool:
    source_keys = {*(f"url:{item}" for item in _source_url_aliases(source))}
    content_key = _content_key(source)
    if content_key is not None:
        source_keys.add(f"content:{content_key}")
    return bool(source_keys.intersection(keys))


def _merge_source_metadata(
    representative: Mapping[str, Any],
    group: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    merged = dict(representative)
    candidate_ids = _ordered_unique(
        [
            candidate_id
            for item in group
            for candidate_id in [*_string_list(item.get("candidate_ids")), _candidate_id(item)]
        ]
    )
    query_ids = _ordered_unique(
        [query_id for item in group for query_id in _string_list(item.get("query_ids"))]
    )
    providers = _ordered_unique(
        [provider for item in group for provider in _string_list(item.get("provider_hits"))]
        + [
            str(item["provider"])
            for item in group
            if isinstance(item.get("provider"), str) and item["provider"].strip()
        ]
    )
    alternate_urls = _ordered_unique(
        [
            url
            for item in group
            for url in [*_string_list(item.get("alternate_urls")), *_source_url_aliases(item)]
        ]
    )
    primary_url = normalize_source_url(str(merged.get("url", "")))
    merged["candidate_ids"] = candidate_ids
    merged["query_ids"] = query_ids
    merged["provider_hits"] = providers
    merged["alternate_urls"] = [url for url in alternate_urls if url != primary_url]
    merged["duplicate_count"] = max(
        0,
        sum(int(item.get("duplicate_count", 0)) for item in group if _is_int(item.get("duplicate_count")))
        + len(group)
        - 1,
    )
    for field in ("content", "content_hash", "document_id", "canonical_url", "final_url"):
        if merged.get(field) in (None, ""):
            for item in group:
                if item.get(field) not in (None, ""):
                    merged[field] = item[field]
                    break
    return merged


def _representative_key(
    source: Mapping[str, Any],
    *,
    preferred_domains: Sequence[str],
    required_source_types: Sequence[SourceType | str],
    min_content_length: int,
) -> tuple[float, ...] | tuple[int, ...] | tuple[Any, ...]:
    rank = source.get("rank")
    rank_score = 1 / math.log2(float(rank) + 1) if isinstance(rank, int) and rank > 0 else 0.0
    return (
        float(_content_quality_score(source, min_content_length) > 0),
        _preferred_domain_score(source, preferred_domains),
        _source_type_score(source, required_source_types),
        float(_date_available(source)),
        _relevance_score(source),
        rank_score,
        -float(len(normalize_source_url(str(source.get("url", ""))))),
        _candidate_id(source),
    )


def _duplicate_reason(item: Mapping[str, Any], representative: Mapping[str, Any]) -> str:
    item_aliases = set(_source_url_aliases(item))
    representative_aliases = set(_source_url_aliases(representative))
    if item_aliases.intersection(representative_aliases):
        representative_canonical = normalize_source_url(
            str(representative.get("canonical_url", ""))
        )
        if representative_canonical in item_aliases and representative_canonical:
            return "canonical_url"
        representative_final = normalize_source_url(str(representative.get("final_url", "")))
        if representative_final in item_aliases and representative_final:
            return "final_url"
        return "url"
    if _content_key(item) is not None and _content_key(item) == _content_key(representative):
        return "content_hash"
    return "source_alias"


def _candidate_id(source: Mapping[str, Any]) -> str:
    value = source.get("candidate_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    url = normalize_source_url(str(source.get("url", "")))
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return f"candidate-{digest}"


def _dedup_key(source: Mapping[str, Any]) -> str:
    for field in ("canonical_url", "final_url", "url"):
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            return normalize_source_url(value)
    content_key = _content_key(source)
    return f"content:{content_key}" if content_key else f"candidate:{_candidate_id(source)}"


def _relevance_score(source: Mapping[str, Any]) -> float:
    score = source.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool) and 0 <= score <= 1:
        return round(float(score), 6)
    rank = source.get("rank")
    if isinstance(rank, int) and rank > 0:
        return round(min(1.0, 1 / math.log2(rank + 1)), 6)
    return 0.2


def _freshness_score(status: FreshnessStatus) -> float:
    return {
        FreshnessStatus.FRESH: 1.0,
        FreshnessStatus.UNKNOWN: 0.35,
        FreshnessStatus.STALE: 0.0,
        FreshnessStatus.FUTURE: 0.0,
        FreshnessStatus.INVALID: 0.0,
    }[status]


def _preferred_domain_score(source: Mapping[str, Any], domains: Sequence[str]) -> float:
    domain = _source_domain(source)
    return 1.0 if domain and any(_domain_matches(domain, item) for item in domains) else 0.0


def _source_type_score(
    source: Mapping[str, Any],
    required: Sequence[SourceType | str],
) -> float:
    value = str(source.get("source_type", SourceType.UNKNOWN))
    if required:
        return 1.0 if value in {str(item) for item in required} else 0.0
    return 1.0 if value != SourceType.UNKNOWN.value else 0.25


def _content_quality_score(source: Mapping[str, Any], minimum: int) -> float:
    content = source.get("content")
    if not isinstance(content, str) or not content.strip():
        return 0.0
    return round(min(1.0, len(content.strip()) / max(minimum * 4, 1)), 6)


def _weighted_score(breakdown: Mapping[str, float]) -> float:
    return round(
        min(1.0, max(0.0, sum(breakdown[key] * _RANK_WEIGHTS[key] for key in _RANK_WEIGHTS))),
        6,
    )


def _source_domain(source: Mapping[str, Any]) -> str:
    domain = source.get("domain")
    if isinstance(domain, str) and domain.strip():
        return domain.strip().casefold().rstrip(".")
    value = source.get("canonical_url") or source.get("final_url") or source.get("url")
    if not isinstance(value, str):
        return ""
    try:
        return (urlsplit(value).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def _domain_matches(domain: str, preferred: str) -> bool:
    value = preferred.strip().casefold().rstrip(".")
    if "://" in value:
        value = (urlsplit(value).hostname or "").casefold().rstrip(".")
    else:
        value = value.split("/", 1)[0].split(":", 1)[0].rstrip(".")
    return bool(value) and (domain == value or domain.endswith(f".{value}"))


def _date_available(source: Mapping[str, Any]) -> bool:
    return any(source.get(field) is not None for field in ("published_at", "modified_at"))


def _parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        try:
            return _aware_utc(value)
        except ValueError:
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _ordered_unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


__all__ = [
    "RANKING_VERSION",
    "DeduplicationResult",
    "FreshnessEvaluation",
    "RankedSource",
    "deduplicate_sources",
    "evaluate_freshness",
    "evidence_is_eligible",
    "freshness_counts",
    "normalize_source_url",
    "rank_sources",
]
