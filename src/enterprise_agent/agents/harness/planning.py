from langchain.agents.middleware import TodoListMiddleware


def explicit_local_planning_middleware() -> TodoListMiddleware:
    """Deep Agents 0.7 moved todo planning to LangChain middleware and made it opt-in."""
    return TodoListMiddleware()

