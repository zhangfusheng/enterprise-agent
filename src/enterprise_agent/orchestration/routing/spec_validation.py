"""Deterministic validation and normalization for parsed goal specifications."""

from typing import Any

from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.governance.authorization import AuthorizationPolicy
from enterprise_agent.orchestration.routing.capabilities import artifact_capability


def validate_goal_spec(spec: GoalSpec, policy: AuthorizationPolicy) -> GoalSpec:
    """Return a normalized spec or reject a structurally inconsistent one.

    This boundary deliberately does not infer user intent.  In particular, it
    neither adds nor removes ``analysis`` based on words in the request.
    """

    if not spec.goals and not spec.missing_information:
        raise ValueError("goal spec requires a goal or an explicit clarification request")

    known = set[Any]().union(*(policy.get_role_policy(role).capabilities for role in policy.roles()))
    goals = []
    for goal in spec.goals:
        required = sorted(set(goal.required_capabilities))
        unknown = set(required) - known
        if unknown:
            raise ValueError(f"unsupported capabilities: {sorted(unknown)}")
        goals.append(goal.model_copy(update={"required_capabilities": required}))

    deliverables = []
    for item in spec.deliverables:
        # ``path`` implied an independent artifact before delivery_mode existed.
        mode = "artifact" if item.path else item.delivery_mode
        if mode == "artifact":
            capability = artifact_capability(item.format)
            if capability is None or capability not in known:
                raise ValueError(f"unsupported artifact format: {item.format}")
        deliverables.append(item.model_copy(update={"delivery_mode": mode}))

    _unique_ids("goal", [item.id for item in goals])
    _unique_ids("deliverable", [item.id for item in deliverables])
    _unique_ids("acceptance criterion", [item.id for item in spec.acceptance_criteria])
    return spec.model_copy(update={"goals": goals, "deliverables": deliverables})


def _unique_ids(kind: str, ids: list[str]) -> None:
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {kind} ids")
