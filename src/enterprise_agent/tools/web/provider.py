from dataclasses import dataclass
from typing import Protocol

import httpx
from bs4 import BeautifulSoup

_DDG_URL = "https://html.duckduckgo.com/html/"
_BING_URL = "https://cn.bing.com/search"
_USER_AGENT = "enterprise-agent/0.1"


@dataclass(frozen=True)
class SearchItem:
    title: str
    url: str
    snippet: str
    metadata: dict[str, str]


class SearchProvider(Protocol):
    def search(self, query: str, limit: int = 5) -> list[SearchItem]: ...


class OfflineSearchProvider:
    """Deterministic local source used when external network is disabled."""

    def search(self, query: str, limit: int = 5) -> list[SearchItem]:
        return [
            SearchItem(
                title="离线 fixture 来源",
                url="https://fixture.invalid/enterprise-agent",
                snippet=f"离线检索：{query}",
                metadata={"provider": "offline"},
            )
        ][: max(1, limit)]


class DuckDuckGoSearchProvider:
    """HTML search adapter used only by this process.

    DuckDuckGo remains the first request. A connection failure is read from
    Bing inside this client. System DNS and other programs stay unchanged.
    """

    def __init__(self, timeout: float = 10):
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> list[SearchItem]:
        try:
            html = _get(_DDG_URL, {"q": query}, self.timeout, follow_redirects=False)
            return _duckduckgo_items(html, limit)
        except httpx.TransportError:
            html = _get(_BING_URL, {"q": query}, self.timeout, follow_redirects=True)
            return _bing_items(html, limit)


def _get(url: str, params: dict[str, str], timeout: float, *, follow_redirects: bool) -> str:
    response = httpx.get(
        url,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=httpx.Timeout(timeout, connect=min(timeout, 4.0)),
        follow_redirects=follow_redirects,
    )
    response.raise_for_status()
    return response.text


def _duckduckgo_items(html: str, limit: int) -> list[SearchItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[SearchItem] = []
    for result in soup.select(".result")[:limit]:
        link = result.select_one(".result__a")
        snippet = result.select_one(".result__snippet")
        if link and link.get("href"):
            items.append(SearchItem(
                title=link.get_text(" ", strip=True),
                url=str(link["href"]),
                snippet=snippet.get_text(" ", strip=True) if snippet else "",
                metadata={"provider": "duckduckgo"},
            ))
    return items


def _bing_items(html: str, limit: int) -> list[SearchItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: list[SearchItem] = []
    for result in soup.select("li.b_algo"):
        if len(items) >= limit:
            break
        link = result.select_one("h2 a")
        snippet = result.select_one(".b_caption p")
        href = str(link["href"]) if link and link.get("href") else ""
        if link is None or not href.startswith(("http://", "https://")):
            continue
        items.append(SearchItem(
            title=link.get_text(" ", strip=True),
            url=href,
            snippet=snippet.get_text(" ", strip=True) if snippet else "",
            metadata={"provider": "bing"},
        ))
    return items
