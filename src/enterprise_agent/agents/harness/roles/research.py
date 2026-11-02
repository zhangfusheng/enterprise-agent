"""Research worker harness role."""

from enterprise_agent.agents.harness.chat import RoleHarnessChatModel
from enterprise_agent.agents.harness.spec import application_tools_role

_PROMPT = (
    "你是 research Worker。根据任务目标自行决定搜索词和要阅读的网页，"
    "只使用 web_search 与 web_fetch。最后用简短文字结束，证据以工具结果为准。"
)


class ResearchHarnessChatModel(RoleHarnessChatModel):
    """LangChain model whose provider key selects the research harness profile."""

    model_name: str = "research"


RESEARCH = application_tools_role("research", _PROMPT, ResearchHarnessChatModel)
