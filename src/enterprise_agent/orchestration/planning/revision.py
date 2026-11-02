from enterprise_agent.contracts.plans import GlobalPlan, PlanHistoryEntry


def bounded_revision(history: list[PlanHistoryEntry], current: GlobalPlan, reason: str, max_replans: int) -> tuple[GlobalPlan, list[PlanHistoryEntry]]:
    if len(history) >= max_replans:
        return current, history
    next_plan = current.model_copy(update={"plan_version": current.plan_version + 1})
    return next_plan, [*history, PlanHistoryEntry(version=current.plan_version, reason=reason, plan=current)]

