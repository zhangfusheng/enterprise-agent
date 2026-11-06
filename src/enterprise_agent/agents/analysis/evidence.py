"""Project research evidence into the fields analysis is allowed to see."""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from enterprise_agent.agents.analysis.objective import BAD_INPUT
from enterprise_agent.agents.analysis.schemas import ResearchEvidenceBundle
from enterprise_agent.agents.research.ranking import normalize_source_url

MISSING_EVIDENCE = "缺少研究证据"
MISSING_TITLE = "来源缺少标题或链接"
BAD_URL = "来源链接不是 http(s)"

_UNSAFE_URL = re.compile(r"[\s<>]")


def project_evidence(evidence: list[Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Copy allowlisted fields, keeping the first record for each URL.

    A record without a public http(s) title and link is an error and is omitted.
    Bad numeric values are omitted; the rest of that record stays.
    """
    sources: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in evidence:
        source, error = _project(item, preserve_research_fields=False)
        if error:
            errors.append(error)
        if source is None:
            continue
        url = source["url"]
        if url in seen:
            continue
        seen.add(url)
        sources.append(source)
    return sources, errors


def project_research_evidence(
    bundle: ResearchEvidenceBundle,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Project a Research result while preserving citation identity metadata.

    The returned mapping is still an allowlist. It contains only the fields
    needed by Analysis tools and ``EvidenceIndex``; arbitrary provider metadata
    never reaches the model prompt.
    """

    sources: list[dict[str, Any]] = []
    errors: list[str] = []
    seen: set[str] = set()
    for item in bundle.sources:
        source, error = _project(item, preserve_research_fields=True)
        if error:
            errors.append(error)
        if source is None:
            continue
        key = normalize_source_url(source["url"])
        if key in seen:
            continue
        seen.add(key)
        sources.append(source)
    return sources, errors


def _project(
    item: Any,
    *,
    preserve_research_fields: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(item, Mapping):
        return None, MISSING_TITLE
    title = _text(item.get("title"))
    url = _text(item.get("url"))
    if title is None or url is None:
        return None, MISSING_TITLE
    if not _public_url(url):
        return None, BAD_URL
    snippet = item.get("snippet")
    content = item.get("content")
    source: dict[str, Any] = {
        "title": title,
        "url": url,
        "snippet": snippet if isinstance(snippet, str) else "",
    }
    if isinstance(content, str) and content:
        source["content"] = content
    if preserve_research_fields:
        _copy_research_fields(item, source)
    if "values" not in item:
        return source, None
    values = _values(item["values"])
    if values is None:
        return source, BAD_INPUT
    source["values"] = values
    return source, None


def _copy_research_fields(item: Mapping[str, Any], source: dict[str, Any]) -> None:
    for key in (
        "candidate_id",
        "provider",
        "domain",
        "rank",
        "score",
        "source_type",
        "canonical_url",
        "final_url",
        "published_at",
        "modified_at",
        "retrieved_at",
        "document_id",
        "content_hash",
        "extraction_method",
        "fetched_at",
        "content_type",
    ):
        value = item.get(key)
        if value is not None:
            source[key] = value
    paragraphs = item.get("paragraphs")
    if isinstance(paragraphs, list) and all(isinstance(value, str) for value in paragraphs):
        source["paragraphs"] = list(paragraphs)


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _public_url(url: str) -> bool:
    if _UNSAFE_URL.search(url):
        return False
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _values(value: Any) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    numbers: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int | float):
            return None
        numbers.append(float(item))
    return numbers
