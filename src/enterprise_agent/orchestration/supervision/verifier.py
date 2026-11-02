from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.contracts.results import AgentResult


def verify(spec: GoalSpec, result: AgentResult) -> list[str]:
    missing: list[str] = []
    if not result.summary:
        missing.append("结果缺少摘要")
    if spec.constraints.require_sources and not result.sources:
        missing.append("验收要求来源，但结果没有来源")
    return missing

