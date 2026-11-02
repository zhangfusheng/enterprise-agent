from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from enterprise_agent.models.config import ModelProfile, ProviderConfig
from enterprise_agent.models.providers.base import register

_FALLBACK_TEXT = "已按离线确定性模型处理请求。"
_FIRST_TOOL_CHOICES = {"required", "any"}


class OfflineChatModel(BaseChatModel):
    """Deterministic chat model used when the process is not calling a provider."""

    model_name: str = "deterministic-v1"
    offline: bool = True

    @property
    def _llm_type(self) -> str:
        return "enterprise-offline"

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        formatted = [_openai_tool(tool) for tool in tools]
        return self.bind(tools=formatted, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        message = _respond(messages, kwargs.get("tools"), kwargs.get("tool_choice"))
        return ChatResult(generations=[ChatGeneration(message=message)])


@register("fake")
def build(profile: ModelProfile, provider: ProviderConfig) -> OfflineChatModel:
    del provider
    return OfflineChatModel(model_name=profile.model_name)


def _respond(
    messages: list[BaseMessage],
    tools: list[dict[str, Any]] | None,
    tool_choice: Any,
) -> AIMessage:
    selected = _selected_tool(tools or [], tool_choice, messages)
    if selected is None:
        text = _last_user_text(messages)
        content = f"离线模型已处理：{text}" if text else _FALLBACK_TEXT
        return AIMessage(content=content)
    function = _function_body(selected)
    payload = _default_value(function.get("parameters") or {"type": "object"})
    if not isinstance(payload, dict):
        payload = {}
    name = str(function.get("name") or "tool")
    if name == "web_search" and not payload.get("query"):
        payload["query"] = _last_user_text(messages)[:500] or "offline fixture"
    elif name == "save_report" and not payload.get("markdown"):
        payload["markdown"] = "## 离线报告\n\n已根据本地 fixture 资料生成。"
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": name,
                "args": payload,
                "id": "offline-call-1",
                "type": "tool_call",
            }
        ],
    )


def _selected_tool(
    tools: list[dict[str, Any]],
    tool_choice: Any,
    messages: list[BaseMessage],
) -> dict[str, Any] | None:
    if not tools:
        return None
    if messages and getattr(messages[-1], "type", None) == "tool":
        return None
    names = [_tool_name(tool) for tool in tools]
    if tool_choice in _FIRST_TOOL_CHOICES or tool_choice is True:
        return tools[0]
    if tool_choice in (None, "auto", "none", False):
        text = _last_user_text(messages)
        if "web_search" in names and any(
            marker in text for marker in ("搜索", "网页", "来源", "最新", "实时")
        ):
            return tools[names.index("web_search")]
        if "save_report" in names and "报告" in text:
            return tools[names.index("save_report")]
        if "calculate" in names and any(marker in text for marker in ("计算", "增长率")):
            return tools[names.index("calculate")]
        return None
    name = _choice_name(tool_choice)
    if name not in names:
        available = ", ".join(item for item in names if item)
        raise ValueError(f"unknown tool {name!r}; available tools: {available}")
    return tools[names.index(name)]


def _choice_name(tool_choice: Any) -> str:
    if isinstance(tool_choice, str):
        return tool_choice
    if isinstance(tool_choice, dict):
        return _tool_name(tool_choice)
    return str(tool_choice)


def _openai_tool(tool: dict[str, Any] | type | Callable[..., Any] | BaseTool) -> dict[str, Any]:
    if isinstance(tool, dict) and isinstance(tool.get("function"), dict):
        return tool
    converted = convert_to_openai_tool(tool)
    if not isinstance(converted, dict):
        raise TypeError("tool conversion did not produce an object")
    return converted


def _function_body(tool: dict[str, Any]) -> dict[str, Any]:
    function = tool.get("function")
    if isinstance(function, dict):
        return function
    return tool


def _tool_name(tool: dict[str, Any]) -> str:
    name = _function_body(tool).get("name")
    return str(name) if isinstance(name, str) else ""


def _last_user_text(messages: list[BaseMessage]) -> str:
    for message in reversed(messages):
        if getattr(message, "type", None) not in {"human", "user"}:
            continue
        text = _message_text(message).strip()
        if text:
            return text[:500]
    return ""


def _message_text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(str(part.get("text") or ""))
    return "".join(parts)


def _default_value(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return None
    if "default" in schema:
        return schema["default"]
    kind = _schema_type(schema)
    if kind == "string":
        return ""
    if kind in {"number", "integer"}:
        return 0
    if kind == "boolean":
        return False
    if kind == "array":
        return []
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        return {name: _default_value(child) for name, child in properties.items()}
    return None


def _schema_type(schema: dict[str, Any]) -> str | None:
    kind = schema.get("type")
    if isinstance(kind, str):
        return kind
    if isinstance(kind, list):
        for item in kind:
            if item != "null":
                return str(item)
    return None
