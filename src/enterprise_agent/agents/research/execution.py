"""Deterministic execution of a compiled research plan."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from enterprise_agent.agents.research.planning import PlanValidationError
from enterprise_agent.agents.research.schemas import (
    ResearchPlan,
    ResearchQuery,
    ResearchRequest,
)
from enterprise_agent.agents.research.tools import (
    ResearchLoopPolicy,
    ResearchLoopStopped,
    RunControl,
    bind_research_tools,
)
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.infrastructure.observability.tracing import trace
from enterprise_agent.runtime.cancellation import CancellationRequested
from enterprise_agent.tools.gateway import ToolDenied, ToolGateway
from enterprise_agent.tools.web.fetch import WebFetcher
from enterprise_agent.tools.web.provider import SearchProvider


@dataclass(slots=True)
class ResearchExecution:
    plan: ResearchPlan
    control: RunControl

    @property
    def evidence(self) -> list[dict[str, Any]]:
        return self.control.evidence()

    @property
    def errors(self) -> list[str]:
        return list(self.control.errors)


class ResearchExecutor:
    """Execute only queries present in a validated plan."""

    def __init__(
        self,
        provider: SearchProvider,
        fetcher: WebFetcher,
        *,
        allow_fetch: bool = True,
        skip_fetch_prefixes: tuple[str, ...] = (),
        loop_policy: ResearchLoopPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.fetcher = fetcher
        self.allow_fetch = allow_fetch
        self.skip_fetch_prefixes = tuple(skip_fetch_prefixes)
        self.loop_policy = loop_policy or ResearchLoopPolicy()

    def run(
        self,
        plan: ResearchPlan,
        request: ResearchRequest,
        context: ExecutionContext,
        *,
        gateway: ToolGateway | None = None,
    ) -> ResearchExecution:
        control = RunControl(
            self._bounded_policy(plan),
            request.excluded_urls,
            stop_when_sufficient=False,
        )
        tools = bind_research_tools(
            provider=self.provider,
            fetcher=self.fetcher,
            gateway=gateway,
            allow_fetch=self.allow_fetch,
            skip_prefixes=self.skip_fetch_prefixes,
            limit=request.limit,
            context=context,
            control=control,
        )
        search_tool = next(tool for tool in tools if tool.name == "web_search")
        fetch_tool = next((tool for tool in tools if tool.name == "web_fetch"), None)
        effective_as_of = request.as_of or datetime.now(UTC)
        self._execute_queries(plan, search_tool, control)
        control.rank_candidates(
            preferred_domains=plan.preferred_domains,
            required_source_types=plan.required_source_types,
            freshness_days=plan.freshness_days,
            as_of=effective_as_of,
        )
        if (
            self.allow_fetch
            and fetch_tool is not None
            and control.policy.require_fetch
            and control.candidates()
        ):
            self._execute_fetches(fetch_tool, control)
            control.rank_candidates(
                preferred_domains=plan.preferred_domains,
                required_source_types=plan.required_source_types,
                freshness_days=plan.freshness_days,
                as_of=effective_as_of,
            )
        return ResearchExecution(plan=plan, control=control)

    def _bounded_policy(self, plan: ResearchPlan) -> ResearchLoopPolicy:
        max_search_calls = min(self.loop_policy.max_search_calls, plan.max_search_calls)
        max_fetch_calls = min(self.loop_policy.max_fetch_calls, plan.max_fetch_calls)
        min_usable_sources = max(
            self.loop_policy.min_usable_sources,
            plan.min_verified_sources,
        )
        max_candidate_sources = min(
            self.loop_policy.max_candidate_sources,
            plan.max_candidate_sources,
        )
        if max_candidate_sources < min_usable_sources:
            raise PlanValidationError(
                "research plan source budget is smaller than its verification requirement"
            )
        return ResearchLoopPolicy(
            max_search_calls=max_search_calls,
            max_fetch_calls=max_fetch_calls,
            max_total_tool_calls=self.loop_policy.max_total_tool_calls,
            max_duplicate_calls=self.loop_policy.max_duplicate_calls,
            min_usable_sources=min_usable_sources,
            max_candidate_sources=max_candidate_sources,
            min_content_length=self.loop_policy.min_content_length,
            require_fetch=self.loop_policy.require_fetch,
        )

    def _execute_queries(self, plan: ResearchPlan, search_tool: Any, control: RunControl) -> None:
        pending = {query.query_id: query for query in plan.queries}
        completed: set[str] = set()
        while pending:
            ready = [
                query
                for query in pending.values()
                if set(query.depends_on).issubset(completed)
            ]
            if not ready:
                raise PlanValidationError("research plan execution is blocked by a dependency cycle")
            batch = ready[:]
            if plan.execution_mode.value == "sequential" or plan.max_concurrency == 1:
                for query in batch:
                    self._execute_query(query, search_tool, control)
                    completed.add(query.query_id)
                    pending.pop(query.query_id, None)
                    if control.stats.stop_reason is not None:
                        return
                continue

            workers = min(plan.max_concurrency, len(batch))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="research-search") as pool:
                futures = {
                    pool.submit(
                        copy_context().run,
                        self._execute_query,
                        query,
                        search_tool,
                        control,
                    ): query
                    for query in batch
                }
                for future in as_completed(futures):
                    query = futures[future]
                    future.result()
                    completed.add(query.query_id)
                    pending.pop(query.query_id, None)
                    if control.stats.stop_reason is not None:
                        for remaining in futures:
                            remaining.cancel()
                        return

    def _execute_query(self, query: ResearchQuery, search_tool: Any, control: RunControl) -> None:
        with trace(
            "web_search",
            "tool",
            inputs={"query_id": query.query_id, "query": query.query},
            tags=["research", "planned-query"],
        ) as span:
            try:
                payload = search_tool.invoke({"query": query.query, "limit": 5})
            except ResearchLoopStopped:
                return
            except (CancellationRequested, ToolDenied):
                raise
            except Exception as exc:
                control.errors.append(f"query {query.query_id} failed: {type(exc).__name__}")
                return
        parsed = _json_payload(payload)
        span.outputs = {"ok": parsed.get("ok", False), "query_id": query.query_id}
        if parsed.get("ok") is False:
            error = str(parsed.get("error") or "search failed")
            control.errors.append(f"query {query.query_id} failed: {error}")
            return
        values = parsed.get("value")
        if isinstance(values, list):
            control.mark_query_sources(
                query.query_id,
                [
                    item["url"]
                    for item in values
                    if isinstance(item, dict) and isinstance(item.get("url"), str)
                ],
            )

    def _execute_fetches(self, fetch_tool: Any, control: RunControl) -> None:
        for candidate in control.candidates():
            url = candidate.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            with trace("web_fetch", "tool", inputs={"url": url}, tags=["research", "planned-fetch"]) as span:
                try:
                    payload = fetch_tool.invoke({"url": url})
                except ResearchLoopStopped:
                    return
                except (CancellationRequested, ToolDenied):
                    raise
                except Exception as exc:
                    control.errors.append(f"fetch failed for {url}: {type(exc).__name__}")
                    continue
            parsed = _json_payload(payload)
            span.outputs = {"ok": parsed.get("ok", False), "url": url}
            if parsed.get("ok") is False:
                control.errors.append(
                    f"fetch failed for {url}: {parsed.get('error') or 'fetch failed'}"
                )


def _json_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return {"ok": False, "error": "tool result is not json"}
    else:
        parsed = payload
    return parsed if isinstance(parsed, dict) else {"ok": False, "error": "tool result is not an object"}


__all__ = ["ResearchExecution", "ResearchExecutor"]
