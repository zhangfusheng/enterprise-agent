"""Per-vendor form models. A card trims the JSON schema; the request uses the model."""

from pydantic import BaseModel

from enterprise_agent.models.config import ModelProfile

from .anthropic import AnthropicParameters
from .dashscope import DashScopeParameters
from .deepseek import DeepSeekParameters
from .doubao import DoubaoParameters
from .gemini import GeminiParameters
from .moonshot import MoonshotParameters
from .ollama import OllamaParameters
from .openai_chat import OpenAIChatParameters
from .openai_response import OpenAIResponseParameters
from .xai import XAIParameters

PARAMETERS: dict[str, type[BaseModel]] = {
    "dashscope": DashScopeParameters,
    "doubao": DoubaoParameters,
    "anthropic": AnthropicParameters,
    "openai_chat": OpenAIChatParameters,
    "openai_response": OpenAIResponseParameters,
    "gemini": GeminiParameters,
    "deepseek": DeepSeekParameters,
    "moonshot": MoonshotParameters,
    "xai": XAIParameters,
    "ollama": OllamaParameters,
}

__all__ = [
    "PARAMETERS",
    "AnthropicParameters",
    "DashScopeParameters",
    "DeepSeekParameters",
    "DoubaoParameters",
    "GeminiParameters",
    "MoonshotParameters",
    "OllamaParameters",
    "OpenAIChatParameters",
    "OpenAIResponseParameters",
    "XAIParameters",
    "parameters_class",
    "resolve_parameters",
]


def parameters_class(vendor: str) -> type[BaseModel]:
    """Return the form model for ``vendor``."""
    try:
        return PARAMETERS[vendor]
    except KeyError as exc:
        known = ", ".join(sorted(PARAMETERS))
        raise KeyError(f"unknown model vendor {vendor!r}; known vendors: {known}") from exc


def resolve_parameters(profile: ModelProfile, vendor: str) -> BaseModel:
    """Validate ``profile.parameters`` for ``vendor``.

    ``profile.temperature`` fills a missing temperature so older profiles keep working.
    A temperature written in ``parameters`` wins.
    """
    model = parameters_class(vendor)
    raw = dict(profile.parameters)
    if "temperature" in model.model_fields and "temperature" not in raw:
        raw["temperature"] = profile.temperature
    return model.model_validate(raw)
