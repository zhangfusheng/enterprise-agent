from enterprise_agent.contracts.plans import GlobalPlan, TaskSpec, TaskStatus


def ready_tasks(plan: GlobalPlan) -> list[TaskSpec]:
    completed = {task.task_id for task in plan.tasks if task.status == TaskStatus.SUCCEEDED}
    return [task for task in plan.tasks if task.status == TaskStatus.PENDING and set(task.depends_on) <= completed]

