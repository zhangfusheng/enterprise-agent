"""Bounded HTTP fetching for research documents.

The fetcher owns transport concerns only. Content parsing is delegated to the
extractor registry so that HTML, text, and PDF documents share one contract.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from enterprise_agent.tools.web.access_policy import validate_public_url, validate_redirect

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
_SUPPORTED_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "application/xhtml+xml",
        "text/html",
        "text/plain",
    }
)
_DEFAULT_HEADERS = {
    "Accept": ", ".join(sorted(_SUPPORTED_MEDIA_TYPES)),
    "Accept-Encoding": "gzip, deflate, br",
}


class WebFetchError(RuntimeError):
    """A safe, classifiable error raised by the fetch or extraction boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "fetch_error",
        retryable: bool = False,
        status_code: int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.url = url


@dataclass(frozen=True, slots=True)
class FetchResponse:
    """The bounded transport response handed to an extractor."""

    original_url: str
    final_url: str
    status_code: int
    headers: Mapping[str, str]
    body: bytes
    fetched_at: datetime
    redirect_chain: tuple[str, ...]
    content_type: str

    @property
    def response_bytes(self) -> int:
        return len(self.body)


class WebFetcher:
    """Fetch public documents with bounded, reusable HTTP transport."""

    def __init__(
        self,
        *,
        timeout: httpx.Timeout | float = 15.0,
        max_bytes: int = 2_000_000,
        max_text_chars: int = 100_000,
        max_redirects: int = 3,
        max_retries: int = 1,
        allow_http_downgrade: bool = False,
        user_agent: str = "enterprise-agent/0.1",
        max_pdf_pages: int = 50,
        client: httpx.Client | None = None,
        extractor_registry: Any | None = None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if max_text_chars < 1:
            raise ValueError("max_text_chars must be positive")
        if max_redirects < 0:
            raise ValueError("max_redirects must be non-negative")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if max_pdf_pages < 1:
            raise ValueError("max_pdf_pages must be positive")
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.max_text_chars = max_text_chars
        self.max_redirects = max_redirects
        self.max_retries = max_retries
        self.allow_http_downgrade = allow_http_downgrade
        self.user_agent = user_agent
        self.max_pdf_pages = max_pdf_pages
        self._client = client
        self._owns_client = client is None
        self._extractor_registry = extractor_registry

    def fetch(self, url: str) -> dict[str, Any]:
        response = self._fetch_response(url)
        extracted = self._registry().extract(
            response,
            max_text_chars=self.max_text_chars,
            max_pdf_pages=self.max_pdf_pages,
        )
        return {
            "document": extracted.document.model_dump(mode="json"),
            "fetch": {
                "status_code": response.status_code,
                "response_bytes": response.response_bytes,
                "redirect_count": len(response.redirect_chain),
                "redirect_chain": list(response.redirect_chain),
                "warnings": list(extracted.warnings),
            },
        }

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def _registry(self):
        if self._extractor_registry is None:
            from enterprise_agent.tools.web.extract import ExtractorRegistry

            self._extractor_registry = ExtractorRegistry.default()
        return self._extractor_registry

    def _fetch_response(self, url: str) -> FetchResponse:
        original_url = _validate_initial_url(url)
        current = original_url
        redirect_chain: list[str] = []
        for redirect_count in range(self.max_redirects + 1):
            response = self._request_with_retries(current)
            if response.status_code in _REDIRECT_STATUSES:
                location = response.headers.get("location")
                if not location:
                    raise WebFetchError(
                        "redirect did not include location",
                        code="redirect_limit",
                        url=current,
                    )
                if redirect_count >= self.max_redirects:
                    raise WebFetchError(
                        "maximum redirects exceeded",
                        code="redirect_limit",
                        url=current,
                    )
                try:
                    target = validate_redirect(current, location)
                except Exception as exc:
                    raise WebFetchError(
                        str(exc),
                        code="blocked_url",
                        url=urljoin(current, location),
                    ) from exc
                if not self.allow_http_downgrade and _is_http_downgrade(current, target):
                    raise WebFetchError(
                        "redirect from https to http is not allowed",
                        code="blocked_url",
                        url=target,
                    )
                redirect_chain.append(target)
                current = target
                continue
            return FetchResponse(
                original_url=original_url,
                final_url=current,
                status_code=response.status_code,
                headers=response.headers,
                body=response.body,
                fetched_at=datetime.now(UTC),
                redirect_chain=tuple(redirect_chain),
                content_type=_media_type(response.headers.get("content-type", ""), response.body),
            )
        raise WebFetchError("maximum redirects exceeded", code="redirect_limit", url=current)

    def _request_with_retries(self, url: str) -> _ResponseData:
        last_error: WebFetchError | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._request_once(url)
            except WebFetchError as exc:
                if not exc.retryable or attempt >= self.max_retries:
                    raise
                last_error = exc
            else:
                if response.status_code not in _RETRYABLE_STATUSES or attempt >= self.max_retries:
                    if not 200 <= response.status_code < 300 and response.status_code not in _REDIRECT_STATUSES:
                        raise WebFetchError(
                            f"upstream returned HTTP {response.status_code}",
                            code="http_status",
                            retryable=response.status_code in _RETRYABLE_STATUSES,
                            status_code=response.status_code,
                            url=url,
                        )
                    return response
                last_error = WebFetchError(
                    f"upstream returned HTTP {response.status_code}",
                    code="http_status",
                    retryable=True,
                    status_code=response.status_code,
                    url=url,
                )
            time.sleep(_retry_delay(attempt, last_error))
        raise last_error or WebFetchError("request failed", url=url)

    def _request_once(self, url: str) -> _ResponseData:
        client = self._get_client()
        headers = {**_DEFAULT_HEADERS, "User-Agent": self.user_agent}
        try:
            with client.stream("GET", url, headers=headers) as response:
                response_headers = {key.lower(): value for key, value in response.headers.items()}
                if response.status_code in _REDIRECT_STATUSES:
                    return _ResponseData(response.status_code, response_headers, b"")
                if not 200 <= response.status_code < 300:
                    return _ResponseData(response.status_code, response_headers, b"")
                content_length = _content_length(response_headers.get("content-length"))
                if content_length is not None and content_length > self.max_bytes:
                    raise WebFetchError(
                        "response exceeds size limit",
                        code="body_too_large",
                        url=url,
                    )
                declared_type = _declared_media_type(response_headers.get("content-type", ""))
                if declared_type and declared_type not in _SUPPORTED_MEDIA_TYPES:
                    raise WebFetchError(
                        f"unsupported content type: {declared_type}",
                        code="unsupported_content_type",
                        url=url,
                    )
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > self.max_bytes:
                        raise WebFetchError(
                            "response exceeds size limit",
                            code="body_too_large",
                            url=url,
                        )
                return _ResponseData(response.status_code, response_headers, bytes(body))
        except WebFetchError:
            raise
        except httpx.TimeoutException as exc:
            raise WebFetchError(
                "web request timed out",
                code="timeout",
                retryable=True,
                url=url,
            ) from exc
        except httpx.TransportError as exc:
            raise WebFetchError(
                "web transport failed",
                code="transport_error",
                retryable=True,
                url=url,
            ) from exc

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, follow_redirects=False)
        return self._client


@dataclass(frozen=True, slots=True)
class _ResponseData:
    status_code: int
    headers: dict[str, str]
    body: bytes


def web_fetch(
    url: str,
    timeout: float = 15.0,
    max_bytes: int = 2_000_000,
    max_redirects: int = 3,
) -> dict[str, Any]:
    """Fetch one public document for direct, non-container callers."""

    fetcher = WebFetcher(
        timeout=timeout,
        max_bytes=max_bytes,
        max_redirects=max_redirects,
        max_retries=0,
    )
    try:
        return fetcher.fetch(url)
    finally:
        fetcher.close()


def _validate_initial_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise WebFetchError("URL must not be blank", code="invalid_url", url=url)
    value = url.strip()
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise WebFetchError("only http(s) URLs with a hostname are allowed", code="invalid_url", url=value)
    try:
        validate_public_url(value)
    except Exception as exc:
        raise WebFetchError(str(exc), code="blocked_url", url=value) from exc
    return value


def _is_http_downgrade(source: str, target: str) -> bool:
    return urlsplit(source).scheme.lower() == "https" and urlsplit(target).scheme.lower() == "http"


def _declared_media_type(value: str) -> str:
    return value.split(";", 1)[0].strip().lower()


def _media_type(value: str, body: bytes) -> str:
    declared = _declared_media_type(value)
    if declared:
        if declared not in _SUPPORTED_MEDIA_TYPES:
            raise WebFetchError(
                f"unsupported content type: {declared}",
                code="unsupported_content_type",
            )
        return declared
    if body.startswith(b"%PDF-"):
        return "application/pdf"
    sample = body[:1024].lstrip().lower()
    if sample.startswith(b"<!doctype html") or b"<html" in sample:
        return "text/html"
    if b"\x00" in body[:4096]:
        raise WebFetchError("response content type is missing", code="unsupported_content_type")
    return "text/plain"


def _content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _retry_delay(attempt: int, error: WebFetchError | None) -> float:
    if error is not None and error.status_code == 429:
        return 0.25
    return min(2.0, 0.25 * (2**attempt))


__all__ = ["FetchResponse", "WebFetchError", "WebFetcher", "web_fetch"]
