"""Answer Agent harness role."""

from enterprise_agent.agents.harness.chat import RoleHarnessChatModel
from enterprise_agent.agents.harness.spec import application_tools_role

_PROMPT = (
    "你是 answer Agent。回答不需要工具、实时信息或外部操作的用户问题。"
    "不要调用工具、不要声称完成不存在的副作用，最后只返回面向用户的最终答案。"
)


class AnswerHarnessChatModel(RoleHarnessChatModel):
    """LangChain model whose provider key selects the answer harness profile."""

    model_name: str = "answer"


ANSWER = application_tools_role("answer", _PROMPT, AnswerHarnessChatModel)
