from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr

from enterprise_agent.models.config import ModelProfile, ProviderConfig
from enterprise_agent.models.mapping import map_anthropic
from enterprise_agent.models.parameters import resolve_parameters
from enterprise_agent.models.parameters.anthropic import AnthropicParameters
from enterprise_agent.models.providers.base import register, require_api_key


@register("anthropic")
def build(profile: ModelProfile, provider: ProviderConfig) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    if provider.vendor not in (None, "anthropic"):
        raise ValueError(f"protocol anthropic does not build vendor {provider.vendor!r}")
    params = resolve_parameters(profile, provider.vendor or "anthropic")
    if not isinstance(params, AnthropicParameters):
        raise TypeError("anthropic parameters are required")
    return ChatAnthropic(
        model_name=profile.model_name,
        api_key=SecretStr(require_api_key(provider)),
        base_url=provider.endpoint,
        temperature=profile.temperature,
        timeout=profile.timeout_seconds,
        # Alias of stop_sequences. The type checker treats it as a required argument.
        stop=None,
        **map_anthropic(params),
    )
