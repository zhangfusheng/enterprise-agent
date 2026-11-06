"""Deterministic document extraction for fetched web responses."""

from __future__ import annotations

import codecs
import hashlib
import io
import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from pypdf import PdfReader

from enterprise_agent.agents.research.schemas import PageDocument
from enterprise_agent.tools.web.fetch import FetchResponse, WebFetchError

_TEXT_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "blockquote", "pre")
_NOISE_TAGS = (
    "aside",
    "button",
    "canvas",
    "dialog",
    "form",
    "footer",
    "header",
    "iframe",
    "nav",
    "noscript",
    "script",
    "style",
    "svg",
    "template",
)
_DATE_KEYS = {
    "datecreated": "published_at",
    "datepublished": "published_at",
    "datemodified": "modified_at",
    "published_time": "published_at",
    "modified_time": "modified_at",
    "article:published_time": "published_at",
    "article:modified_time": "modified_at",
}


class ContentExtractor(Protocol):
    def supports(self, content_type: str) -> bool: ...

    def extract(
        self,
        response: FetchResponse,
        *,
        max_text_chars: int,
        max_pdf_pages: int,
    ) -> ExtractionResult: ...


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    document: PageDocument
    warnings: tuple[str, ...] = ()


class ExtractorRegistry:
    def __init__(self, extractors: Sequence[ContentExtractor]) -> None:
        if not extractors:
            raise ValueError("extractor registry must not be empty")
        self._extractors = tuple(extractors)

    @classmethod
    def default(cls) -> ExtractorRegistry:
        return cls((HtmlExtractor(), PlainTextExtractor(), PdfExtractor()))

    def extract(
        self,
        response: FetchResponse,
        *,
        max_text_chars: int,
        max_pdf_pages: int,
    ) -> ExtractionResult:
        for extractor in self._extractors:
            if extractor.supports(response.content_type):
                return extractor.extract(
                    response,
                    max_text_chars=max_text_chars,
                    max_pdf_pages=max_pdf_pages,
                )
        raise WebFetchError(
            f"no extractor for content type: {response.content_type}",
            code="unsupported_content_type",
            url=response.final_url,
        )


class HtmlExtractor:
    def supports(self, content_type: str) -> bool:
        return content_type in {"text/html", "application/xhtml+xml"}

    def extract(
        self,
        response: FetchResponse,
        *,
        max_text_chars: int,
        max_pdf_pages: int,
    ) -> ExtractionResult:
        del max_pdf_pages
        text, warnings = _decode(response.body, response.headers.get("content-type", ""))
        soup = BeautifulSoup(text, "html.parser")
        title = _html_title(soup)
        canonical_url = _canonical_url(soup, response.final_url)
        published_at, modified_at = _html_dates(soup)
        for node in soup.find_all(_NOISE_TAGS):
            node.decompose()
        root = soup.find("main") or soup.find("article") or soup.body or soup
        paragraphs = _html_paragraphs(root)
        if not paragraphs:
            paragraphs = _line_paragraphs(root.get_text("\n", strip=True))
        paragraphs, truncated = _limit_paragraphs(paragraphs, max_text_chars)
        if truncated:
            warnings.append("content_truncated")
        return ExtractionResult(
            document=_document(
                response,
                title=title,
                canonical_url=canonical_url,
                content="\n\n".join(paragraphs),
                paragraphs=paragraphs,
                extraction_method="html_main_content",
                published_at=published_at,
                modified_at=modified_at,
            ),
            warnings=tuple(warnings),
        )


class PlainTextExtractor:
    def supports(self, content_type: str) -> bool:
        return content_type == "text/plain"

    def extract(
        self,
        response: FetchResponse,
        *,
        max_text_chars: int,
        max_pdf_pages: int,
    ) -> ExtractionResult:
        del max_pdf_pages
        text, warnings = _decode(response.body, response.headers.get("content-type", ""))
        paragraphs, truncated = _limit_paragraphs(_line_paragraphs(text), max_text_chars)
        if truncated:
            warnings.append("content_truncated")
        return ExtractionResult(
            document=_document(
                response,
                title="",
                canonical_url=None,
                content="\n\n".join(paragraphs),
                paragraphs=paragraphs,
                extraction_method="plain_text",
            ),
            warnings=tuple(warnings),
        )


class PdfExtractor:
    def supports(self, content_type: str) -> bool:
        return content_type == "application/pdf"

    def extract(
        self,
        response: FetchResponse,
        *,
        max_text_chars: int,
        max_pdf_pages: int,
    ) -> ExtractionResult:
        try:
            reader = PdfReader(io.BytesIO(response.body))
        except Exception as exc:
            raise WebFetchError(
                "PDF could not be parsed",
                code="decode_error",
                url=response.final_url,
            ) from exc
        if len(reader.pages) > max_pdf_pages:
            raise WebFetchError(
                "PDF page count exceeds limit",
                code="body_too_large",
                url=response.final_url,
            )
        page_text: list[str] = []
        for page in reader.pages:
            try:
                value = page.extract_text() or ""
            except Exception as exc:
                raise WebFetchError(
                    "PDF text extraction failed",
                    code="decode_error",
                    url=response.final_url,
                ) from exc
            normalized = _normalize_text(value)
            if normalized:
                page_text.append(normalized)
        paragraphs, truncated = _limit_paragraphs(page_text, max_text_chars)
        if not paragraphs:
            raise WebFetchError(
                "PDF contains no extractable text",
                code="empty_document",
                url=response.final_url,
            )
        warnings = ["content_truncated"] if truncated else []
        title = ""
        if reader.metadata is not None and reader.metadata.title:
            title = _normalize_text(reader.metadata.title)
        return ExtractionResult(
            document=_document(
                response,
                title=title,
                canonical_url=None,
                content="\n\n".join(paragraphs),
                paragraphs=paragraphs,
                extraction_method="pdf_text",
            ),
            warnings=tuple(warnings),
        )


def _document(
    response: FetchResponse,
    *,
    title: str,
    canonical_url: str | None,
    content: str,
    paragraphs: list[str],
    extraction_method: str,
    published_at: datetime | None = None,
    modified_at: datetime | None = None,
) -> PageDocument:
    normalized_content = _normalize_text(content)
    content_hash = hashlib.sha256(normalized_content.encode("utf-8")).hexdigest()
    document_id = f"document-{hashlib.sha256((canonical_url or response.final_url).encode('utf-8')).hexdigest()[:16]}"
    if not normalized_content:
        raise WebFetchError("document has no extractable text", code="empty_document", url=response.final_url)
    return PageDocument(
        document_id=document_id,
        original_url=response.original_url,
        final_url=response.final_url,
        canonical_url=canonical_url,
        title=_normalize_text(title),
        content=normalized_content,
        paragraphs=paragraphs,
        content_hash=content_hash,
        content_type=response.content_type,
        extraction_method=extraction_method,
        published_at=published_at,
        modified_at=modified_at,
        fetched_at=response.fetched_at,
    )


def _decode(body: bytes, content_type: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    charset = _charset(content_type)
    if charset is None:
        charset = _html_charset(body)
    if charset is None:
        charset = "utf-8"
    try:
        codecs.lookup(charset)
        return body.decode(charset), warnings
    except (LookupError, UnicodeDecodeError):
        warnings.append("encoding_fallback")
        return body.decode("utf-8", errors="replace"), warnings


def _charset(content_type: str) -> str | None:
    for part in content_type.split(";")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "charset" and value.strip():
            return value.strip().strip('"').strip("'")
    return None


def _html_charset(body: bytes) -> str | None:
    sample = body[:8192].decode("ascii", errors="ignore")
    match = re.search(r"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)", sample, re.IGNORECASE)
    return match.group(1) if match else None


def _html_title(soup: BeautifulSoup) -> str:
    for node in (
        soup.find("meta", property="og:title"),
        soup.find("meta", attrs={"name": "twitter:title"}),
    ):
        content = node.get("content") if node is not None else None
        if isinstance(content, str) and content.strip():
            return _normalize_text(content)
    title = soup.find("title")
    if title:
        return _normalize_text(title.get_text(" ", strip=True))
    heading = soup.find("h1")
    return _normalize_text(heading.get_text(" ", strip=True)) if heading else ""


def _canonical_url(soup: BeautifulSoup, base_url: str) -> str | None:
    for link in soup.find_all("link"):
        rel = link.get("rel")
        if rel is None:
            continue
        rel_values = {str(item).casefold() for item in (rel if isinstance(rel, list) else [rel])}
        href = link.get("href")
        if "canonical" not in rel_values or not isinstance(href, str) or not href.strip():
            continue
        target = urljoin(base_url, href.strip())
        parsed = urlsplit(target)
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return target
    return None


def _html_dates(soup: BeautifulSoup) -> tuple[datetime | None, datetime | None]:
    values: dict[str, datetime] = {}
    for meta in soup.find_all("meta"):
        key = str(meta.get("property") or meta.get("name") or "").casefold()
        mapped = _DATE_KEYS.get(key)
        value = meta.get("content")
        if mapped and isinstance(value, str):
            parsed = _parse_datetime(value)
            if parsed is not None:
                values.setdefault(mapped, parsed)
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        for key, value in _walk_date_values(payload):
            parsed = _parse_datetime(value)
            if parsed is not None:
                values.setdefault(key, parsed)
    return values.get("published_at"), values.get("modified_at")


def _walk_date_values(value: object) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            mapped = _DATE_KEYS.get(str(key).casefold())
            if mapped and isinstance(item, str):
                yield mapped, item
            yield from _walk_date_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_date_values(item)


def _parse_datetime(value: str) -> datetime | None:
    candidate = value.strip()
    if not candidate:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(candidate)
        except (TypeError, ValueError, IndexError):
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _html_paragraphs(root: object) -> list[str]:
    if not isinstance(root, BeautifulSoup) and not hasattr(root, "find_all"):
        return []
    values: list[str] = []
    for node in root.find_all(_TEXT_TAGS):
        value = _normalize_text(node.get_text(" ", strip=True))
        if value and value not in values:
            values.append(value)
    return values


def _line_paragraphs(text: str) -> list[str]:
    values = [_normalize_text(item) for item in re.split(r"\n\s*\n|\r\n|\n", text)]
    return [item for item in values if item]


def _limit_paragraphs(paragraphs: list[str], max_chars: int) -> tuple[list[str], bool]:
    limited: list[str] = []
    used = 0
    truncated = False
    for paragraph in paragraphs:
        separator = 2 if limited else 0
        if used + separator + len(paragraph) <= max_chars:
            limited.append(paragraph)
            used += separator + len(paragraph)
            continue
        remaining = max_chars - used - separator
        if remaining > 0:
            limited.append(paragraph[:remaining].rstrip())
        truncated = True
        break
    return [item for item in limited if item], truncated


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


__all__ = [
    "ContentExtractor",
    "ExtractionResult",
    "ExtractorRegistry",
    "HtmlExtractor",
    "PdfExtractor",
    "PlainTextExtractor",
]
