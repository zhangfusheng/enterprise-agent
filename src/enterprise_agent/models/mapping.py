"""Map validated form parameters onto vendor request fields.

These dicts are the provider payload. They are not model cards.
"""

from typing import Any

from enterprise_agent.models.parameters.anthropic import AnthropicParameters
from enterprise_agent.models.parameters.dashscope import DashScopeParameters
from enterprise_agent.models.parameters.deepseek import DeepSeekParameters
from enterprise_agent.models.parameters.doubao import DoubaoParameters
from enterprise_agent.models.parameters.gemini import GeminiParameters
from enterprise_agent.models.parameters.moonshot import MoonshotParameters
from enterprise_agent.models.parameters.ollama import OllamaParameters
from enterprise_agent.models.parameters.openai_chat import OpenAIChatParameters
from enterprise_agent.models.parameters.openai_response import OpenAIResponseParameters
from enterprise_agent.models.parameters.xai import XAIParameters

_OPENAI_MODEL_KWARGS = ("audio", "modalities")


def map_dashscope(params: DashScopeParameters, *, tools: bool = False) -> dict[str, Any]:
    payload = _sampling(params, "max_tokens")
    extra: dict[str, Any] = {"enable_thinking": params.thinking_enable}
    if params.thinking_budget is not None:
        extra["thinking_budget"] = params.thinking_budget
    if params.top_k is not None:
        extra["top_k"] = params.top_k
    payload["extra_body"] = extra
    _assign_voice(payload, params.voice)
    _assign_parallel(payload, params.parallel_tool_calls, tools)
    return payload


def map_doubao(params: DoubaoParameters, *, tools: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if params.temperature is not None:
        payload["temperature"] = params.temperature
    if params.top_p is not None:
        payload["top_p"] = params.top_p
    if params.reasoning_effort is not None:
        payload["reasoning_effort"] = params.reasoning_effort
    # ChatOpenAI renames constructor max_tokens to max_completion_tokens. Ark treats
    # that field as a 64k cap on thinking plus the answer, and rejects it beside
    # max_tokens. The answer limit stays in extra_body so the request keeps max_tokens.
    extra: dict[str, Any] = {"thinking": {"type": _doubao_thinking(params)}}
    if params.max_tokens is not None:
        extra["max_tokens"] = params.max_tokens
    payload["extra_body"] = extra
    _assign_parallel(payload, params.parallel_tool_calls, tools)
    return payload


def map_openai_chat(params: OpenAIChatParameters, *, tools: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if params.max_tokens is not None:
        payload["max_completion_tokens"] = params.max_tokens
    if params.temperature is not None:
        payload["temperature"] = params.temperature
    if params.top_p is not None:
        payload["top_p"] = params.top_p
    if params.thinking_enable is not None and params.reasoning_effort is not None:
        payload["reasoning_effort"] = params.reasoning_effort
    _assign_voice(payload, params.voice)
    _assign_parallel(payload, params.parallel_tool_calls, tools)
    return payload


def map_openai_responses(params: OpenAIResponseParameters) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if params.max_tokens is not None:
        payload["max_output_tokens"] = params.max_tokens
    if params.temperature is not None:
        payload["temperature"] = params.temperature
    if params.reasoning_effort is not None:
        payload["reasoning"] = {"effort": params.reasoning_effort}
    return payload


def map_anthropic(params: AnthropicParameters) -> dict[str, Any]:
    max_tokens = params.max_tokens if params.max_tokens is not None else 8192
    mode = params.thinking_mode
    if mode is None:
        mode = "enabled" if params.thinking_enable else "disabled"
    thinking: dict[str, Any]
    if mode == "enabled":
        budget = params.thinking_budget if params.thinking_budget is not None else max_tokens // 2
        if budget >= max_tokens:
            max_tokens = budget + 1024
        thinking = {"type": "enabled", "budget_tokens": budget}
    else:
        thinking = {"type": mode}
    if mode != "disabled" and params.thinking_display is not None:
        thinking["display"] = params.thinking_display
    payload: dict[str, Any] = {"max_tokens": max_tokens, "thinking": thinking}
    if params.reasoning_effort is not None:
        payload["output_config"] = {"effort": params.reasoning_effort}
    return payload


def map_gemini(params: GeminiParameters) -> dict[str, Any]:
    payload = _sampling(params, "max_output_tokens")
    if params.thinking_enable:
        budget = params.thinking_budget if params.thinking_budget is not None else 1024
        payload["thinking_config"] = {"include_thoughts": True, "thinking_budget": budget}
    else:
        payload["thinking_config"] = {"include_thoughts": False, "thinking_budget": 0}
    return payload


def map_deepseek(params: DeepSeekParameters) -> dict[str, Any]:
    payload = _sampling(params, "max_tokens")
    if params.reasoning_effort is not None:
        payload["reasoning_effort"] = params.reasoning_effort
    state = "enabled" if params.thinking_enable else "disabled"
    payload["extra_body"] = {"thinking": {"type": state}}
    return payload


def map_moonshot(params: MoonshotParameters, *, model_name: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if params.temperature is not None:
        payload["temperature"] = params.temperature
    if params.top_p is not None:
        payload["top_p"] = params.top_p
    if model_name == "kimi-k3":
        if params.max_tokens is not None:
            payload["max_completion_tokens"] = params.max_tokens
        if params.reasoning_effort is not None:
            payload["reasoning_effort"] = params.reasoning_effort
        return payload
    if params.max_tokens is not None:
        payload["max_tokens"] = params.max_tokens
    state = "enabled" if params.thinking_enable else "disabled"
    payload["extra_body"] = {"thinking": {"type": state}}
    return payload


def map_xai(params: XAIParameters) -> dict[str, Any]:
    payload = _sampling(params, "max_tokens")
    if params.thinking_enable is not None and params.reasoning_effort is not None:
        payload["reasoning_effort"] = params.reasoning_effort
    return payload


def map_ollama(params: OllamaParameters) -> dict[str, Any]:
    options: dict[str, Any] = {}
    if params.max_tokens is not None:
        options["num_predict"] = params.max_tokens
    if params.temperature is not None:
        options["temperature"] = params.temperature
    payload: dict[str, Any] = {"think": params.thinking_enable}
    if options:
        payload["options"] = options
    return payload


def chat_openai_arguments(mapped: dict[str, Any]) -> dict[str, Any]:
    """Move audio fields into ``model_kwargs`` for ``ChatOpenAI``."""
    payload = dict(mapped)
    payload.pop("parallel_tool_calls", None)
    model_kwargs = {key: payload.pop(key) for key in _OPENAI_MODEL_KWARGS if key in payload}
    if model_kwargs:
        payload["model_kwargs"] = model_kwargs
    return payload


def _doubao_thinking(params: DoubaoParameters) -> str:
    if params.thinking_mode is not None:
        return params.thinking_mode
    return "enabled" if params.thinking_enable else "disabled"


def _sampling(params: Any, max_tokens_key: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if params.max_tokens is not None:
        payload[max_tokens_key] = params.max_tokens
    if params.temperature is not None:
        payload["temperature"] = params.temperature
    if getattr(params, "top_p", None) is not None:
        payload["top_p"] = params.top_p
    return payload


def _assign_voice(payload: dict[str, Any], voice: str | None) -> None:
    if not voice:
        return
    payload["audio"] = {"voice": voice, "format": "pcm16"}
    payload["modalities"] = ["text", "audio"]


def _assign_parallel(payload: dict[str, Any], enabled: bool, tools: bool) -> None:
    if enabled is False and tools:
        payload["parallel_tool_calls"] = False
