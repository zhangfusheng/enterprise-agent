"""Agent-facing adapter for the reusable research execution core."""

from enterprise_agent.agents.harness.deepagents import DeepAgentsHarness
from enterprise_agent.agents.research.core import (
    ResearchCore,
    build_research_prompt,
    merge_evidence,
    outcome,
    research_metadata,
    url_key,
)
from enterprise_agent.agents.research.schemas import ResearchRequest
from enterprise_agent.agents.research.tools import ResearchLoopPolicy
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import AgentResult
from enterprise_agent.infrastructure.observability.tracing import trace
from enterprise_agent.tools.gateway import ToolGateway
from enterprise_agent.tools.web.provider import SearchProvider

_FIXTURE_URL_PREFIX = "https://fixture.invalid"


class ResearchAgent:
    """Adapt ``ResearchCore`` to the current Agent/Worker runtime."""

    role = "research"
    search_tool = "web_search"
    fetch_tool = "web_fetch"

    def __init__(
        self,
        provider: SearchProvider,
        allow_fetch: bool = True,
        gateway: ToolGateway | None = None,
        skip_fetch_prefixes: tuple[str, ...] = (_FIXTURE_URL_PREFIX,),
        *,
        harness: DeepAgentsHarness,
        loop_policy: ResearchLoopPolicy | None = None,
        core: ResearchCore | None = None,
    ) -> None:
        self.provider = provider
        self.allow_fetch = allow_fetch
        self.harness = harness
        self.gateway = gateway
        self.skip_fetch_prefixes = tuple(skip_fetch_prefixes)
        if core is None:
            self.loop_policy = loop_policy or ResearchLoopPolicy()
            self.core = ResearchCore(
                provider,
                harness=harness,
                allow_fetch=allow_fetch,
                skip_fetch_prefixes=self.skip_fetch_prefixes,
                loop_policy=self.loop_policy,
            )
        else:
            self.core = core
            self.loop_policy = core.loop_policy

    def run(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        *,
        gateway: ToolGateway | None = None,
    ) -> AgentResult:
        """Run research with either the bound or an explicitly scoped gateway.

        The optional gateway lets an application entrypoint create an isolated
        budget/cancellation/idempotency scope without mutating the agent. The
        WorkerRegistry continues to pass the run-scoped gateway through the
        agent's bound gateway for multi-agent executions.
        """
        with trace(
            "research_loop",
            "chain",
            inputs={"query": request.query, "limits": self.loop_policy.snapshot()},
            tags=["research", "loop-policy"],
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


def _task_prompt(
    request: ResearchRequest,
    allow_fetch: bool,
    skip_prefixes: tuple[str, ...],
    policy: ResearchLoopPolicy | None = None,
) -> str:
    """Compatibility wrapper for callers that imported the old helper."""
    return build_research_prompt(request, allow_fetch, skip_prefixes, policy)


# Keep the old private names importable while the implementation lives in Core.
_merge_evidence = merge_evidence
_outcome = outcome
_research_metadata = research_metadata
_url_key = url_key


__all__ = ["ResearchAgent", "_task_prompt"]
