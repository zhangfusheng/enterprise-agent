from enterprise_agent.agents.research.schemas import ResearchRequest
from enterprise_agent.application.research import ResearchApplication
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.tools.gateway import ToolGateway


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="standalone-run",
        tenant_id="tenant",
        user_id="user",
        agent_id="caller",
        workspace_root=".",
    )


class _Agent:
    def __init__(self) -> None:
        self.calls: list[tuple[ResearchRequest, ExecutionContext, ToolGateway | None]] = []

    def run(self, request, context, *, gateway=None):
        self.calls.append((request, context, gateway))
        return AgentResult(
            status=ResultStatus.SUCCESS,
            summary="standalone result",
            agent_id="agent_research",
        )


def test_standalone_entrypoint_uses_a_fresh_run_scoped_gateway():
    agent = _Agent()
    created: list[tuple[BudgetLedger | None, CancellationToken | None]] = []
    gateway = object()

    def factory(ledger, cancellation):
        created.append((ledger, cancellation))
        return gateway

    application = ResearchApplication(agent, factory)
    ledger = object()
    cancellation = object()

    result = application.run(
        ResearchRequest(query="独立研究"),
        _context(),
        ledger=ledger,
        cancellation=cancellation,
    )

    assert result.status == ResultStatus.SUCCESS
    assert created == [(ledger, cancellation)]
    assert agent.calls[0][2] is gateway
    assert agent.calls[0][0].query == "独立研究"


def test_standalone_entrypoint_does_not_require_global_orchestration():
    agent = _Agent()
    gateway = object()
    application = ResearchApplication(agent, lambda ledger, cancellation: gateway)

    result = application.run(ResearchRequest(query="只执行 research"), _context())

    assert result.status == ResultStatus.SUCCESS
    assert len(agent.calls) == 1
