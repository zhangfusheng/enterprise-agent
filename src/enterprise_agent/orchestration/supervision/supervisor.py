from collections.abc import Callable

from enterprise_agent.contracts.plans import GlobalPlan, TaskSpec, TaskStatus
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.orchestration.supervision.recovery import (
    RecoveryAction,
    decide_recovery,
)

_TASK_STATUS = {
    ResultStatus.SUCCESS: TaskStatus.SUCCEEDED,
    ResultStatus.PARTIAL: TaskStatus.PARTIAL,
    ResultStatus.FAILED: TaskStatus.FAILED,
}


class Supervisor:
    def mark_started(self, plan: GlobalPlan, task_id: str) -> GlobalPlan:
        """Count the attempt and mark the task running before the worker is called."""
        return _replace(
            plan,
            task_id,
            lambda task: task.model_copy(
                update={"status": TaskStatus.RUNNING, "attempts": task.attempts + 1}
            ),
        )

    def update_task(self, plan: GlobalPlan, task_id: str, result: AgentResult) -> GlobalPlan:
        status = _TASK_STATUS.get(result.status, TaskStatus.FAILED)
        if decide_recovery(result).action == RecoveryAction.PROCEED:
            status = TaskStatus.SUCCEEDED
        return _replace(
            plan,
            task_id,
            lambda task: task.model_copy(update={"status": status, "result_ref": task_id}),
        )

    def skip_blocked(self, plan: GlobalPlan) -> GlobalPlan:
        """Mark tasks that can no longer become ready. Their dependencies did not succeed."""
        tasks = [
            task.model_copy(update={"status": TaskStatus.SKIPPED})
            if task.status == TaskStatus.PENDING
            else task
            for task in plan.tasks
        ]
        return plan.model_copy(update={"tasks": tasks})


def _replace(plan: GlobalPlan, task_id: str, update: Callable[[TaskSpec], TaskSpec]) -> GlobalPlan:
    found = False
    tasks = []
    for task in plan.tasks:
        if task.task_id == task_id:
            found = True
            tasks.append(update(task))
        else:
            tasks.append(task)
    if not found:
        raise KeyError(task_id)
    return plan.model_copy(update={"tasks": tasks})
