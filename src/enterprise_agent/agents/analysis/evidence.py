"""Project research evidence into the fields analysis is allowed to see."""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from enterprise_agent.agents.analysis.objective import BAD_INPUT

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
        source, error = _project(item)
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


def _project(item: Any) -> tuple[dict[str, Any] | None, str | None]:
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
    if "values" not in item:
        return source, None
    values = _values(item["values"])
    if values is None:
        return source, BAD_INPUT
    source["values"] = values
    return source, None


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
