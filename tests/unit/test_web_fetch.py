import io

import httpx
import pytest
from pypdf import PdfWriter

from enterprise_agent.agents.research.schemas import PageDocument
from enterprise_agent.agents.research.tools import ResearchLoopPolicy, RunControl
from enterprise_agent.tools.web import access_policy
from enterprise_agent.tools.web import fetch as fetch_module
from enterprise_agent.tools.web.fetch import WebFetcher, WebFetchError


@pytest.fixture
def allow_public_test_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch_module, "validate_public_url", lambda url: None)
    monkeypatch.setattr(access_policy, "validate_public_url", lambda url: None)
    monkeypatch.setattr(fetch_module.time, "sleep", lambda seconds: None)


def _fetcher(handler, **kwargs) -> tuple[WebFetcher, httpx.Client]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return WebFetcher(client=client, **kwargs), client


def test_html_fetch_extracts_document_metadata_and_main_content(
    allow_public_test_urls: None,
) -> None:
    html = b"""
    <html><head>
      <title>Fallback title</title>
      <meta property="og:title" content="Research title">
      <meta property="article:published_time" content="2026-01-02T03:04:05Z">
      <link rel="canonical" href="/canonical">
      <script type="application/ld+json">{"dateModified":"2026-01-03T03:04:05Z"}</script>
    </head><body>
      <nav>Navigation should not be included</nav>
      <main><h1>Research title</h1><p>First paragraph.</p><p>Second paragraph.</p></main>
      <script>secret noise</script>
    </body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=html,
            request=request,
        )

    fetcher, client = _fetcher(handler, max_retries=0)
    try:
        result = fetcher.fetch("https://example.com/source")
    finally:
        fetcher.close()
        client.close()

    document = PageDocument.model_validate(result["document"])
    assert document.title == "Research title"
    assert document.canonical_url == "https://example.com/canonical"
    assert document.extraction_method == "html_main_content"
    assert document.paragraphs == ["Research title", "First paragraph.", "Second paragraph."]
    assert "Navigation should not be included" not in document.content
    assert "secret noise" not in document.content
    assert document.published_at is not None
    assert document.modified_at is not None
    assert result["fetch"]["status_code"] == 200


def test_plain_text_fetch_normalizes_paragraphs(
    allow_public_test_urls: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            content=b"First line\n\nSecond line\nThird line",
            request=request,
        )

    fetcher, client = _fetcher(handler, max_retries=0)
    try:
        document = PageDocument.model_validate(fetcher.fetch("https://example.com/text")["document"])
    finally:
        fetcher.close()
        client.close()

    assert document.extraction_method == "plain_text"
    assert document.paragraphs == ["First line", "Second line", "Third line"]
    assert document.content_hash


def test_fetch_stream_enforces_response_size_limit(
    allow_public_test_urls: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"too large",
            request=request,
        )

    fetcher, client = _fetcher(handler, max_bytes=4, max_retries=0)
    try:
        with pytest.raises(WebFetchError) as caught:
            fetcher.fetch("https://example.com/large")
    finally:
        fetcher.close()
        client.close()

    assert caught.value.code == "body_too_large"


def test_fetch_retries_transient_status_and_follows_redirect(
    allow_public_test_urls: None,
) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(503, request=request)
        if len(calls) == 2:
            return httpx.Response(302, headers={"location": "/final"}, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"usable content",
            request=request,
        )

    fetcher, client = _fetcher(handler, max_retries=1, max_redirects=2)
    try:
        result = fetcher.fetch("https://example.com/start")
    finally:
        fetcher.close()
        client.close()

    assert calls == [
        "https://example.com/start",
        "https://example.com/start",
        "https://example.com/final",
    ]
    assert result["document"]["final_url"] == "https://example.com/final"
    assert result["fetch"]["redirect_count"] == 1


def test_fetch_rejects_unsupported_content_type(
    allow_public_test_urls: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"png",
            request=request,
        )

    fetcher, client = _fetcher(handler, max_retries=0)
    try:
        with pytest.raises(WebFetchError) as caught:
            fetcher.fetch("https://example.com/image")
    finally:
        fetcher.close()
        client.close()

    assert caught.value.code == "unsupported_content_type"


def test_pdf_without_extractable_text_fails_closed(
    allow_public_test_urls: None,
) -> None:
    stream = io.BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(stream)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/pdf"},
            content=stream.getvalue(),
            request=request,
        )

    fetcher, client = _fetcher(handler, max_retries=0)
    try:
        with pytest.raises(WebFetchError) as caught:
            fetcher.fetch("https://example.com/empty.pdf")
    finally:
        fetcher.close()
        client.close()

    assert caught.value.code == "empty_document"


def test_fetch_result_is_recorded_as_a_verified_page_document(
    allow_public_test_urls: None,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            content=b"A sufficiently long document body for the quality gate.",
            request=request,
        )

    fetcher, client = _fetcher(handler, max_retries=0)
    control = RunControl(
        ResearchLoopPolicy(min_usable_sources=1, max_candidate_sources=2, min_content_length=10)
    )
    url = "https://example.com/source"
    control.record_search(
        [{"title": "Source", "url": url, "snippet": "snippet", "provider": "test"}]
    )
    try:
        value = fetcher.fetch(url)
    finally:
        fetcher.close()
        client.close()

    assert control.record_fetch(url, value) is True
    source = control.evidence()[0]
    assert source["document_id"].startswith("document-")
    assert source["extraction_method"] == "plain_text"
    assert source["content_hash"]
