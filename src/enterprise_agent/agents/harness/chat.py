"""Shared LangChain model that selects a worker harness profile."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Self

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.base import LangSmithParams
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from pydantic import Field, model_validator

ENTERPRISE_PROVIDER = "enterprise"


class RoleHarnessChatModel(BaseChatModel):
    """Chat model whose provider key selects one Deep Agents harness profile.

    ``model_name`` must be the worker role name. Deep Agents resolves the
    profile as ``enterprise:<model_name>``. Generation and tool binding are
    delegated to the configured chat model.
    """

    chat_model: Any = Field(exclude=True)
    model_name: str = ""

    @model_validator(mode="after")
    def _require_model_name(self) -> Self:
        if not self.model_name.strip():
            raise ValueError("model_name must be the worker role name")
        return self

    @property
    def _llm_type(self) -> str:
        return f"enterprise-{self.model_name}"

    def _get_ls_params(self, stop: list[str] | None = None, **kwargs: Any) -> LangSmithParams:
        del stop, kwargs
        return {"ls_provider": ENTERPRISE_PROVIDER, "ls_model_name": self.model_name}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        bind_tools = getattr(self.chat_model, "bind_tools", None)
        if not callable(bind_tools):
            raise TypeError(f"{self.model_name} chat model cannot bind tools")
        return bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        generate = getattr(self.chat_model, "_generate", None)
        if not callable(generate):
            raise TypeError(f"{self.model_name} chat model cannot generate messages")
        result = generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        if not isinstance(result, ChatResult):
            raise TypeError(f"{self.model_name} chat model must return a ChatResult")
        return result
