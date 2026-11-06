"""Deterministic search fixture used only by offline development and tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from enterprise_agent.agents.research.schemas import SearchCandidate
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
