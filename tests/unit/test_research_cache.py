import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from enterprise_agent.agents.research.schemas import PageDocument, SearchCandidate
from enterprise_agent.agents.research.tools import (
    ResearchLoopPolicy,
    RunControl,
    bind_research_tools,
)
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.infrastructure.cache.research import (
    CacheEntry,
    CacheLoadFailure,
    MemoryCacheStore,
    ResearchCache,
    ResearchCachePolicy,
)
from enterprise_agent.infrastructure.observability.metrics import (
    begin_run_metrics,
    metrics_snapshot,
    reset_run_metrics,
)
from enterprise_agent.tools.web.provider import SearchRequest, SearchResponse


def _context(*, tenant: str = "tenant-a", run: str = "run-a") -> ExecutionContext:
    return ExecutionContext(
        run_id=run,
        tenant_id=tenant,
        user_id="user",
        agent_id="research",
        workspace_root=".",
    )


def _cache(*, fetch: bool = False) -> ResearchCache:
    return ResearchCache(
        MemoryCacheStore(max_entries=20, max_value_bytes=100_000),
        ResearchCachePolicy(
            read_enabled=True,
            write_enabled=True,
            fetch_enabled=fetch,
            gray_percentage=100,
        ),
    )


def test_cache_key_is_tenant_scoped_and_does_not_expose_query():
    cache = _cache()
    key = cache.search_key(_context(), provider="tavily", query="private question", limit=5)
    other_tenant = cache.search_key(
        _context(tenant="tenant-b"), provider="tavily", query="private question", limit=5
    )

    assert key != other_tenant
    assert "private" not in key
    assert "tenant-a" not in key


def test_cache_merges_concurrent_same_key_loads():
    cache = _cache()
    calls = 0
    calls_lock = threading.Lock()

    def load() -> dict[str, str]:
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.03)
        return {"answer": "cached"}

    def run() -> object:
        return cache.get_or_set(
            "same-key",
            load,
            ttl_seconds=60,
            read_enabled=True,
            write_enabled=True,
            stale_if_error=True,
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: run(), range(6)))

    assert calls == 1
    assert all(result.value == {"answer": "cached"} for result in results)


def test_expired_cache_can_be_used_when_live_loader_fails():
    cache = _cache()
    cache.store.set("stale-key", CacheEntry({"answer": "old"}, time.time() - 1))

    result = cache.get_or_set(
        "stale-key",
        lambda: (_ for _ in ()).throw(CacheLoadFailure("provider unavailable")),
        ttl_seconds=60,
        read_enabled=True,
        write_enabled=True,
        stale_if_error=True,
    )

    assert result.state == "stale"
    assert result.value == {"answer": "old"}


def test_cache_backend_failure_does_not_fail_live_research():
    class _BrokenStore:
        def get(self, key: str) -> CacheEntry | None:
            del key
            raise OSError("cache unavailable")

        def set(self, key: str, entry: CacheEntry) -> None:
            del key, entry
            raise OSError("cache unavailable")

        def delete(self, key: str) -> None:
            del key

    cache = ResearchCache(_BrokenStore(), ResearchCachePolicy(gray_percentage=100))
    result = cache.get_or_set(
        "broken-key",
        lambda: {"answer": "live"},
        ttl_seconds=60,
        read_enabled=True,
        write_enabled=True,
        stale_if_error=True,
    )

    assert result.state == "error"
    assert result.value == {"answer": "live"}


class _Search:
    name = "test"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        return SearchResponse(
            provider=self.name,
            results=(
                SearchCandidate(
                    candidate_id="candidate-1",
                    title="Source",
                    url="https://example.com/source",
                    snippet=request.query,
                    provider=self.name,
                    rank=1,
                    domain="example.com",
                    retrieved_at=datetime.now(UTC),
                ),
            ),
        )


def test_research_tool_uses_cache_and_reports_hit_metrics():
    provider = _Search()
    cache = _cache()
    context = _context()
    token = begin_run_metrics()
    try:
        for _ in range(2):
            control = RunControl(
                ResearchLoopPolicy(
                    max_search_calls=1,
                    max_fetch_calls=0,
                    max_total_tool_calls=2,
                    min_usable_sources=1,
                    max_candidate_sources=3,
                    require_fetch=False,
                ),
                stop_when_sufficient=False,
            )
            tool = bind_research_tools(
                provider=provider,
                fetcher=None,  # type: ignore[arg-type]
                gateway=None,
                allow_fetch=False,
                skip_prefixes=(),
                limit=5,
                context=context,
                control=control,
                cache=cache,
            )[0]
            payload = json.loads(tool.invoke({"query": "same query", "limit": 5}))
            assert payload["ok"] is True
        snapshot = metrics_snapshot()
    finally:
        reset_run_metrics(token)

    assert provider.calls == 1
    assert snapshot["research"]["cache_hits"] == 1
    assert snapshot["research"]["search_requests"] == 2


def test_fetch_cache_still_passes_quality_gate():
    class _Fetcher:
        calls = 0

        def fetch(self, url: str) -> dict[str, object]:
            self.calls += 1
            content = "A sufficiently long document for the research quality gate."
            document = PageDocument(
                document_id="document-1",
                original_url=url,
                final_url=url,
                title="Source",
                content=content,
                paragraphs=[content],
                content_hash="hash",
                content_type="text/plain",
                extraction_method="test",
                fetched_at=datetime.now(UTC),
            )
            return {"document": document.model_dump(mode="json")}

    fetcher = _Fetcher()
    cache = _cache(fetch=True)
    context = _context()
    for _ in range(2):
        control = RunControl(
            ResearchLoopPolicy(
                max_search_calls=1,
                max_fetch_calls=1,
                max_total_tool_calls=2,
                min_usable_sources=1,
                max_candidate_sources=3,
                min_content_length=10,
            ),
            stop_when_sufficient=False,
        )
        control.record_search(
            [
                {
                    "title": "Source",
                    "url": "https://example.com/source",
                    "snippet": "snippet",
                    "provider": "test",
                }
            ]
        )
        tool = bind_research_tools(
            provider=_Search(),
            fetcher=fetcher,
            gateway=None,
            allow_fetch=True,
            skip_prefixes=(),
            limit=5,
            context=context,
            control=control,
            cache=cache,
        )[1]
        payload = json.loads(tool.invoke({"url": "https://example.com/source"}))
        assert payload["ok"] is True

    assert fetcher.calls == 1
