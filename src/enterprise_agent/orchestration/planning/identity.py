"""Stable task identity and incremental plan reconciliation."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from enterprise_agent.contracts.plans import GlobalPlan, TaskSpec, TaskStatus
from enterprise_agent.contracts.results import AgentResult
from enterprise_agent.orchestration.supervision.recovery import RecoveryAction, decide_recovery

_RESULT_PREFIX = "result:"
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class TaskReconciliation:
    """The result of matching a revised plan to its previous execution."""

    plan: GlobalPlan
    task_results: dict[str, AgentResult]
    task_mapping: dict[str, str]
    task_actions: dict[str, str]
    retired_task_ids: tuple[str, ...]


def logical_task_key(task: TaskSpec) -> str:
    """Return the persisted logical identity, with a legacy-safe fallback."""
    return task.task_key or task.task_id


def task_matches(new_tasks: Sequence[TaskSpec], old_tasks: Sequence[TaskSpec]) -> dict[str, str]:
    """Match new task ids to old task ids without reusing one old task twice.

    Matching is deliberately conservative: explicit logical keys win, then
    the old execution id, then a semantic signature. An ambiguous semantic
    match is left unmatched so evidence cannot be attached to the wrong task.
    """

    unmatched = {task.task_id for task in old_tasks}
    old_by_id = {task.task_id: task for task in old_tasks}
    matches: dict[str, str] = {}

    for selector in (_logical_selector, _id_selector, _semantic_selector):
        for new_task in new_tasks:
            if new_task.task_id in matches:
                continue
            selected = selector(new_task)
            if selected is None:
                continue
            candidates = [
                old_id
                for old_id in unmatched
                if selected == selector(old_by_id[old_id])
            ]
            if len(candidates) != 1:
                continue
            old_id = candidates[0]
            matches[new_task.task_id] = old_id
            unmatched.remove(old_id)
    return matches


def reconcile_plan(
    plan: GlobalPlan,
    previous: GlobalPlan | None,
    previous_results: Mapping[str, AgentResult],
) -> TaskReconciliation:
    """Reuse completed work and reopen only tasks requiring repair.

    Historical results are moved to the new task id even for a repair. This
    lets a research worker use old evidence while making sure a failed or
    materially changed task is executed again.
    """

    if previous is None:
        return TaskReconciliation(
            plan=plan,
            task_results=dict(previous_results),
            task_mapping={},
            task_actions={task.task_id: "new" for task in plan.tasks},
            retired_task_ids=(),
        )

    old_by_id = {task.task_id: task for task in previous.tasks}
    matches = task_matches(plan.tasks, previous.tasks)
    task_results: dict[str, AgentResult] = {}
    task_actions: dict[str, str] = {}
    task_mapping: dict[str, str] = {}

    # First decide whether each task is intrinsically reusable. Dependency
    # invalidation is applied below after all task ids have been rewritten.
    reusable: dict[str, bool] = {}
    for new_task in plan.tasks:
        old_id = matches.get(new_task.task_id)
        old_task = old_by_id.get(old_id) if old_id else None
        old_result = previous_results.get(old_id) if old_id else None
        reusable[new_task.task_id] = bool(
            old_task
            and old_result
            and _compatible_definition(old_task, new_task)
            and decide_recovery(old_result).action == RecoveryAction.PROCEED
        )

    old_to_new = {old_id: new_id for new_id, old_id in matches.items()}
    current_ids = {task.task_id for task in plan.tasks}
    rewritten_tasks: list[TaskSpec] = []
    for new_task in plan.tasks:
        rewritten_dependencies = [
            dep if dep in current_ids else old_to_new.get(dep, dep)
            for dep in new_task.depends_on
        ]
        rewritten_inputs = [
            _rewrite_result_ref(value, old_to_new, current_ids) for value in new_task.inputs
        ]
        old_id = matches.get(new_task.task_id)
        old_task = old_by_id.get(old_id) if old_id else None
        old_result = previous_results.get(old_id) if old_id else None
        if old_id and old_result is not None:
            task_results[new_task.task_id] = old_result
        if old_task is None:
            action = "new"
        elif reusable[new_task.task_id]:
            action = "reused"
        else:
            action = "repaired"
        task_actions[new_task.task_id] = action
        if old_id:
            task_mapping[old_id] = new_task.task_id

        attempts = old_task.attempts if old_task is not None else new_task.attempts
        status = TaskStatus.SUCCEEDED if action == "reused" else TaskStatus.PENDING
        rewritten_tasks.append(
            new_task.model_copy(
                update={
                    "depends_on": rewritten_dependencies,
                    "inputs": rewritten_inputs,
                    "attempts": attempts,
                    "status": status,
                    "result_ref": new_task.task_id if status == TaskStatus.SUCCEEDED else None,
                }
            )
        )

    # A result depending on a new or repaired task is stale even when the
    # task's own definition did not change. Repeat until dependency actions
    # have propagated through the plan order.
    changed = True
    while changed:
        changed = False
        for task in rewritten_tasks:
            if task_actions[task.task_id] != "reused":
                continue
            if any(task_actions.get(dep) != "reused" for dep in task.depends_on):
                task_actions[task.task_id] = "repaired"
                changed = True

    rewritten_tasks = [
        task.model_copy(
            update={
                "status": TaskStatus.SUCCEEDED
                if task_actions[task.task_id] == "reused"
                else TaskStatus.PENDING,
                "result_ref": task.task_id
                if task_actions[task.task_id] == "reused"
                else None,
            }
        )
        for task in rewritten_tasks
    ]
    retired = tuple(
        task.task_id for task in previous.tasks if task.task_id not in task_mapping
    )
    return TaskReconciliation(
        plan=plan.model_copy(update={"tasks": rewritten_tasks}),
        task_results=task_results,
        task_mapping=task_mapping,
        task_actions=task_actions,
        retired_task_ids=retired,
    )


def _logical_selector(task: TaskSpec) -> str | None:
    return task.task_key


def _id_selector(task: TaskSpec) -> str:
    return task.task_id


def _semantic_selector(task: TaskSpec) -> tuple[tuple[str, ...], str, str]:
    return (
        tuple(sorted(task.goal_ids)),
        task.agent_role,
        _task_phase(task),
    )


def _task_phase(task: TaskSpec) -> str:
    if task.task_key:
        return task.task_key
    # Built-in roles have a stable phase even when replaying legacy plans that
    # did not persist task_key. Custom roles fall back to normalized text.
    if task.agent_role in {"research", "analysis"}:
        return task.agent_role
    return _normalize(task.task_description)


def _compatible_definition(old: TaskSpec, new: TaskSpec) -> bool:
    return (
        tuple(sorted(old.goal_ids)) == tuple(sorted(new.goal_ids))
        and old.agent_role == new.agent_role
        and (
            _normalize(old.task_description) == _normalize(new.task_description)
            or (_legacy_id_description(old) and _legacy_id_description(new))
        )
        and _normalize(old.expected_output) == _normalize(new.expected_output)
        and _criteria(old.acceptance_criteria) == _criteria(new.acceptance_criteria)
    )


def _criteria(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(_normalize(value) for value in values))


def _normalize(value: str) -> str:
    return _WHITESPACE.sub(" ", value.strip()).casefold()


def _legacy_id_description(task: TaskSpec) -> bool:
    return _normalize(task.task_description) == _normalize(task.task_id)


def _rewrite_result_ref(
    value: str,
    old_to_new: Mapping[str, str],
    current_ids: set[str],
) -> str:
    if not value.startswith(_RESULT_PREFIX):
        return value
    task_id = value.removeprefix(_RESULT_PREFIX)
    if task_id in current_ids:
        return value
    return f"{_RESULT_PREFIX}{old_to_new.get(task_id, task_id)}"


__all__ = [
    "TaskReconciliation",
    "logical_task_key",
    "reconcile_plan",
    "task_matches",
]
