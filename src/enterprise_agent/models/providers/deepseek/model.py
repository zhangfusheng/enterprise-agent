from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from enterprise_agent.models.binding import force_tool_structured_output
from enterprise_agent.models.config import ModelProfile, ProviderConfig
from enterprise_agent.models.mapping import chat_openai_arguments, map_deepseek
from enterprise_agent.models.parameters import resolve_parameters
from enterprise_agent.models.parameters.deepseek import DeepSeekParameters
from enterprise_agent.models.providers.base import register, require_api_key

_DEFAULT_BASE_URL = "https://api.deepseek.com"


@register("deepseek")
def build(profile: ModelProfile, provider: ProviderConfig) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    if provider.vendor not in (None, "deepseek"):
        raise ValueError(f"protocol deepseek does not build vendor {provider.vendor!r}")
    params = resolve_parameters(profile, provider.vendor or "deepseek")
    if not isinstance(params, DeepSeekParameters):
        raise TypeError("deepseek parameters are required")
    model = ChatOpenAI(
        model=profile.model_name,
        api_key=SecretStr(require_api_key(provider)),
        base_url=provider.endpoint or _DEFAULT_BASE_URL,
        timeout=profile.timeout_seconds,
        disabled_params={"parallel_tool_calls": None},
        **chat_openai_arguments(map_deepseek(params)),
    )
    force_tool_structured_output(model)
    return model
