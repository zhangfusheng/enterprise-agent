from langchain.agents.middleware import TodoListMiddleware

from enterprise_agent.agents.harness.planning import explicit_local_planning_middleware


def worker_middleware() -> list[TodoListMiddleware]:
    """Todo planning for workers that keep the Deep Agents built-in tools."""
    return [explicit_local_planning_middleware()]

