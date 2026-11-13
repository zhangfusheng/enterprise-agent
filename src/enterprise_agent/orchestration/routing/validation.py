from typing import Any

from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.governance.authorization import AuthorizationPolicy
from enterprise_agent.orchestration.routing.capabilities import external_flags

_SHARED = frozenset({"workspace_read"})
_ANALYSIS_ROLE = "analysis"


def validate_route(decision: RouteDecision, policy: AuthorizationPolicy) -> None:
    """Reject routes that violate role, capability, or flag invariants.

    ``allow_external_network`` is a permission and never appears here as demand.
    A capability no registered role owns fails before a worker is selected.
    """
    required = set(decision.required_capabilities)
    known = set[Any]().union(*(policy.get_role_policy(role).capabilities for role in policy.roles()))
    unknown = required - known
    if unknown:
        raise ValueError(f"unsupported capabilities: {sorted(unknown)}")
    external, realtime = external_flags(required)
    if (
        decision.requires_external_operation != external
        or decision.requires_realtime_information != realtime
    ):
        raise ValueError("external flags must match search capabilities")
    if decision.route == RouteKind.DIRECT:
        _validate_direct(decision)
        return
    if decision.route == RouteKind.SINGLE:
        _validate_single(decision, policy, required)
        return
    _validate_multi(decision, policy, required)


def _required_for(role: str, required: set[str]) -> set[str]:
    """Analysis does not own workspace reads. That capability stays on research."""
    if role == _ANALYSIS_ROLE:
        return required - _SHARED
    return required


def _validate_direct(decision: RouteDecision) -> None:
    if decision.selected_role or decision.required_capabilities:
        raise ValueError("direct route cannot select a role or capability")
    if decision.requires_external_operation or decision.requires_realtime_information:
        raise ValueError("direct route cannot require an external operation")


def _validate_single(decision: RouteDecision, policy: AuthorizationPolicy, required: set[str]) -> None:
    if not decision.selected_role:
        raise ValueError("single route requires selected_role")
    policy.require_capabilities(decision.selected_role, _required_for(decision.selected_role, required))


def _validate_multi(decision: RouteDecision, policy: AuthorizationPolicy, required: set[str]) -> None:
    if decision.selected_role:
        raise ValueError("multi route cannot select a single role")
    if not required:
        raise ValueError("multi route requires capabilities")
    if any(required <= policy.get_role_policy(role).capabilities for role in policy.roles()):
        raise ValueError("multi route must not be used when one role covers all capabilities")
    covered = set[Any]().union(
        *(policy.get_role_policy(role).capabilities & required for role in policy.roles())
    )
    if covered != required:
        raise ValueError("multi route capabilities cannot be covered by registered roles")
