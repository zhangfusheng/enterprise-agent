"""Tavily Search API adapter."""

from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from enterprise_agent.agents.research.schemas import SearchCandidate
from enterprise_agent.tools.web.provider import (
    SearchProviderConfigurationError,
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    elapsed_ms,
)

_TAVILY_URL = "https://api.tavily.com/search"
_USER_AGENT = "enterprise-agent/0.1"


class TavilySearchProvider:
    """Production adapter for Tavily's official Search API."""

    name = "tavily"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_ref: str = "TAVILY_API_KEY",
        endpoint: str = _TAVILY_URL,
        timeout: float = 8.0,
        search_depth: str = "basic",
        max_response_bytes: int = 2_000_000,
        client: httpx.Client | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if search_depth not in {"basic", "advanced", "fast", "ultra-fast"}:
            raise ValueError("unsupported Tavily search depth")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if not endpoint.startswith("https://"):
            raise ValueError("Tavily endpoint must use https")
        resolved_key = api_key if api_key is not None else os.environ.get(api_key_ref, "")
        if not resolved_key.strip():
            raise SearchProviderConfigurationError(
                f"missing API key environment variable: {api_key_ref}",
                provider=self.name,
            )
        self._api_key = resolved_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.search_depth = search_depth
        self.max_response_bytes = max_response_bytes
        self._client = client or httpx.Client(
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": _USER_AGENT,
            },
            timeout=httpx.Timeout(timeout, connect=min(timeout, 4.0)),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            follow_redirects=False,
        )
        self._owns_client = client is None

    def search(self, request: SearchRequest) -> SearchResponse:
        started_at = time.perf_counter()
        payload: dict[str, Any] = {
            "query": request.query,
            "search_depth": self.search_depth,
            "max_results": request.limit,
            "include_answer": False,
            "include_raw_content": False,
            "include_published_date": True,
        }
        if request.language:
            payload["language"] = request.language
        if request.preferred_domains:
            payload["include_domains"] = list(request.preferred_domains)
        try:
            response = self._client.post(
                self.endpoint,
                json=payload,
                timeout=request.timeout or self.timeout,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": _USER_AGENT,
                },
            )
            if len(response.content) > self.max_response_bytes:
                raise SearchProviderError(
                    "Tavily response exceeds configured size limit",
                    provider=self.name,
                    code="response_too_large",
                    retryable=False,
                    status_code=response.status_code,
                )
            self._raise_for_status(response)
            body = response.json()
            results = self._parse_results(body, request)
            return SearchResponse(
                provider=self.name,
                results=tuple(results),
                request_id=_string_or_none(body.get("request_id"))
                if isinstance(body, dict)
                else None,
                latency_ms=elapsed_ms(started_at),
                metadata={"search_depth": self.search_depth},
            )
        except SearchProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise SearchProviderError(
                "Tavily request timed out",
                provider=self.name,
                code="timeout",
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise SearchProviderError(
                "Tavily transport failed",
                provider=self.name,
                code="transport_error",
                retryable=True,
            ) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise SearchProviderError(
                "Tavily returned an invalid response",
                provider=self.name,
                code="invalid_response",
                retryable=False,
            ) from exc

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in {401, 403}:
            raise SearchProviderConfigurationError(
                "Tavily rejected the configured credentials",
                provider=self.name,
            )
        if status == 429 or status >= 500:
            raise SearchProviderError(
                f"Tavily service returned HTTP {status}",
                provider=self.name,
                code="rate_limited" if status == 429 else "upstream_error",
                retryable=True,
                status_code=status,
            )
        if status >= 400:
            raise SearchProviderError(
                f"Tavily request rejected with HTTP {status}",
                provider=self.name,
                code="request_rejected",
                retryable=False,
                status_code=status,
            )

    def _parse_results(
        self,
        body: Any,
        request: SearchRequest,
    ) -> list[SearchCandidate]:
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise SearchProviderError(
                "Tavily response did not contain a result list",
                provider=self.name,
                code="invalid_response",
                retryable=False,
            )
        retrieved_at = datetime.now(UTC)
        candidates: list[SearchCandidate] = []
        for index, item in enumerate(body["results"][: request.limit], start=1):
            if not isinstance(item, dict):
                continue
            title = _string_or_none(item.get("title"))
            url = _string_or_none(item.get("url"))
            if not title or not url or urlsplit(url).scheme not in {"http", "https"}:
                continue
            try:
                published_at = _parse_datetime(item.get("published_date"))
                score = _float_or_none(item.get("score"))
                candidates.append(
                    SearchCandidate(
                        candidate_id=_candidate_id(url),
                        title=title,
                        url=url,
                        snippet=_string_or_none(item.get("content")) or "",
                        provider=self.name,
                        rank=index,
                        domain=(urlsplit(url).hostname or "").casefold(),
                        score=score,
                        published_at=published_at,
                        retrieved_at=retrieved_at,
                    )
                )
            except ValueError:
                continue
        return candidates


def _candidate_id(url: str) -> str:
    digest = hashlib.sha256(f"tavily:{url}".encode()).hexdigest()[:16]
    return f"candidate-{digest}"


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("published date must be a string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("published date must include a timezone")
    return parsed.astimezone(UTC)


def _string_or_none(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("score must be numeric")
    result = float(value)
    if not 0 <= result <= 1:
        raise ValueError("score must be between 0 and 1")
    return result
