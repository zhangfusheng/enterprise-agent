"""Thin adapter around the installed Deep Agents harness."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any, cast

from deepagents import CompiledSubAgent, create_deep_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

from enterprise_agent.agents.harness.backends import build_state_backend
from enterprise_agent.agents.harness.middleware import worker_middleware
from enterprise_agent.agents.harness.registry import HarnessRoleRegistry
from enterprise_agent.agents.harness.spec import HarnessRole
from enterprise_agent.infrastructure.observability.langchain_trace import LangChainTraceHandler
from enterprise_agent.infrastructure.observability.metrics import (
    CallOutcome,
    elapsed_ms,
    quote_cost,
    record_model_call,
    usage_from_messages,
)
from enterprise_agent.infrastructure.observability.tracing import (
    TraceSink,
    trace,
    tracing_scope,
)
from enterprise_agent.models.registry import ModelRegistry

WORKER_PROMPT_VERSION = "worker-prompt-v1"
_RECURSION_LIMIT = 50


class DeepAgentsHarness:
    """Assemble a Deep Agents tool loop for one registered worker role.

    Deep Agents supplies the local tool loop, workspace backend, and task
    delegation surface. Enterprise governance remains outside it, so every
    application tool is still called through ToolGateway and delegation is
    checked by DelegationGuard.

    Adding a role does not require a branch here. The composition root registers
    a ``HarnessRole`` and binds that role to a model profile. Roles limited to
    application tools hide the built-in filesystem, shell, task, and todo tools
    and can use only the tools passed into ``invoke``.
    """

    def __init__(
        self,
        models: ModelRegistry,
        roles: HarnessRoleRegistry,
        traces: TraceSink | None = None,
    ) -> None:
        if not isinstance(models, ModelRegistry):
            raise TypeError("harness requires a ModelRegistry")
        if not isinstance(roles, HarnessRoleRegistry):
            raise TypeError("harness requires a HarnessRoleRegistry")
        self.models = models
        self.roles = roles
        self._traces = traces

    def build(
        self,
        role: str,
        tools: list[Any] | None = None,
        subagents: list[dict[str, Any] | CompiledSubAgent] | None = None,
        *,
        model_profile: str | None = None,
        reasoning_effort: str | None = None,
    ) -> Any:
        spec = self.roles.get(role)
        chat_model = self._chat_model(spec.name, model_profile, reasoning_effort)
        graph_tools = list(tools or [])
        return create_deep_agent(
            model=self._wrap_model(spec, chat_model),
            tools=graph_tools,
            system_prompt=spec.system_prompt,
            middleware=[] if spec.application_tools_only else worker_middleware(),
            subagents=cast(Any, [] if spec.application_tools_only else list(subagents or [])),
            backend=build_state_backend(),
        )

    def invoke(
        self,
        role: str,
        prompt: str,
        tools: list[Any] | None = None,
        *,
        thread_id: str,
        model_profile: str | None = None,
        reasoning_effort: str | None = None,
    ) -> list[BaseMessage]:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("worker prompt must be a non-empty string")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("thread_id must be a non-empty string")
        started = time.perf_counter()
        with tracing_scope(self._traces, run_id=thread_id.strip()):
            with trace(role, "chain", inputs={"prompt": prompt}, tags=["worker", role]) as span:
                try:
                    graph = self.build(
                        role,
                        tools=tools,
                        model_profile=model_profile,
                        reasoning_effort=reasoning_effort,
                    )
                    result = graph.invoke(
                        {"messages": [{"role": "user", "content": prompt}]},
                        {
                            "configurable": {"thread_id": thread_id.strip()},
                            "recursion_limit": _RECURSION_LIMIT,
                            "callbacks": [LangChainTraceHandler(span)],
                        },
                    )
                    messages = _messages(result)
                except Exception:
                    self._record_worker(role, started, (), "error", model_profile=model_profile)
                    raise
                incoming, outgoing = usage_from_messages(messages)
                span.outputs = {
                    "message_count": len(messages),
                    "input_tokens": incoming,
                    "output_tokens": outgoing,
                }
                self._record_worker(role, started, messages, "success", model_profile=model_profile)
                return messages

    def _chat_model(
        self,
        role: str,
        model_profile: str | None = None,
        reasoning_effort: str | None = None,
    ) -> BaseChatModel:
        if reasoning_effort is None:
            model = (
                self.models.chat_model_for_role(role)
                if model_profile is None
                else self.models.chat_model_for_role(role, model_profile)
            )
        else:
            model = self.models.chat_model_for_role(
                role, model_profile, reasoning_effort=reasoning_effort
            )
        if not isinstance(model, BaseChatModel):
            raise TypeError(f"role {role} requires a chat model")
        return model

    def _wrap_model(self, spec: HarnessRole, chat_model: BaseChatModel) -> BaseChatModel:
        return spec.chat_model(chat_model=chat_model)

    def _record_worker(
        self,
        role: str,
        started: float,
        messages: Sequence[object],
        outcome: CallOutcome,
        *,
        model_profile: str | None = None,
    ) -> None:
        profile = self.models.profile_for_role(role, model_profile)
        incoming, outgoing = usage_from_messages(messages)
        record_model_call(
            stage="worker",
            role=role,
            model_version=profile.model_name,
            prompt_version=WORKER_PROMPT_VERSION,
            input_tokens=incoming,
            output_tokens=outgoing,
            latency_ms=elapsed_ms(started),
            cost_usd=quote_cost(
                profile.parameters,
                incoming,
                outgoing,
                offline=profile.provider == "offline",
            ),
            outcome=outcome,
        )


def _messages(result: object) -> list[BaseMessage]:
    if not isinstance(result, Mapping) or "messages" not in result:
        raise RuntimeError("deep agent returned no messages")
    messages = result["messages"]
    if isinstance(messages, str | bytes) or not isinstance(messages, Sequence):
        raise RuntimeError("deep agent messages are not a sequence")
    delivered = list(messages)
    if any(not isinstance(message, BaseMessage) for message in delivered):
        raise RuntimeError("deep agent returned a message that is not a BaseMessage")
    return delivered
