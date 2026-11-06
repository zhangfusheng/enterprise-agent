"""Deterministic search fixture used only by offline development and tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from enterprise_agent.agents.research.schemas import PageDocument, SearchCandidate
from enterprise_agent.tools.web.fetch import WebFetcher
from enterprise_agent.tools.web.provider import SearchRequest, SearchResponse


class FixtureSearchProvider:
    """Local fixture provider; never registered for production networking."""

    name = "fixture"

    def search(self, request: SearchRequest) -> SearchResponse:
        url = "https://fixture.invalid/enterprise-agent"
        digest = hashlib.sha256(f"fixture:{url}".encode()).hexdigest()[:16]
        candidate = SearchCandidate(
            candidate_id=f"candidate-{digest}",
            title="离线 fixture 来源",
            url=url,
            snippet=f"离线检索：{request.query}",
            provider=self.name,
            rank=1,
            domain="fixture.invalid",
            retrieved_at=datetime.now(UTC),
        )
        return SearchResponse(provider=self.name, results=(candidate,))


class FixtureWebFetcher(WebFetcher):
    """Deterministic document fetcher paired with ``FixtureSearchProvider``."""

    def fetch(self, url: str) -> dict[str, object]:
        if not url.startswith("https://fixture.invalid/"):
            raise ValueError("fixture fetcher only serves fixture.invalid")
        document_id = f"document-{hashlib.sha256(url.encode()).hexdigest()[:16]}"
        content = (
            "This deterministic offline research document exists only for tests. "
            "It provides stable source content so citation identity, exact quotes, "
            "and report gating can be exercised without external network access."
        )
        document = PageDocument(
            document_id=document_id,
            original_url=url,
            final_url=url,
            canonical_url=url,
            title="离线 fixture 来源",
            content=content,
            paragraphs=[content],
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            content_type="text/plain",
            extraction_method="offline_fixture",
            fetched_at=datetime.now(UTC),
        )
        return {"document": document.model_dump(mode="json")}

    def close(self) -> None:
        return None
