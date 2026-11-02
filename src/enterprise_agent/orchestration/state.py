from typing import Any, NotRequired, TypedDict

from enterprise_agent.contracts.runs import RunStatus


class PlanHistoryState(TypedDict):
    version: int
    reason: str
    plan: dict[str, Any]
    task_mapping: NotRequired[dict[str, str]]
    task_actions: NotRequired[dict[str, str]]
    retired_task_ids: NotRequired[list[str]]


class OrchestrationState(TypedDict):
    run_id: str
    tenant_id: str
    user_id: str
    input_text: str
    inputs: NotRequired[list[dict[str, Any]]]
    constraints: NotRequired[dict[str, Any]]
    requested_role: NotRequired[str | None]
    model_profile: NotRequired[str | None]
    reasoning_effort: NotRequired[str | None]
    external_network_allowed: NotRequired[bool]
    goal_spec: NotRequired[dict[str, Any]]
    route: NotRequired[dict[str, Any]]
    plan: NotRequired[dict[str, Any]]
    plan_history: NotRequired[list[PlanHistoryState]]
    task_results: NotRequired[dict[str, dict[str, Any]]]
    result: NotRequired[dict[str, Any]]
    summary: NotRequired[str]
    status: NotRequired[RunStatus]
    missing_information: NotRequired[list[str]]
    budget_used: NotRequired[dict[str, int]]
    replan_count: NotRequired[int]
    cancel_requested: NotRequired[bool]
