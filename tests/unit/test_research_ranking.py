from datetime import UTC, datetime, timedelta

from enterprise_agent.agents.research.ranking import (
    deduplicate_sources,
    evaluate_freshness,
    normalize_source_url,
    rank_sources,
)
from enterprise_agent.agents.research.schemas import FreshnessStatus, SourceType
from enterprise_agent.agents.research.tools import ResearchLoopPolicy, RunControl

_AS_OF = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)


def _source(url: str, **overrides):
    return {
        "candidate_id": overrides.pop("candidate_id", url.rsplit("/", 1)[-1]),
        "title": "Source",
        "url": url,
        "domain": "example.com",
        "provider": "tavily",
        "rank": overrides.pop("rank", 1),
        "source_type": overrides.pop("source_type", SourceType.UNKNOWN.value),
        **overrides,
    }


def test_normalize_source_url_removes_tracking_but_preserves_semantic_query():
    assert normalize_source_url(
        "HTTPS://Example.COM:443/path/?b=2&utm_source=news&a=1#fragment"
    ) == "https://example.com/path?a=1&b=2"
    assert normalize_source_url("https://example.com/path?session=abc") != normalize_source_url(
        "https://example.com/path?session=def"
    )


def test_deduplicate_sources_merges_canonical_alias_and_content_metadata():
    result = deduplicate_sources(
        [
            _source(
                "https://example.com/redirect",
                candidate_id="first",
                rank=2,
                content="A sufficiently long document body for deduplication.",
                content_hash="same-content",
            ),
            _source(
                "https://example.com/article",
                candidate_id="second",
                canonical_url="https://example.com/redirect",
                rank=1,
                content="A sufficiently long document body for deduplication.",
                content_hash="same-content",
            ),
        ]
    )

    assert len(result.sources) == 1
    assert result.sources[0]["candidate_id"] == "second"
    assert result.sources[0]["candidate_ids"] == ["first", "second"]
    assert result.sources[0]["duplicate_count"] == 1
    assert result.duplicates[0]["duplicate_of"] == "second"
    assert result.duplicates[0]["reason"] in {"canonical_url", "content_hash"}


def test_freshness_uses_published_then_modified_and_classifies_boundaries():
    assert evaluate_freshness(
        _source("https://example.com/fresh", published_at="2026-11-01T00:00:00+00:00"),
        as_of=_AS_OF,
        freshness_days=5,
    ).status == FreshnessStatus.FRESH
    assert evaluate_freshness(
        _source("https://example.com/stale", published_at="2026-10-31T23:59:59+00:00"),
        as_of=_AS_OF,
        freshness_days=5,
    ).status == FreshnessStatus.STALE
    assert evaluate_freshness(
        _source("https://example.com/modified", modified_at="2026-11-05T00:00:00+00:00"),
        as_of=_AS_OF,
        freshness_days=5,
    ).status == FreshnessStatus.FRESH
    assert evaluate_freshness(
        _source("https://example.com/unknown"),
        as_of=_AS_OF,
        freshness_days=5,
    ).status == FreshnessStatus.UNKNOWN
    assert evaluate_freshness(
        _source("https://example.com/future", published_at="2026-11-07T00:00:00+00:00"),
        as_of=_AS_OF,
        freshness_days=5,
    ).status == FreshnessStatus.FUTURE
    assert evaluate_freshness(
        _source("https://example.com/invalid", published_at="not-a-date"),
        as_of=_AS_OF,
        freshness_days=5,
    ).status == FreshnessStatus.INVALID


def test_rank_sources_is_deterministic_and_prefers_requested_domain_and_type():
    sources = [
        _source(
            "https://community.example.com/a",
            candidate_id="community",
            domain="community.example.com",
            score=0.99,
            source_type=SourceType.COMMUNITY.value,
            published_at=(_AS_OF - timedelta(days=1)).isoformat(),
        ),
        _source(
            "https://docs.example.com/b",
            candidate_id="official",
            domain="docs.example.com",
            score=0.8,
            source_type=SourceType.OFFICIAL_DOCS.value,
            published_at=(_AS_OF - timedelta(days=1)).isoformat(),
        ),
    ]

    ranked = rank_sources(
        sources,
        preferred_domains=["docs.example.com"],
        required_source_types=[SourceType.OFFICIAL_DOCS],
        freshness_days=7,
        as_of=_AS_OF,
    )

    assert ranked[0].source["candidate_id"] == "official"
    assert ranked[0].assessment.rank_score > ranked[1].assessment.rank_score
    assert ranked[0].assessment.score_breakdown["preferred_domain"] == 1.0


def test_run_control_retains_query_and_provider_provenance_for_duplicate_urls():
    control = RunControl(
        ResearchLoopPolicy(min_usable_sources=1, max_candidate_sources=3),
        stop_when_sufficient=False,
    )
    url = "https://example.com/source"
    control.record_search(
        [{"title": "Source", "url": url, "snippet": "one", "provider": "tavily"}]
    )
    control.record_search(
        [{"title": "Source", "url": f"{url}/?utm_source=search", "snippet": "two", "provider": "doubao"}]
    )
    control.mark_query_sources("query-1", [url])
    control.mark_query_sources("query-2", [f"{url}/?utm_source=search"])
    control.rank_candidates(as_of=_AS_OF)

    source = control.candidates()[0]
    assert source["query_ids"] == ["query-1", "query-2"]
    assert source["provider_hits"] == ["tavily", "doubao"]
    assert source["duplicate_count"] == 1
    assert control.snapshot()["duplicate_source_count"] == 1
