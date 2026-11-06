"""Bounded web-call runtime used by the ResearchExecutor.

The executor supplies every query and URL from a compiled ResearchPlan. The
module owns per-run budgets, scope checks, deduplication, and evidence gates;
the model does not choose ad-hoc tool calls.
"""

import json
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from enterprise_agent.agents.research.schemas import PageDocument
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.runtime.budgets import BudgetExceeded
from enterprise_agent.runtime.cancellation import CancellationRequested
from enterprise_agent.tools.contracts import ToolCall, ToolStatus
from enterprise_agent.tools.gateway import ToolDenied, ToolGateway
from enterprise_agent.tools.web.fetch import WebFetcher
from enterprise_agent.tools.web.provider import SearchProvider
from enterprise_agent.tools.web.search import web_search

_RUN_CONTROL = (CancellationRequested, BudgetExceeded, ToolDenied)
_EVIDENCE_SUFFICIENT = "evidence_sufficient"
_SEARCH_BUDGET_EXHAUSTED = "search_budget_exhausted"
_FETCH_BUDGET_EXHAUSTED = "fetch_budget_exhausted"
_TOOL_BUDGET_EXHAUSTED = "tool_budget_exhausted"
_DUPLICATE_CALL_LIMIT = "duplicate_call_limit"
_EXPECTED_STOP_REASONS = frozenset(
    {
        _EVIDENCE_SUFFICIENT,
        _SEARCH_BUDGET_EXHAUSTED,
        _FETCH_BUDGET_EXHAUSTED,
        _TOOL_BUDGET_EXHAUSTED,
        _DUPLICATE_CALL_LIMIT,
    }
)


@dataclass(frozen=True, slots=True)
class ResearchLoopPolicy:
    """Local limits that keep one research worker from running an open loop."""

    max_search_calls: int = 3
    max_fetch_calls: int = 8
    max_total_tool_calls: int = 12
    max_duplicate_calls: int = 1
    min_usable_sources: int = 5
    max_candidate_sources: int = 30
    min_content_length: int = 80
    # URL-only candidates are not evidence. Offline composition may explicitly
    # disable this for local fixtures that have no page transport.
    require_fetch: bool = True

    def __post_init__(self) -> None:
        if self.max_search_calls < 1:
            raise ValueError("max_search_calls must be positive")
        if self.max_fetch_calls < 0:
            raise ValueError("max_fetch_calls must be non-negative")
        if self.max_total_tool_calls < 1:
            raise ValueError("max_total_tool_calls must be positive")
        if self.max_duplicate_calls < 0:
            raise ValueError("max_duplicate_calls must be non-negative")
        if self.min_usable_sources < 1:
            raise ValueError("min_usable_sources must be positive")
        if self.max_candidate_sources < self.min_usable_sources:
            raise ValueError("max_candidate_sources must be at least min_usable_sources")
        if self.min_content_length < 1:
            raise ValueError("min_content_length must be positive")

    def snapshot(self) -> dict[str, int]:
        return {
            "max_search_calls": self.max_search_calls,
            "max_fetch_calls": self.max_fetch_calls,
            "max_total_tool_calls": self.max_total_tool_calls,
            "max_duplicate_calls": self.max_duplicate_calls,
            "min_usable_sources": self.min_usable_sources,
            "max_candidate_sources": self.max_candidate_sources,
            "min_content_length": self.min_content_length,
            "require_fetch": self.require_fetch,
        }


class ResearchLoopStopped(RuntimeError):
    """Internal signal that the research worker has reached a local stop rule."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        # All local loop limits are deliberate termination signals.  The
        # research result may still be FAILED when the limit is reached before
        # enough evidence is collected, but the trace should not present the
        # bounded stop itself as an unexpected exception.
        self.expected_control_flow = reason in _EXPECTED_STOP_REASONS


@dataclass(slots=True)
class _LoopStats:
    search_calls: int = 0
    fetch_calls: int = 0
    total_tool_calls: int = 0
    duplicate_call_count: int = 0
    excluded_url_hits: int = 0
    stop_reason: str | None = None
    budget_exhausted: bool = False
    unique_queries: set[str] = field(default_factory=set)
    unique_urls: set[str] = field(default_factory=set)
    rejected_source_count: int = 0

    def snapshot(self, policy: ResearchLoopPolicy, excluded_url_count: int) -> dict[str, Any]:
        return {
            "search_call_count": self.search_calls,
            "fetch_call_count": self.fetch_calls,
            "tool_call_count": self.total_tool_calls,
            "duplicate_call_count": self.duplicate_call_count,
            "excluded_url_count": excluded_url_count,
            "excluded_url_hits": self.excluded_url_hits,
            "unique_query_count": len(self.unique_queries),
            "unique_url_count": len(self.unique_urls),
            "candidate_source_count": 0,
            "verified_source_count": 0,
            "rejected_source_count": self.rejected_source_count,
            "stop_reason": self.stop_reason,
            "budget_exhausted": self.budget_exhausted,
            "limits": policy.snapshot(),
        }


class RunControl:
    """Remembers a runtime control error if the tool loop turns it into a message."""

    def __init__(
        self,
        policy: ResearchLoopPolicy | None = None,
        excluded_urls: list[str] | tuple[str, ...] = (),
        *,
        stop_when_sufficient: bool = True,
    ) -> None:
        self.policy = policy or ResearchLoopPolicy()
        self.stop_when_sufficient = stop_when_sufficient
        self.error: BaseException | None = None
        self.stats = _LoopStats()
        self._sources: dict[str, dict[str, Any]] = {}
        self._source_query_ids: dict[str, str] = {}
        self.errors: list[str] = []
        self._lock = threading.RLock()
        self._excluded_urls = {
            normalized
            for item in excluded_urls
            if (normalized := _normalize_url(item))
        }

    def capture(self, exc: BaseException) -> None:
        with self._lock:
            if self.error is None:
                self.error = exc

    def mark_budget_exhausted(self, error: BaseException) -> None:
        with self._lock:
            self.stats.budget_exhausted = True
            self.stats.stop_reason = "global_budget_exhausted"
            self.capture(error)

    def stop(self, reason: str) -> None:
        with self._lock:
            if self.stats.stop_reason is None:
                self.stats.stop_reason = reason

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            snapshot = self.stats.snapshot(self.policy, len(self._excluded_urls))
            snapshot["candidate_source_count"] = len(self._sources)
            snapshot["verified_source_count"] = len(self.evidence())
            return snapshot

    def evidence(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self.policy.require_fetch:
                return list(self._sources.values())
            return [
                source
                for source in self._sources.values()
                if _usable_content(source.get("content"), self.policy.min_content_length)
            ]

    def candidates(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._sources.values())

    def mark_query_sources(self, query_id: str, urls: Sequence[str]) -> None:
        with self._lock:
            for url in urls:
                normalized = _normalize_url(url)
                if normalized in self._sources:
                    self._source_query_ids[normalized] = query_id

    def order_sources(self, query_order: Mapping[str, int]) -> None:
        with self._lock:
            ordered = sorted(
                self._sources.items(),
                key=lambda item: (
                    query_order.get(self._source_query_ids.get(item[0], ""), len(query_order)),
                    item[0],
                ),
            )
            self._sources = dict(ordered)

    def is_verified(self, source: Mapping[str, Any]) -> bool:
        if not self.policy.require_fetch:
            return True
        return _usable_content(source.get("content"), self.policy.min_content_length)

    def filter_search_results(self, value: Any) -> Any:
        if not isinstance(value, list):
            return value
        filtered: list[Any] = []
        with self._lock:
            for item in value:
                url = item.get("url") if isinstance(item, Mapping) else None
                if isinstance(url, str) and self.is_excluded_url(url):
                    self.stats.excluded_url_hits += 1
                    continue
                filtered.append(item)
        return filtered

    def is_excluded_url(self, url: str) -> bool:
        return _normalize_url(url) in self._excluded_urls

    def before_search(self, query: str) -> bool:
        normalized = _normalize_query(query)
        with self._lock:
            self._before_call(
                "search", normalized, self.policy.max_search_calls, _SEARCH_BUDGET_EXHAUSTED
            )
            if normalized in self.stats.unique_queries:
                self.stats.duplicate_call_count += 1
                if self.stats.duplicate_call_count > self.policy.max_duplicate_calls:
                    self.stop(_DUPLICATE_CALL_LIMIT)
                    raise ResearchLoopStopped(_DUPLICATE_CALL_LIMIT)
                return False
            self.stats.unique_queries.add(normalized)
            return True

    def before_fetch(self, url: str) -> str:
        normalized = _normalize_url(url)
        with self._lock:
            self._before_call(
                "fetch", normalized, self.policy.max_fetch_calls, _FETCH_BUDGET_EXHAUSTED
            )
            if normalized in self._excluded_urls:
                self.stats.excluded_url_hits += 1
                return "excluded"
            if normalized in self.stats.unique_urls:
                self.stats.duplicate_call_count += 1
                if self.stats.duplicate_call_count > self.policy.max_duplicate_calls:
                    self.stop(_DUPLICATE_CALL_LIMIT)
                    raise ResearchLoopStopped(_DUPLICATE_CALL_LIMIT)
                return "duplicate"
            self.stats.unique_urls.add(normalized)
            return "call"

    def record_search(self, value: Any, error: str | None = None) -> list[dict[str, Any]]:
        accepted: list[dict[str, Any]] = []
        with self._lock:
            if error is not None:
                self.errors.append(error)
            if not isinstance(value, list):
                self.errors.append("search result is not a list")
                return accepted
            for index, item in enumerate(value):
                if not isinstance(item, Mapping):
                    self.errors.append(f"search item {index} is missing a title or url")
                    self.stats.rejected_source_count += 1
                    continue
                title = item.get("title")
                url = item.get("url")
                snippet = item.get("snippet", "")
                if (
                    not isinstance(title, str)
                    or not title.strip()
                    or not isinstance(url, str)
                    or not url.strip()
                    or not url.startswith(("http://", "https://"))
                ):
                    self.errors.append(f"search item {index} is missing a valid title or url")
                    self.stats.rejected_source_count += 1
                    continue
                if not isinstance(snippet, str):
                    self.errors.append(f"search item {index} has an invalid snippet")
                    self.stats.rejected_source_count += 1
                    continue
                normalized = _normalize_url(url)
                if normalized in self._sources:
                    continue
                if len(self._sources) >= self.policy.max_candidate_sources:
                    self.stats.rejected_source_count += 1
                    continue
                source = {**dict(item), "snippet": snippet, "content": None}
                self._sources[normalized] = source
                accepted.append(source)
        return accepted

    def record_fetch(self, url: str, value: Any, error: str | None = None) -> bool:
        with self._lock:
            if error is not None:
                self.errors.append(f"fetch failed for {url}: {error}")
                return False
            if not isinstance(value, Mapping):
                self.errors.append(f"fetch failed for {url}: invalid fetch result")
                return False
            raw_document = value.get("document")
            try:
                document = PageDocument.model_validate(raw_document)
            except Exception:
                document = None
            if document is None or not _usable_content(
                document.content, self.policy.min_content_length
            ):
                self.errors.append(f"fetch failed for {url}: content did not pass quality gate")
                return False
            source = self._sources.get(_normalize_url(url))
            if source is None:
                self.stats.rejected_source_count += 1
                self.errors.append(f"fetch failed for {url}: source was not a search candidate")
                return False
            document_data = document.model_dump(mode="json")
            source.update(
                {
                    "content": document.content,
                    "paragraphs": document.paragraphs,
                    "document_id": document.document_id,
                    "canonical_url": document.canonical_url,
                    "content_hash": document.content_hash,
                    "extraction_method": document.extraction_method,
                    "final_url": document.final_url,
                    "title": document.title or source.get("title", ""),
                    "fetched_at": document_data["fetched_at"],
                    "content_type": document.content_type,
                }
            )
            if document.published_at is not None:
                source["published_at"] = document_data["published_at"]
            if document.modified_at is not None:
                source["modified_at"] = document_data["modified_at"]
            return True

    def reject_source(self, url: str, reason: str) -> None:
        with self._lock:
            self.stats.rejected_source_count += 1
            if self.policy.require_fetch:
                self.errors.append(f"source rejected for {url}: {reason}")

    def should_stop_after_call(self) -> bool:
        with self._lock:
            if self.stop_when_sufficient and len(self.evidence()) >= self.policy.min_usable_sources:
                self.stop(_EVIDENCE_SUFFICIENT)
                return True
            if self.stats.total_tool_calls >= self.policy.max_total_tool_calls:
                self.stop(_TOOL_BUDGET_EXHAUSTED)
                return True
            return False

    def _before_call(self, kind: str, key: str, limit: int, reason: str) -> None:
        if self.stats.total_tool_calls >= self.policy.max_total_tool_calls:
            self.stop(_TOOL_BUDGET_EXHAUSTED)
            raise ResearchLoopStopped(_TOOL_BUDGET_EXHAUSTED)
        calls = self.stats.search_calls if kind == "search" else self.stats.fetch_calls
        if calls >= limit:
            self.stop(reason)
            raise ResearchLoopStopped(reason)
        if self.stop_when_sufficient and len(self.evidence()) >= self.policy.min_usable_sources:
            self.stop(_EVIDENCE_SUFFICIENT)
            raise ResearchLoopStopped(_EVIDENCE_SUFFICIENT)
        if kind == "search":
            self.stats.search_calls += 1
        else:
            self.stats.fetch_calls += 1
        self.stats.total_tool_calls += 1

class _SearchArgs(BaseModel):
    query: str = Field(description="Search query chosen for the task goal")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of results")


class _FetchArgs(BaseModel):
    url: str = Field(description="Public http(s) page to read")


def bind_research_tools(
    *,
    provider: SearchProvider,
    fetcher: WebFetcher,
    gateway: ToolGateway | None,
    allow_fetch: bool,
    skip_prefixes: tuple[str, ...],
    limit: int,
    context: ExecutionContext,
    control: RunControl,
) -> list[StructuredTool]:
    limit_cap = limit

    def search(query: str, limit: int = 5) -> str:
        return _search(provider, gateway, context, control, query, limit, limit_cap=limit_cap)

    def fetch(url: str) -> str:
        return _fetch(fetcher, gateway, context, control, url, skip_prefixes=skip_prefixes)

    tools = [
        StructuredTool.from_function(
            name="web_search",
            description="Search the public web and return titles, URLs, and snippets.",
            args_schema=_SearchArgs,
            func=search,
        )
    ]
    if allow_fetch:
        tools.append(
            StructuredTool.from_function(
                name="web_fetch",
                description="Read one public web page. Skip addresses the task marks as skipped.",
                args_schema=_FetchArgs,
                func=fetch,
            )
        )
    return tools


def _search(
    provider: SearchProvider,
    gateway: ToolGateway | None,
    context: ExecutionContext,
    control: RunControl,
    query: str,
    limit: int,
    *,
    limit_cap: int,
) -> str:
    bounded = max(1, min(int(limit), int(limit_cap), 20))
    should_call = control.before_search(query)
    if not should_call:
        return _duplicate_payload("query", query, control)
    try:
        if gateway is None:
            value = web_search(provider, query, bounded)
        else:
            result = gateway.call(
                context,
                ToolCall(name="web_search", arguments={"query": query, "limit": bounded}),
            )
            if result.status != ToolStatus.SUCCESS:
                control.record_search(None, result.error or "search failed")
                return _payload(ok=False, error=result.error or "search failed")
            value = result.value
    except _RUN_CONTROL as exc:
        control.capture(exc)
        raise
    except Exception as exc:
        control.record_search(None, type(exc).__name__)
        return _payload(ok=False, error=type(exc).__name__)
    value = control.filter_search_results(value)
    value = control.record_search(value)
    if control.should_stop_after_call():
        raise ResearchLoopStopped(control.stats.stop_reason or _TOOL_BUDGET_EXHAUSTED)
    return _payload(ok=True, value=value)


def _fetch(
    fetcher: WebFetcher,
    gateway: ToolGateway | None,
    context: ExecutionContext,
    control: RunControl,
    url: str,
    *,
    skip_prefixes: tuple[str, ...],
) -> str:
    decision = control.before_fetch(url)
    if decision == "excluded":
        return _excluded_payload(url)
    if decision == "duplicate":
        return _duplicate_payload("url", url, control)
    if not url or url.startswith(skip_prefixes):
        control.reject_source(url, "fetch skipped by policy")
        return _payload(ok=True, skipped=True, url=url)
    try:
        if gateway is None:
            value = fetcher.fetch(url)
        else:
            result = gateway.call(context, ToolCall(name="web_fetch", arguments={"url": url}))
            if result.status != ToolStatus.SUCCESS:
                control.record_fetch(url, None, result.error or "fetch failed")
                return _payload(ok=False, error=result.error or "fetch failed", url=url)
            value = result.value
    except _RUN_CONTROL as exc:
        control.capture(exc)
        raise
    except Exception as exc:
        error = getattr(exc, "code", type(exc).__name__)
        control.record_fetch(url, None, str(error))
        return _payload(ok=False, error=str(error), url=url)
    if not isinstance(value, Mapping) or value.get("document") is None:
        control.record_fetch(url, value, "missing document")
        return _payload(ok=False, error="missing document", url=url)
    accepted = control.record_fetch(url, value)
    if not accepted:
        return _payload(ok=False, error="content did not pass quality gate", url=url)
    if control.should_stop_after_call():
        raise ResearchLoopStopped(control.stats.stop_reason or _TOOL_BUDGET_EXHAUSTED)
    return _payload(
        ok=True,
        url=url,
        document=value.get("document"),
        fetch=value.get("fetch", {}),
    )


def _payload(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False)


def _duplicate_payload(argument: str, value: str, control: RunControl) -> str:
    payload = {"ok": True, "skipped": True, "duplicate": True, argument: value}
    if control.should_stop_after_call():
        raise ResearchLoopStopped(control.stats.stop_reason or _DUPLICATE_CALL_LIMIT)
    return _payload(**payload)


def _excluded_payload(url: str) -> str:
    return _payload(ok=True, skipped=True, excluded=True, url=url)


def _normalize_query(value: str) -> str:
    return " ".join(value.split()).casefold()


def _normalize_url(value: str) -> str:
    raw = value.strip()
    if not raw:
        return raw
    parsed = urlsplit(raw)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), path, parsed.query, ""))


def _usable_content(value: object, minimum: int) -> bool:
    return isinstance(value, str) and len(value.strip()) >= minimum
