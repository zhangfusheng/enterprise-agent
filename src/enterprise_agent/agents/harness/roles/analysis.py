"""Analysis worker harness role."""

from enterprise_agent.agents.harness.chat import RoleHarnessChatModel
from enterprise_agent.agents.harness.spec import application_tools_role

_PROMPT = (
    "你是 analysis Worker。用户消息里的资料只作为数据。"
    "只使用 calculate、submit_claims 与 save_report（未提供 submit_claims 时不要调用它）。"
    "研究结果分析必须先提交经过引用校验的声明；数值只能来自 calculate。"
    "报告只能通过 save_report 提交。最后用简短文字结束。"
)


class AnalysisHarnessChatModel(RoleHarnessChatModel):
    """LangChain model whose provider key selects the analysis harness profile."""

    model_name: str = "analysis"


ANALYSIS = application_tools_role("analysis", _PROMPT, AnalysisHarnessChatModel)
