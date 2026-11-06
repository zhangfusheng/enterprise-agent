"""Agent-facing adapter for the plan-driven research core."""

from enterprise_agent.agents.research.core import ResearchCore
from enterprise_agent.agents.research.planning import ResearchPlanner
from enterprise_agent.agents.research.schemas import ResearchRequest
from enterprise_agent.agents.research.tools import ResearchLoopPolicy
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import AgentResult
from enterprise_agent.infrastructure.observability.tracing import trace
from enterprise_agent.tools.gateway import ToolGateway
from enterprise_agent.tools.web.fetch import WebFetcher
from enterprise_agent.tools.web.provider import SearchProvider


class ResearchAgent:
    """Generate and execute one bounded ResearchPlan."""

    role = "research"
    search_tool = "web_search"
    fetch_tool = "web_fetch"

    def __init__(
        self,
        provider: SearchProvider,
        fetcher: WebFetcher,
        *,
        planner: ResearchPlanner,
        allow_fetch: bool = True,
        gateway: ToolGateway | None = None,
        skip_fetch_prefixes: tuple[str, ...] = (),
        loop_policy: ResearchLoopPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.fetcher = fetcher
        self.planner = planner
        self.allow_fetch = allow_fetch
        self.gateway = gateway
        self.skip_fetch_prefixes = tuple(skip_fetch_prefixes)
        self.loop_policy = loop_policy or ResearchLoopPolicy()
        self.core = ResearchCore(
            provider,
            fetcher,
            planner=planner,
            allow_fetch=allow_fetch,
            skip_fetch_prefixes=self.skip_fetch_prefixes,
            loop_policy=self.loop_policy,
        )

    def run(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        *,
        gateway: ToolGateway | None = None,
    ) -> AgentResult:
        """Create a ResearchPlan and execute it through the bound gateway."""
        with trace(
            "research_plan",
            "chain",
            inputs={"query": request.query, "planner_version": "research-plan-v1"},
            tags=["research", "plan-driven"],
        ) as span:
            result = self.core.run(
                request,
                context,
                gateway=self.gateway if gateway is None else gateway,
            )
            metadata = result.structured_data.get("research", {})
            span.outputs = {
                "status": result.status.value,
                **(metadata if isinstance(metadata, dict) else {}),
            }
            return result


__all__ = ["ResearchAgent"]
