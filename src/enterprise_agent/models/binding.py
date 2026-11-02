"""Attach card metadata and call-time tool flags onto a built chat model."""

from collections.abc import Callable
from typing import Any

from langchain_core.language_models import BaseChatModel


def assign_input_types(model: BaseChatModel, input_types: list[str]) -> None:
    """Record the card's input types without copying its context window."""
    object.__setattr__(model, "input_types", list(input_types))


def force_tool_structured_output(model: BaseChatModel) -> None:
    """Ask for a schema through a tool call.

    DeepSeek chat completions reject ``response_format`` type ``json_schema``.
    A function call carries the same schema without that field.
    """
    original = getattr(model, "with_structured_output", None)
    if not callable(original):
        return

    def with_structured_output(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("method", "json_schema") == "json_schema":
            kwargs["method"] = "function_calling"
        return original(*args, **kwargs)

    object.__setattr__(model, "with_structured_output", with_structured_output)


def disable_parallel_tool_calls(model: BaseChatModel) -> None:
    """Send ``parallel_tool_calls=false`` only when this call includes tools."""
    original: Callable[..., Any] = model.bind_tools

    def bind_tools(tools: Any, *args: Any, **kwargs: Any) -> Any:
        if tools:
            kwargs.setdefault("parallel_tool_calls", False)
        return original(tools, *args, **kwargs)

    object.__setattr__(model, "bind_tools", bind_tools)
