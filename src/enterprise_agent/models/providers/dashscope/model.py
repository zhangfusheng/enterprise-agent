from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from enterprise_agent.models.binding import disable_parallel_tool_calls
from enterprise_agent.models.config import ModelProfile, ProviderConfig
from enterprise_agent.models.mapping import chat_openai_arguments, map_dashscope
from enterprise_agent.models.parameters import resolve_parameters
from enterprise_agent.models.parameters.dashscope import DashScopeParameters
from enterprise_agent.models.providers.base import register, require_api_key

_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@register("dashscope")
def build(profile: ModelProfile, provider: ProviderConfig) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    if provider.vendor not in (None, "dashscope"):
        raise ValueError(f"protocol dashscope does not build vendor {provider.vendor!r}")
    params = resolve_parameters(profile, provider.vendor or "dashscope")
    if not isinstance(params, DashScopeParameters):
        raise TypeError("dashscope parameters are required")
    model = ChatOpenAI(
        model=profile.model_name,
        api_key=SecretStr(require_api_key(provider)),
        base_url=provider.endpoint or _DEFAULT_BASE_URL,
        timeout=profile.timeout_seconds,
        **chat_openai_arguments(map_dashscope(params)),
    )
    if params.parallel_tool_calls is False:
        disable_parallel_tool_calls(model)
    return model
