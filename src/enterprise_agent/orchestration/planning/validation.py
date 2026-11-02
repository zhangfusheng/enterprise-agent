from graphlib import CycleError, TopologicalSorter

from enterprise_agent.contracts.plans import GlobalPlan, TaskStatus
from enterprise_agent.governance.authorization import AuthorizationPolicy
from enterprise_agent.governance.budgets import BudgetLimits

_RESULT_PREFIX = "result:"


class PlanValidationError(ValueError):
    pass


def validate_plan(plan: GlobalPlan, goal_ids: set[str], policy: AuthorizationPolicy, max_tasks: int) -> None:
    tasks = plan.tasks
    if len(tasks) > max_tasks:
        raise PlanValidationError("plan exceeds max task count")
    task_ids = {task.task_id for task in tasks}
    if len(task_ids) != len(tasks):
        raise PlanValidationError("task ids must be unique")
    task_keys = {task.task_key or task.task_id for task in tasks}
    if len(task_keys) != len(tasks):
        raise PlanValidationError("task keys must be unique")

    dependencies: dict[str, set[str]] = {}
    linked_goal = False
    for task in tasks:
        if any(goal_id not in goal_ids for goal_id in task.goal_ids):
            raise PlanValidationError(f"task {task.task_id} references unknown goal")
        if any(dep not in task_ids for dep in task.depends_on):
            raise PlanValidationError(f"task {task.task_id} references unknown dependency")
        if any(_unknown_result(ref, task_ids) for ref in task.inputs):
            raise PlanValidationError(f"task {task.task_id} references unknown result")
        policy.require_capabilities(task.agent_role, set())
        dependencies[task.task_id] = set(task.depends_on)
        linked_goal = linked_goal or bool(task.goal_ids)

    try:
        TopologicalSorter(dependencies).prepare()
    except CycleError as exc:
        raise PlanValidationError("plan dependencies contain a cycle") from exc
    if not linked_goal:
        raise PlanValidationError("plan must associate goals with tasks")


def _unknown_result(ref: str, task_ids: set[str]) -> bool:
    task_id = ref.removeprefix(_RESULT_PREFIX)
    return task_id != ref and task_id not in task_ids


def assert_remaining_budget(plan: GlobalPlan, limits: BudgetLimits, used: dict[str, int]) -> None:
    """Reject a plan whose pending tasks cannot fit the remaining agent and step budget."""
    pending = sum(1 for task in plan.tasks if task.status != TaskStatus.SUCCEEDED)
    if used.get("agents", 0) + pending > limits.agents:
        raise PlanValidationError("plan exceeds remaining agent budget")
    if used.get("steps", 0) + pending > limits.steps:
        raise PlanValidationError("plan exceeds remaining step budget")
