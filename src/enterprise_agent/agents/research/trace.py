"""Rebuild research evidence from the harness tool trace.

The closing model message is not evidence. Sources come from web_search and
web_fetch results in the order the model called them.
"""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage


def search_failure(messages: Sequence[BaseMessage]) -> str | None:
    """Return a search error when every search failed and none produced a source."""
    failure: str | None = None
    for payload in _payloads(messages, "web_search"):
        if payload.get("ok") is False:
            failure = str(payload.get("error") or "search failed")
            continue
        value = payload.get("value")
        if isinstance(value, str | bytes) or not isinstance(value, list):
            failure = "search result is not a list"
            continue
        if any(_source_record(item) is not None for item in value):
            return None
    return failure


def evidence_from_messages(
    messages: Sequence[BaseMessage],
) -> tuple[list[dict[str, Any]], list[str]]:
    evidence: list[dict[str, Any]] = []
    errors: list[str] = []
    by_url: dict[str, dict[str, Any]] = {}
    for payload in _payloads(messages, "web_search"):
        value = payload.get("value")
        if (
            not payload.get("ok", True)
            or isinstance(value, str | bytes)
            or not isinstance(value, list)
        ):
            continue
        for index, item in enumerate(value):
            record = _source_record(item)
            if record is None:
                errors.append(f"search item {index} is missing a title or url")
                continue
            url = str(record["url"])
            if url in by_url:
                continue
            stored = {**record, "content": None}
            by_url[url] = stored
            evidence.append(stored)
    for payload in _payloads(messages, "web_fetch"):
        if payload.get("skipped"):
            continue
        fetched = payload.get("url")
        if not isinstance(fetched, str):
            continue
        if payload.get("ok") is False:
            errors.append(f"fetch failed for {fetched}: {payload.get('error') or 'fetch failed'}")
            continue
        if payload.get("content") is None:
            errors.append(f"fetch failed for {fetched}: missing content")
            continue
        record = by_url.get(fetched)
        if record is not None:
            record["content"] = payload.get("content")
    return evidence, errors


def _payloads(messages: Sequence[BaseMessage], name: str) -> list[dict[str, Any]]:
    call_ids = {
        str(call.get("id"))
        for message in messages
        if isinstance(message, AIMessage)
        for call in message.tool_calls or []
        if call.get("name") == name and call.get("id")
    }
    payloads: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if message.name != name and message.tool_call_id not in call_ids:
            continue
        payloads.append(_json_object(message.content))
    return payloads


def _json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {"ok": False, "error": content or "tool failed"}
    elif isinstance(content, Mapping):
        parsed = dict(content)
    else:
        return {"ok": False, "error": "tool result is not json"}
    if isinstance(parsed, dict):
        return parsed
    return {"ok": True, "value": parsed}


def _source_record(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, Mapping):
        return None
    title = item.get("title")
    url = item.get("url")
    snippet = item.get("snippet", "")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(url, str) or not url.strip():
        return None
    if not isinstance(snippet, str):
        return None
    record = dict(item)
    record.setdefault("snippet", snippet)
    return record
