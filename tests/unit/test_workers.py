import pytest

from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.plans import TaskSpec
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.orchestration.execution.workers import (
    AnalysisWorker,
    ResearchWorker,
    WorkerRegistry,
)
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.tools.contracts import ToolResult, ToolSpec, ToolStatus
from enterprise_agent.tools.gateway import ToolGateway
from enterprise_agent.tools.registry import ToolRegistry


def _task(role: str, description: str = "collect evidence", depends_on: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        task_id="task_worker",
        goal_ids=["goal_main"],
        agent_role=role,
        task_description=description,
        depends_on=depends_on or [],
        expected_output="result",
    )


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_supervisor",
        workspace_root=".",
        metadata={"role": "research", "scopes": {"web"}},
    )


class _Research:
    def __init__(self):
        self.gateway = None
        self.request = None
        self.context = None

    def run(self, request, context):
        self.request = request
        self.context = context
        return AgentResult(status=ResultStatus.SUCCESS, summary=request.query)


class _Analysis:
    def __init__(self):
        self.gateway = None
        self.request = None

    def run(self, request, context):
        del context
        self.request = request
        return AgentResult(status=ResultStatus.SUCCESS, summary=request.objective)


class _Delegation:
    def __init__(self, agent):
        self.analysis_agent = agent
        self.calls = []

    def delegate_analysis(self, parent, objective, evidence, agent_count):
        self.calls.append((parent, objective, evidence, agent_count))
        return AgentResult(status=ResultStatus.SUCCESS, summary="delegated")


class _Echo:
    role = "echo"
    bound_agents: tuple = ()

    def execute(self, task, context, results, *, delegate, agent_count):
        del context, results, delegate, agent_count
        return AgentResult(status=ResultStatus.SUCCESS, summary=task.task_description)


def test_registry_dispatches_registered_role_and_rejects_unknown():
    agent = _Research()
    registry = WorkerRegistry([ResearchWorker(agent)])
    registry.register(_Echo())

    echo = registry.execute(_task("echo", "hello"), _context(), {}, delegate=False, agent_count=1)
    research = registry.execute(_task("research", "find sources"), _context(), {}, delegate=False, agent_count=1)
    unknown = registry.execute(_task("billing"), _context(), {}, delegate=False, agent_count=1)

    assert echo.summary == "hello"
    assert research.summary == "find sources"
    assert agent.context.agent_id == "agent_research"
    assert unknown.status == ResultStatus.FAILED
    assert unknown.errors == ["billing"]


def test_research_worker_passes_acceptance_history_and_exclusions():
    agent = _Research()
    worker = ResearchWorker(agent)
    task = _task("research", "find new sources")
    task = task.model_copy(update={"acceptance_criteria": ["至少两个独立来源"]})
    previous = AgentResult(
        status=ResultStatus.PARTIAL,
        sources=[
            {
                "title": "Old source",
                "url": "https://example.com/old",
                "snippet": "old evidence",
            }
        ],
    )

    worker.execute(
        task,
        _context(),
        {task.task_id: previous},
        delegate=False,
        agent_count=1,
    )

    assert agent.request.acceptance_criteria == ["至少两个独立来源"]
    assert [item.url for item in agent.request.historical_evidence] == [
        "https://example.com/old"
    ]
    assert agent.request.excluded_urls == ["https://example.com/old"]


def test_registry_rejects_duplicate_role():
    registry = WorkerRegistry([ResearchWorker(_Research())])
    with pytest.raises(ValueError, match="duplicate worker role"):
        registry.register(ResearchWorker(_Research()))


def test_analysis_reads_dependency_evidence_and_can_delegate():
    agent = _Analysis()
    delegation = _Delegation(agent)
    worker = AnalysisWorker(agent, delegation)
    prior = {
        "task_research": AgentResult(
            status=ResultStatus.SUCCESS,
            structured_data={"evidence": [{"title": "source"}]},
        )
    }
    task = _task("analysis", "write the report", depends_on=["task_research"])

    direct = worker.execute(task, _context(), prior, delegate=False, agent_count=2)
    delegated = worker.execute(task, _context(), prior, delegate=True, agent_count=2)

    assert direct.summary == "write the report"
    assert agent.request.evidence == [{"title": "source"}]
    assert delegated.summary == "delegated"
    assert delegation.calls[0][1:] == ("write the report", [{"title": "source"}], 2)


def test_bind_run_replaces_gateway_on_every_bound_agent():
    tools = ToolRegistry()
    tools.register(ToolSpec(name="web_search", description="search", required_scopes={"web"}), lambda query: query)
    original = ToolGateway(
        tools,
        BudgetLedger(BudgetLimits(model_calls=1, tool_calls=1, agents=1, steps=1, runtime_seconds=30)),
        CancellationToken(),
        {"stale": ToolResult(status=ToolStatus.SUCCESS, value=1)},
    )
    research = _Research()
    research.gateway = original
    analysis = _Analysis()
    registry = WorkerRegistry([
        ResearchWorker(research),
        AnalysisWorker(analysis, _Delegation(analysis)),
    ])
    ledger = BudgetLedger(BudgetLimits(model_calls=2, tool_calls=2, agents=2, steps=2, runtime_seconds=30))
    cancellation = CancellationToken()

    registry.bind_run(ledger, cancellation)

    assert research.gateway is analysis.gateway
    assert research.gateway is not original
    assert research.gateway.registry is tools
    assert research.gateway.budget is ledger
    assert research.gateway.cancellation is cancellation
    assert research.gateway.idempotency_store == {}
    assert registry.tool_scopes() == {"web"}
