from enterprise_agent.tools.web.provider import SearchProvider, SearchRequest


def web_search(
    provider: SearchProvider,
    query: str,
    limit: int = 5,
    *,
    max_limit: int = 20,
) -> list[dict]:
    request = SearchRequest(query=query, limit=max(1, min(limit, max_limit, 20)))
    response = provider.search(request)
    return [item.model_dump(mode="json") for item in response.results]

