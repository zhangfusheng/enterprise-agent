import json
from datetime import UTC, datetime

import httpx
import pytest

from enterprise_agent.agents.research.schemas import SearchCandidate
from enterprise_agent.tools.web.provider import (
    ProviderRegistry,
    SearchProviderConfigurationError,
    SearchProviderError,
    SearchProviderRouter,
    SearchRequest,
    SearchResponse,
)
from enterprise_agent.tools.web.providers import DoubaoSearchProvider, TavilySearchProvider


def _tavily_payload() -> dict:
    return {
        "request_id": "request-1",
        "results": [
            {
                "title": "Tavily title",
                "url": "https://example.com/tavily",
                "content": "Tavily content",
                "score": 0.91,
                "published_date": "2026-01-02T03:04:05Z",
            },
            {"title": "invalid", "url": "/relative", "content": "skip"},
        ],
    }


def _doubao_payload() -> dict:
    return {
        "ResponseMetadata": {"RequestId": "doubao-request-1"},
        "Result": {
            "ResultCount": 1,
            "WebResults": [
                {
                    "Id": "doubao-result-1",
                    "SortId": 1,
                    "Title": "Doubao title",
                    "Url": "https://example.com/doubao",
                    "Snippet": "Short snippet",
                    "Summary": "Relevant summary",
                    "PublishTime": "2026-01-02T03:04:05+08:00",
                    "RankScore": 0.88,
                }
            ],
        },
    }


def test_doubao_custom_maps_api_response_to_search_candidates() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_doubao_payload(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = DoubaoSearchProvider(
        api_key="test-key",
        client=client,
    )

    response = provider.search(
        SearchRequest(query="豆包搜索", limit=5, preferred_domains=("example.com",))
    )

    assert response.provider == "doubao_custom"
    assert response.request_id == "doubao-request-1"
    assert len(response.results) == 1
    candidate = response.results[0]
    assert candidate.title == "Doubao title"
    assert candidate.snippet == "Relevant summary"
    assert candidate.score == 0.88
    assert candidate.published_at == datetime(2026, 1, 1, 19, 4, 5, tzinfo=UTC)
    assert calls[0].headers["Authorization"] == "Bearer test-key"
    assert json.loads(calls[0].content) == {
        "Query": "豆包搜索",
        "SearchType": "web",
        "Count": 5,
        "Filter": {
            "NeedContent": False,
            "NeedUrl": True,
            "Sites": "example.com",
        },
        "QueryControl": {"QueryRewrite": False},
    }
    client.close()


def test_doubao_custom_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOUBAO_SEARCH_API_KEY", raising=False)

    with pytest.raises(SearchProviderConfigurationError, match="DOUBAO_SEARCH_API_KEY"):
        DoubaoSearchProvider()


def test_doubao_custom_allows_queries_up_to_configured_limit() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_doubao_payload(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = DoubaoSearchProvider(
        api_key="test-key",
        max_query_chars=200,
        client=client,
    )

    query = "q" * 150
    response = provider.search(SearchRequest(query=query))

    assert len(response.results) == 1
    assert json.loads(calls[0].content)["Query"] == query
    client.close()


def test_doubao_custom_rejects_queries_over_configured_limit() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(500, request=request)
        )
    )
    provider = DoubaoSearchProvider(
        api_key="test-key",
        max_query_chars=100,
        client=client,
    )

    with pytest.raises(SearchProviderError, match="100-character limit") as caught:
        provider.search(SearchRequest(query="q" * 101))

    assert caught.value.code == "request_rejected"
    client.close()


def test_tavily_maps_api_response_to_search_candidates() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_tavily_payload(), request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = TavilySearchProvider(api_key="test-key", client=client)

    response = provider.search(
        SearchRequest(query="python", limit=2, language="en", preferred_domains=("example.com",))
    )

    assert response.provider == "tavily"
    assert response.request_id == "request-1"
    assert len(response.results) == 1
    candidate = response.results[0]
    assert candidate.title == "Tavily title"
    assert candidate.snippet == "Tavily content"
    assert candidate.score == 0.91
    assert candidate.published_at == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert calls[0].headers["Authorization"] == "Bearer test-key"
    assert json.loads(calls[0].content) == {
        "query": "python",
        "search_depth": "basic",
        "max_results": 2,
        "include_answer": False,
        "include_raw_content": False,
        "include_published_date": True,
        "language": "en",
        "include_domains": ["example.com"],
    }
    client.close()


def test_tavily_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(SearchProviderConfigurationError, match="TAVILY_API_KEY"):
        TavilySearchProvider()


def test_tavily_classifies_rate_limit_as_retryable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate limited"}, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = TavilySearchProvider(api_key="test-key", client=client)

    with pytest.raises(SearchProviderError) as caught:
        provider.search(SearchRequest(query="python"))

    assert caught.value.code == "rate_limited"
    assert caught.value.retryable is True
    client.close()


class _CandidateProvider:
    def __init__(self, name: str, *, failures: int = 0) -> None:
        self.name = name
        self.failures = failures
        self.calls = 0

    def search(self, request: SearchRequest) -> SearchResponse:
        self.calls += 1
        if self.calls <= self.failures:
            raise SearchProviderError(
                "temporary failure",
                provider=self.name,
                code="temporary",
                retryable=True,
            )
        candidate = SearchCandidate(
            candidate_id=f"candidate-{self.name}",
            title=self.name,
            url=f"https://{self.name}.example/source",
            provider=self.name,
            rank=1,
            domain=f"{self.name}.example",
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        return SearchResponse(provider=self.name, results=(candidate,))


def test_router_retries_transient_failure() -> None:
    provider = _CandidateProvider("tavily", failures=1)
    sleeps: list[float] = []
    router = SearchProviderRouter(
        ProviderRegistry([provider]),
        "tavily",
        retry_count=1,
        retry_backoff_seconds=0.5,
        sleeper=sleeps.append,
    )

    response = router.search(SearchRequest(query="python"))

    assert response.provider == "tavily"
    assert provider.calls == 2
    assert sleeps == [0.5]


def test_router_fails_over_to_configured_provider() -> None:
    primary = _CandidateProvider("tavily", failures=1)
    fallback = _CandidateProvider("brave")
    router = SearchProviderRouter(
        ProviderRegistry([primary, fallback]),
        "tavily",
        ("brave",),
        retry_count=0,
    )

    response = router.search(SearchRequest(query="python"))

    assert response.provider == "brave"
    assert primary.calls == 1
    assert fallback.calls == 1
