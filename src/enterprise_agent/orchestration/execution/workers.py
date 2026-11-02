from collections.abc import Mapping, Sequence
from typing import Protocol

from enterprise_agent.agents.analysis.definition import AnalysisAgent
from enterprise_agent.agents.analysis.schemas import AnalysisRequest
from enterprise_agent.agents.delegation.service import DelegationService
from enterprise_agent.agents.research.definition import ResearchAgent
from enterprise_agent.agents.research.schemas import ResearchEvidence, ResearchRequest
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.plans import TaskSpec
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.tools.gateway import ToolGateway


class _GatewayHost(Protocol):
    gateway: ToolGateway | None


class TaskWorker(Protocol):
    """One role's task execution. Register an implementation to add a role."""

    role: str

    @property
    def bound_agents(self) -> Sequence[_GatewayHost]:
        """Agents whose tool gateway is scoped to the current run."""

    def execute(
        self,
        task: TaskSpec,
        context: ExecutionContext,
        results: Mapping[str, AgentResult],
        *,
        delegate: bool,
        agent_count: int,
    ) -> AgentResult: ...


class WorkerRegistry:
    def __init__(self, workers: Sequence[TaskWorker] = ()):
        self._workers: dict[str, TaskWorker] = {}
        for worker in workers:
            self.register(worker)

    def register(self, worker: TaskWorker) -> None:
        if worker.role in self._workers:
            raise ValueError(f"duplicate worker role: {worker.role}")
        self._workers[worker.role] = worker

    def execute(
        self,
        task: TaskSpec,
        context: ExecutionContext,
        results: Mapping[str, AgentResult],
        *,
        delegate: bool,
        agent_count: int,
    ) -> AgentResult:
        worker = self._workers.get(task.agent_role)
        if worker is None:
            return AgentResult(
                status=ResultStatus.FAILED,
                summary="未知 Worker 角色",
                errors=[task.agent_role],
            )
        return worker.execute(
            task,
            context,
            results,
            delegate=delegate,
            agent_count=agent_count,
        )

    def bind_run(self, ledger: BudgetLedger, cancellation: CancellationToken) -> None:
        """Give this run its own gateway so tool budgets cannot leak across runs."""
        agents: list[_GatewayHost] = []
        seen: set[int] = set()
        template: ToolGateway | None = None
        for worker in self._workers.values():
            for agent in worker.bound_agents:
                if id(agent) in seen:
                    continue
                seen.add(id(agent))
                agents.append(agent)
                if template is None and isinstance(agent.gateway, ToolGateway):
                    template = agent.gateway
        if template is None:
            return
        scoped = ToolGateway(
            template.registry,
            ledger,
            cancellation,
            {},
            operations=template.operations,
        )
        for agent in agents:
            agent.gateway = scoped

    def roles(self) -> set[str]:
        return set[str](self._workers)

    def tool_scopes(self) -> set[str]:
        for worker in self._workers.values():
            for agent in worker.bound_agents:
                gateway = getattr(agent, "gateway", None)
                if isinstance(gateway, ToolGateway):
                    return gateway.registry.scopes()
        return set()


class ResearchWorker:
    role = "research"

    def __init__(self, agent: ResearchAgent):
        self._agent = agent

    @property
    def bound_agents(self) -> tuple[ResearchAgent, ...]:
        return (self._agent,)

    def execute(
        self,
        task: TaskSpec,
        context: ExecutionContext,
        results: Mapping[str, AgentResult],
        *,
        delegate: bool,
        agent_count: int,
    ) -> AgentResult:
        del delegate, agent_count
        previous = results.get(task.task_id)
        historical = _historical_evidence(previous)
        return self._agent.run(
            ResearchRequest(
                query=task.task_description,
                acceptance_criteria=list(task.acceptance_criteria),
                historical_evidence=historical,
                excluded_urls=[item.url for item in historical],
            ),
            _role_context(context, self.role, task.task_id),
        )


class AnalysisWorker:
    role = "analysis"

    def __init__(self, agent: AnalysisAgent, delegation: DelegationService):
        self._agent = agent
        self._delegation = delegation

    @property
    def bound_agents(self) -> tuple[AnalysisAgent, ...]:
        agents = [self._agent]
        if self._delegation.analysis_agent is not self._agent:
            agents.append(self._delegation.analysis_agent)
        return tuple(agents)

    def execute(
        self,
        task: TaskSpec,
        context: ExecutionContext,
        results: Mapping[str, AgentResult],
        *,
        delegate: bool,
        agent_count: int,
    ) -> AgentResult:
        evidence = _evidence(task, results)
        scoped = _role_context(context, self.role, task.task_id)
        if delegate:
            return self._delegation.delegate_analysis(
                scoped,
                task.task_description,
                evidence,
                agent_count,
            )
        return self._agent.run(
            AnalysisRequest(objective=task.task_description, evidence=evidence),
            scoped,
        )


def _role_context(context: ExecutionContext, role: str, task_id: str) -> ExecutionContext:
    return context.model_copy(
        update={
            "agent_id": f"agent_{role}",
            "metadata": {**context.metadata, "role": role, "task_id": task_id},
        }
    )


def _evidence(task: TaskSpec, results: Mapping[str, AgentResult]) -> list[dict]:
    evidence: list[dict] = []
    for dependency in task.depends_on:
        result = results.get(dependency)
        if result is None:
            continue
        evidence.extend(result.structured_data.get("evidence", []))
    return evidence


def _historical_evidence(result: AgentResult | None) -> list[ResearchEvidence]:
    if result is None:
        return []
    candidates: list[object] = [*result.sources]
    raw = result.structured_data.get("evidence")
    if isinstance(raw, list):
        candidates.extend(raw)
    history: list[ResearchEvidence] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        title = item.get("title")
        url = item.get("url")
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str) or not url.strip():
            continue
        key = url.strip().casefold().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        history.append(
            ResearchEvidence(
                title=title,
                url=url,
                snippet=item.get("snippet", "") if isinstance(item.get("snippet", ""), str) else "",
                content=item.get("content") if isinstance(item.get("content"), str) else None,
            )
        )
    return history
