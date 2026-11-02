from enterprise_agent.tools.web.provider import SearchProvider


def web_search(provider: SearchProvider, query: str, limit: int = 5) -> list[dict]:
    if not query.strip():
        raise ValueError("query must not be blank")
    return [item.__dict__ for item in provider.search(query, max(1, min(limit, 20)))]

