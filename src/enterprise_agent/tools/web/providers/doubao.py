"""Volcengine Doubao Search Custom API adapter."""

from __future__ import annotations

import hashlib
import os
import time
from datetime import UTC, datetime
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

_DOUBAO_CUSTOM_URL = "https://open.feedcoopapi.com/search_api/web_search"
_USER_AGENT = "enterprise-agent/0.1"


class DoubaoSearchProvider:
    """Production adapter for the Doubao Search Custom web API."""

    name = "doubao_custom"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_ref: str = "DOUBAO_SEARCH_API_KEY",
        endpoint: str = _DOUBAO_CUSTOM_URL,
        timeout: float = 8.0,
        max_response_bytes: int = 4_000_000,
        max_query_chars: int = 500,
        query_rewrite: bool = False,
        client: httpx.Client | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if max_query_chars < 1:
            raise ValueError("max_query_chars must be positive")
        if not endpoint.startswith("https://"):
            raise ValueError("Doubao Search endpoint must use https")
        resolved_key = api_key if api_key is not None else os.environ.get(api_key_ref, "")
        if not resolved_key.strip():
            raise SearchProviderConfigurationError(
                f"missing API key environment variable: {api_key_ref}",
                provider=self.name,
            )
        self._api_key = resolved_key
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.max_query_chars = max_query_chars
        self.query_rewrite = query_rewrite
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
        if len(request.query) > self.max_query_chars:
            raise SearchProviderError(
                f"Doubao Search query exceeds the {self.max_query_chars}-character limit",
                provider=self.name,
                code="request_rejected",
                retryable=False,
            )
        started_at = time.perf_counter()
        payload: dict[str, Any] = {
            "Query": request.query,
            "SearchType": "web",
            "Count": request.limit,
            "Filter": {
                "NeedContent": False,
                "NeedUrl": True,
            },
            "QueryControl": {"QueryRewrite": self.query_rewrite},
        }
        if request.preferred_domains:
            payload["Filter"]["Sites"] = "|".join(request.preferred_domains)
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
                    "Doubao Search response exceeds configured size limit",
                    provider=self.name,
                    code="response_too_large",
                    retryable=False,
                    status_code=response.status_code,
                )
            self._raise_for_status(response)
            body = response.json()
            self._raise_for_api_error(body)
            metadata = body.get("ResponseMetadata", {})
            request_id = (
                _string_or_none(metadata.get("RequestId"))
                if isinstance(metadata, dict)
                else None
            )
            return SearchResponse(
                provider=self.name,
                results=tuple(self._parse_results(body, request)),
                request_id=request_id,
                latency_ms=elapsed_ms(started_at),
                metadata={"search_type": "web"},
            )
        except SearchProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise SearchProviderError(
                "Doubao Search request timed out",
                provider=self.name,
                code="timeout",
                retryable=True,
            ) from exc
        except httpx.TransportError as exc:
            raise SearchProviderError(
                "Doubao Search transport failed",
                provider=self.name,
                code="transport_error",
                retryable=True,
            ) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise SearchProviderError(
                "Doubao Search returned an invalid response",
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
                "Doubao Search rejected the configured credentials",
                provider=self.name,
            )
        if status == 429 or status >= 500:
            raise SearchProviderError(
                f"Doubao Search service returned HTTP {status}",
                provider=self.name,
                code="rate_limited" if status == 429 else "upstream_error",
                retryable=True,
                status_code=status,
            )
        if status >= 400:
            raise SearchProviderError(
                f"Doubao Search request rejected with HTTP {status}",
                provider=self.name,
                code="request_rejected",
                retryable=False,
                status_code=status,
            )

    def _raise_for_api_error(self, body: Any) -> None:
        if not isinstance(body, dict):
            raise SearchProviderError(
                "Doubao Search response must be an object",
                provider=self.name,
                code="invalid_response",
                retryable=False,
            )
        metadata = body.get("ResponseMetadata")
        if not isinstance(metadata, dict) or metadata.get("Error") in (None, {}):
            return
        raise SearchProviderError(
            "Doubao Search API returned an error",
            provider=self.name,
            code="upstream_error",
            retryable=False,
        )

    def _parse_results(
        self,
        body: dict[str, Any],
        request: SearchRequest,
    ) -> list[SearchCandidate]:
        result = body.get("Result")
        if not isinstance(result, dict):
            raise SearchProviderError(
                "Doubao Search response did not contain Result",
                provider=self.name,
                code="invalid_response",
                retryable=False,
            )
        items = result.get("WebResults")
        if not isinstance(items, list):
            raise SearchProviderError(
                "Doubao Search response did not contain WebResults",
                provider=self.name,
                code="invalid_response",
                retryable=False,
            )
        retrieved_at = datetime.now(UTC)
        candidates: list[SearchCandidate] = []
        for index, item in enumerate(items[: request.limit], start=1):
            if not isinstance(item, dict):
                continue
            title = _string_or_none(item.get("Title"))
            url = _string_or_none(item.get("Url"))
            if not title or not url or urlsplit(url).scheme not in {"http", "https"}:
                continue
            try:
                candidates.append(
                    SearchCandidate(
                        candidate_id=_candidate_id(item, url),
                        title=title,
                        url=url,
                        snippet=(
                            _string_or_none(item.get("Summary"))
                            or _string_or_none(item.get("Snippet"))
                            or ""
                        ),
                        provider=self.name,
                        rank=index,
                        domain=(urlsplit(url).hostname or "").casefold(),
                        score=_float_or_none(item.get("RankScore")),
                        published_at=_parse_datetime(item.get("PublishTime")),
                        retrieved_at=retrieved_at,
                    )
                )
            except ValueError:
                continue
        return candidates


def _candidate_id(item: dict[str, Any], url: str) -> str:
    source_id = _string_or_none(item.get("Id")) or url
    digest = hashlib.sha256(f"doubao_custom:{source_id}".encode()).hexdigest()[:16]
    return f"candidate-{digest}"


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("published date must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
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
