from dataclasses import dataclass
from typing import Self

from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.contracts.routing import RouteDecision, RouteKind

_SEARCH = frozenset[str]({"web_search", "web_fetch"})
_ANALYSIS = frozenset({"analysis", "calculate", "save_report"})
_RESEARCH_ROLE = "research"
_ANALYSIS_ROLE = "analysis"

_DIRECT_ANSWER = "目标没有工具、实时信息、交付物或外部操作要求，可直接回答。"
_SPLIT_WORK = "目标同时需要外部证据和分析/交付，拆为有依赖的 Research→Analysis 任务。"
_SINGLE_WORKER = "目标可由一个具备所需能力的 Worker 完成。"


@dataclass(frozen=True, slots=True)
class _Demand:
    """Facts derived once from a GoalSpec and reused by every route branch."""

    capabilities: frozenset[str]
    has_deliverables: bool

    @classmethod
    def from_spec(cls, spec: GoalSpec) -> Self:
        capabilities = frozenset(
            capability for goal in spec.goals for capability in goal.required_capabilities
        )
        if spec.deliverables:
            capabilities = capabilities | {"analysis"}
        return cls(capabilities=capabilities, has_deliverables=bool(spec.deliverables))

    @property
    def required(self) -> list[str]:
        return sorted(self.capabilities)

    @property
    def search(self) -> bool:
        return bool(self.capabilities & _SEARCH)

    @property
    def analysis_work(self) -> bool:
        return self.has_deliverables or bool(self.capabilities & _ANALYSIS)

    @property
    def can_answer_directly(self) -> bool:
        return not self.capabilities and not self.has_deliverables


class Router:
    def decide(self, spec: GoalSpec, requested_role: str | None = None) -> RouteDecision:
        if spec.is_waiting_input:
            raise ValueError("waiting input is not a route")
        demand = _Demand.from_spec(spec)
        if demand.can_answer_directly:
            return _direct(_DIRECT_ANSWER)
        if demand.search and demand.analysis_work:
            return _multi(demand)
        return _single(demand, requested_role)


def _direct(rationale: str) -> RouteDecision:
    return RouteDecision(route=RouteKind.DIRECT, rationale=rationale)


def _multi(demand: _Demand) -> RouteDecision:
    return RouteDecision(
        route=RouteKind.MULTI,
        required_capabilities=demand.required,
        rationale=_SPLIT_WORK,
        requires_external_operation=True,
        requires_realtime_information=True,
    )


def _single(demand: _Demand, requested_role: str | None) -> RouteDecision:
    role = requested_role or (_RESEARCH_ROLE if demand.search else _ANALYSIS_ROLE)
    external = demand.search
    return RouteDecision(
        route=RouteKind.SINGLE,
        required_capabilities=demand.required,
        selected_role=role,
        rationale=_SINGLE_WORKER,
        requires_external_operation=external,
        requires_realtime_information=external,
    )
