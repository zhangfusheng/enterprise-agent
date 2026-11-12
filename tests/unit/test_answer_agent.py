from langchain_core.messages import AIMessage

from enterprise_agent.agents.answer.definition import PROMPT_VERSION, AnswerAgent
from enterprise_agent.agents.answer.schemas import AnswerRequest
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import ResultStatus


class _Harness:
    def __init__(self, messages):
        self.messages = messages
        self.calls: list[dict] = []

    def invoke(self, role, prompt, tools=None, *, thread_id, **kwargs):
        self.calls.append(
            {
                "role": role,
                "prompt": prompt,
                "tools": tools,
                "thread_id": thread_id,
                "kwargs": kwargs,
            }
        )
        return self.messages


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run-answer",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent_answer",
        parent_agent_id="agent_supervisor",
        workspace_root=".",
        allowed_roles={"answer"},
        allowed_tools=set(),
        metadata={"role": "answer", "model_profile": "offline-default"},
    )


def test_answer_agent_returns_model_answer_without_tools():
    harness = _Harness([AIMessage(content="幂等操作重复执行不会产生额外效果。")])

    result = AnswerAgent(harness).run(
        AnswerRequest(
            question="什么是幂等性？",
            inputs=[{"name": "context", "content": "面向 API 设计"}],
        ),
        _context(),
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.summary == "幂等操作重复执行不会产生额外效果。"
    assert result.structured_data["answer"] == result.summary
    assert result.agent_id == "agent_answer"
    assert len(harness.calls) == 1
    call = harness.calls[0]
    assert call["role"] == "answer"
    assert call["tools"] == []
    assert call["thread_id"] == "run-answer"
    assert "什么是幂等性？" in call["prompt"]
    assert "面向 API 设计" in call["prompt"]
    assert "</user_request>" in call["prompt"]
    assert PROMPT_VERSION == "answer-prompt-v1"


def test_answer_agent_rejects_an_empty_model_response():
    result = AnswerAgent(_Harness([AIMessage(content="")])).run(
        AnswerRequest(question="请解释幂等性"),
        _context(),
    )

    assert result.status == ResultStatus.FAILED
    assert result.summary == ""
    assert result.errors == ["Answer Agent 没有返回可展示的回答"]
