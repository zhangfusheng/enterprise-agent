import httpx
import pytest

from enterprise_agent.tools.web.provider import DuckDuckGoSearchProvider

_DDG = """
<div class="result">
  <a class="result__a" href="https://example.com/duck">Duck title</a>
  <span class="result__snippet">duck snippet</span>
</div>
"""
_BING = """
<li class="b_algo">
  <h2><a href="https://example.com/bing">Bing title</a></h2>
  <div class="b_caption"><p>bing snippet</p></div>
</li>
<li class="b_algo">
  <h2><a href="/relative">skip</a></h2>
</li>
"""


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


def test_duckduckgo_html_is_used_when_the_connection_works(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    def get(url: str, **kwargs: object) -> _Response:
        del kwargs
        calls.append(url)
        return _Response(_DDG)

    monkeypatch.setattr(httpx, "get", get)
    items = DuckDuckGoSearchProvider().search("python", limit=5)

    assert calls == ["https://html.duckduckgo.com/html/"]
    assert [(item.title, item.url, item.metadata["provider"]) for item in items] == [
        ("Duck title", "https://example.com/duck", "duckduckgo")
    ]


def test_connection_failure_reads_bing_without_changing_the_request_target(
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[str] = []

    def get(url: str, **kwargs: object) -> _Response:
        del kwargs
        calls.append(url)
        if "duckduckgo" in url:
            raise httpx.ConnectError("network is unreachable")
        return _Response(_BING)

    monkeypatch.setattr(httpx, "get", get)
    items = DuckDuckGoSearchProvider().search("python", limit=5)

    assert calls == ["https://html.duckduckgo.com/html/", "https://cn.bing.com/search"]
    assert [(item.title, item.url, item.snippet, item.metadata["provider"]) for item in items] == [
        ("Bing title", "https://example.com/bing", "bing snippet", "bing")
    ]
