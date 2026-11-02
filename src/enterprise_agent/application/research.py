"""Internal application entrypoint for one independent research request."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from enterprise_agent.agents.research.schemas import ResearchRequest
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import AgentResult
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.tools.gateway import ToolGateway


class ResearchRunner(Protocol):
    """The small agent contract needed by the independent entrypoint."""

    def run(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        *,
        gateway: ToolGateway | None = None,
    ) -> AgentResult: ...


GatewayFactory = Callable[
    [BudgetLedger | None, CancellationToken | None],
    ToolGateway,
]


class ResearchApplication:
    """Run one research request without global planning or orchestration.

    This is an in-process application boundary, not an HTTP endpoint. It owns
    the run-scoped tool gateway and delegates research semantics to the agent
    and ``ResearchCore``. A caller must provide an authenticated execution
    context; the application never invents tenant or user identity.
    """

    def __init__(self, agent: ResearchRunner, gateway_factory: GatewayFactory) -> None:
        self.agent = agent
        self._gateway_factory = gateway_factory

    def run(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        *,
        ledger: BudgetLedger | None = None,
        cancellation: CancellationToken | None = None,
    ) -> AgentResult:
        """Execute a standalone research request in an isolated run scope."""
        gateway = self._gateway_factory(ledger, cancellation)
        return self.agent.run(request, context, gateway=gateway)


__all__ = ["GatewayFactory", "ResearchApplication", "ResearchRunner"]
