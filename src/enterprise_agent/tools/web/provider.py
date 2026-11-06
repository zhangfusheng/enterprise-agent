"""Contracts and routing primitives for production web search providers."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from time import sleep
from typing import Protocol

from enterprise_agent.agents.research.schemas import SearchCandidate


@dataclass(frozen=True, slots=True)
class SearchItem:
    """Legacy result shape kept for historical contract adapters only."""

    title: str
    url: str
    snippet: str
    metadata: dict[str, str]


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """Provider-neutral search input."""

    query: str
    limit: int = 5
    language: str | None = None
    preferred_domains: tuple[str, ...] = ()
    timeout: float | None = None

    def __post_init__(self) -> None:
        if not self.query.strip():
            raise ValueError("query must not be blank")
        if not 1 <= self.limit <= 20:
            raise ValueError("limit must be between 1 and 20")
        if self.timeout is not None and self.timeout <= 0:
            raise ValueError("timeout must be positive")


@dataclass(frozen=True, slots=True)
class SearchResponse:
    """Provider-neutral search output."""

    provider: str
    results: tuple[SearchCandidate, ...]
    request_id: str | None = None
    latency_ms: float = 0.0
    metadata: Mapping[str, str] = field(default_factory=dict)


class SearchProvider(Protocol):
    """Interface implemented by each production search service adapter."""

    name: str

    def search(self, request: SearchRequest) -> SearchResponse:
        ...


class SearchProviderError(RuntimeError):
    """Classified failure from a search provider."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        code: str,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.retryable = retryable
        self.status_code = status_code


class SearchProviderConfigurationError(SearchProviderError):
    """A provider cannot be used because its configuration is invalid."""

    def __init__(self, message: str, *, provider: str) -> None:
        super().__init__(
            message,
            provider=provider,
            code="configuration_error",
            retryable=False,
        )


class SearchProviderExhaustedError(SearchProviderError):
    """All configured providers failed for one request."""

    def __init__(self, errors: list[SearchProviderError]) -> None:
        providers = ",".join(error.provider for error in errors)
        super().__init__(
            f"all configured search providers failed: {providers}",
            provider="router",
            code="all_providers_failed",
            retryable=False,
        )
        self.errors = tuple(errors)


class ProviderRegistry:
    """Explicit allow-list of search providers available to the process."""

    def __init__(self, providers: Iterable[SearchProvider] = ()) -> None:
        self._providers: dict[str, SearchProvider] = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider: SearchProvider) -> None:
        name = provider.name.strip().lower()
        if not name:
            raise ValueError("search provider name must not be blank")
        if name in self._providers:
            raise ValueError(f"search provider already registered: {name}")
        self._providers[name] = provider

    def get(self, name: str) -> SearchProvider:
        normalized = name.strip().lower()
        try:
            return self._providers[normalized]
        except KeyError as exc:
            available = ", ".join(sorted(self._providers)) or "none"
            raise SearchProviderConfigurationError(
                f"unknown search provider {normalized!r}; available: {available}",
                provider=normalized or "unknown",
            ) from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))


class SearchProviderRouter:
    """Run the configured provider chain with bounded retries and failover."""

    name = "router"

    def __init__(
        self,
        registry: ProviderRegistry,
        primary: str,
        fallbacks: Iterable[str] = (),
        *,
        retry_count: int = 1,
        retry_backoff_seconds: float = 0.25,
        sleeper=sleep,
    ) -> None:
        if retry_count < 0:
            raise ValueError("retry_count must be non-negative")
        if retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative")
        names = _unique_names((primary, *fallbacks))
        self.providers = tuple(registry.get(name) for name in names)
        self.primary_provider = self.providers[0]
        self.retry_count = retry_count
        self.retry_backoff_seconds = retry_backoff_seconds
        self._sleep = sleeper

    @property
    def provider_names(self) -> tuple[str, ...]:
        return tuple(provider.name for provider in self.providers)

    def search(self, request: SearchRequest) -> SearchResponse:
        errors: list[SearchProviderError] = []
        for provider in self.providers:
            for attempt in range(self.retry_count + 1):
                try:
                    response = provider.search(request)
                    if response.provider != provider.name:
                        return SearchResponse(
                            provider=provider.name,
                            results=response.results,
                            request_id=response.request_id,
                            latency_ms=response.latency_ms,
                            metadata={**dict(response.metadata), "provider_mismatch": "true"},
                        )
                    return response
                except SearchProviderError as exc:
                    errors.append(exc)
                    if not exc.retryable or attempt >= self.retry_count:
                        break
                    self._sleep(self.retry_backoff_seconds * (2**attempt))
                except Exception:
                    errors.append(
                        SearchProviderError(
                            "search provider raised an unexpected error",
                            provider=provider.name,
                            code="provider_error",
                            retryable=False,
                        )
                    )
                    break
        raise SearchProviderExhaustedError(errors)

    def close(self) -> None:
        for provider in self.providers:
            close = getattr(provider, "close", None)
            if callable(close):
                close()


def _unique_names(names: Iterable[str]) -> tuple[str, ...]:
    unique: list[str] = []
    for name in names:
        normalized = name.strip().lower()
        if normalized and normalized not in unique:
            unique.append(normalized)
    if not unique:
        raise ValueError("at least one search provider is required")
    return tuple(unique)


def elapsed_ms(started_at: float) -> float:
    """Return elapsed monotonic time in milliseconds for provider telemetry."""

    return round((time.perf_counter() - started_at) * 1000, 3)
