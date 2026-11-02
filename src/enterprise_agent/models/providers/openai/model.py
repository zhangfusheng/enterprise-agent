from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from enterprise_agent.models.binding import disable_parallel_tool_calls
from enterprise_agent.models.config import ModelProfile, ProviderConfig
from enterprise_agent.models.mapping import chat_openai_arguments, map_openai_chat
from enterprise_agent.models.parameters import resolve_parameters
from enterprise_agent.models.parameters.openai_chat import OpenAIChatParameters
from enterprise_agent.models.providers.base import register, require_api_key


@register("openai_compatible")
def build(profile: ModelProfile, provider: ProviderConfig) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    if provider.vendor not in (None, "openai_chat"):
        raise ValueError(
            f"protocol openai_compatible does not build vendor {provider.vendor!r}"
        )
    params = resolve_parameters(profile, provider.vendor or "openai_chat")
    if not isinstance(params, OpenAIChatParameters):
        raise TypeError("openai chat parameters are required")
    model = ChatOpenAI(
        model=profile.model_name,
        api_key=SecretStr(require_api_key(provider)),
        base_url=provider.endpoint,
        timeout=profile.timeout_seconds,
        **chat_openai_arguments(map_openai_chat(params)),
    )
    if params.parallel_tool_calls is False:
        disable_parallel_tool_calls(model)
    return model
