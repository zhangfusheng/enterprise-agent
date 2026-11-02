from enterprise_agent.contracts.plans import GlobalPlan
from enterprise_agent.orchestration.execution.readiness import ready_tasks


class Executor:
    def next(self, plan: GlobalPlan):
        return ready_tasks(plan)

