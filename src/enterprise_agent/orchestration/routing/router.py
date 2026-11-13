from dataclasses import dataclass
from typing import Self

from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.governance.authorization import AuthorizationPolicy
from enterprise_agent.orchestration.routing.capabilities import artifact_capability, external_flags

_RESEARCH_ROLE = "research"
_ANALYSIS_ROLE = "analysis"

_DIRECT_ANSWER = "目标没有工具、实时信息、交付物或外部操作要求，可直接回答。"
_SPLIT_WORK = "目标同时需要外部证据和分析/交付，拆为有依赖的 Research→Analysis 任务。"
_SINGLE_WORKER = "目标可由一个具备所需能力的 Worker 完成。"


@dataclass(frozen=True, slots=True)
class _Demand:
    """Facts derived once from a GoalSpec and reused by every route branch."""

    capabilities: frozenset[str]
    has_artifacts: bool

    @classmethod
    def from_spec(cls, spec: GoalSpec) -> Self:
        capabilities = frozenset(
            capability for goal in spec.goals for capability in goal.required_capabilities
        )
        artifact_capabilities = {
            capability
            for item in spec.deliverables
            if item.delivery_mode == "artifact"
            if (capability := artifact_capability(item.format)) is not None
        }
        return cls(
            capabilities=capabilities | artifact_capabilities,
            has_artifacts=bool(artifact_capabilities),
        )

    @property
    def required(self) -> list[str]:
        return sorted(self.capabilities)

    @property
    def can_answer_directly(self) -> bool:
        return not self.capabilities and not self.has_artifacts


class Router:
    def __init__(self, policy: AuthorizationPolicy | None = None) -> None:
        self._policy = policy

    def decide(self, spec: GoalSpec, requested_role: str | None = None) -> RouteDecision:
        if spec.is_waiting_input:
            raise ValueError("waiting input is not a route")
        demand = _Demand.from_spec(spec)
        if demand.can_answer_directly:
            return _direct(_DIRECT_ANSWER)
        if requested_role:
            return _single(demand, requested_role)
        roles = self._covering_roles(demand.capabilities)
        if roles:
            return _single(demand, _preferred_role(roles))
        return _multi(demand)

    def _covering_roles(self, required: frozenset[str]) -> list[str]:
        if self._policy is None:
            built_in = {
                _RESEARCH_ROLE: frozenset({"web_search", "web_fetch", "workspace_read"}),
                _ANALYSIS_ROLE: frozenset({"analysis", "calculate", "save_report"}),
            }
            return sorted(role for role, capabilities in built_in.items() if required <= capabilities)
        return sorted(
            role
            for role in self._policy.roles()
            if required <= self._policy.get_role_policy(role).capabilities
        )


def _direct(rationale: str) -> RouteDecision:
    return RouteDecision(route=RouteKind.DIRECT, rationale=rationale)


def _multi(demand: _Demand) -> RouteDecision:
    external, realtime = external_flags(set(demand.capabilities))
    return RouteDecision(
        route=RouteKind.MULTI,
        required_capabilities=demand.required,
        rationale=_SPLIT_WORK,
        requires_external_operation=external,
        requires_realtime_information=realtime,
    )


def _single(demand: _Demand, role: str) -> RouteDecision:
    external, realtime = external_flags(set(demand.capabilities))
    return RouteDecision(
        route=RouteKind.SINGLE,
        required_capabilities=demand.required,
        selected_role=role,
        rationale=_SINGLE_WORKER,
        requires_external_operation=external,
        requires_realtime_information=realtime,
    )


def _preferred_role(roles: list[str]) -> str:
    for preferred in (_ANALYSIS_ROLE, _RESEARCH_ROLE):
        if preferred in roles:
            return preferred
    return roles[0]
