from langchain_core.language_models import BaseChatModel

from .cards import ModelCard
from .catalog import ModelCatalog
from .config import ModelConfig, ModelProfile, ProviderConfig
from .parameters import parameters_class
from .providers.base import build_chat_model

_EFFORT_ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")
_THINKING_BUDGETS = {
    "minimal": 1024,
    "low": 2048,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16384,
    "max": 32768,
}
_PROTOCOL_VENDORS = {
    "openai_compatible": "openai_chat",
    "anthropic": "anthropic",
    "dashscope": "dashscope",
    "doubao": "doubao",
    "deepseek": "deepseek",
}


class ModelRegistry:
    def __init__(self, config: ModelConfig, catalog: ModelCatalog | None = None):
        self._profiles = {item.name: item for item in config.profiles}
        self._providers = {item.name: item for item in config.providers}
        self._bindings = {item.role: item.model_profile for item in config.bindings}
        self._catalog = catalog

    def profile(self, name: str) -> ModelProfile:
        """Return a configured profile by its stable, user-selectable name."""
        try:
            return self._profiles[name]
        except KeyError as exc:
            raise KeyError(f"unknown model profile: {name}") from exc

    def profile_names(self) -> tuple[str, ...]:
        return tuple(self._profiles)

    def default_profile_for_role(self, role: str) -> str:
        try:
            return self._bindings[role]
        except KeyError as exc:
            raise KeyError(f"no model binding for role {role}") from exc

    def offline_profile_name(self) -> str | None:
        for name, profile in self._profiles.items():
            provider = self._providers.get(profile.provider)
            if provider is not None and provider.protocol == "fake":
                return name
        return None

    def list_options(self, *, offline_mode: bool = False) -> list[dict[str, object]]:
        """Return non-secret model metadata suitable for a model picker."""
        default = self.offline_profile_name() if offline_mode else self._bindings.get("router")
        options: list[dict[str, object]] = []
        for name, profile in self._profiles.items():
            provider = self._providers[profile.provider]
            card = self._card_for(profile, provider)
            available = not offline_mode or provider.protocol == "fake"
            options.append(
                {
                    "profile": name,
                    "label": (
                        card.label
                        if card is not None
                        else "离线确定性模型"
                        if provider.protocol == "fake"
                        else profile.model_name
                    ),
                    "provider": provider.name,
                    "model_name": profile.model_name,
                    "status": card.status if card is not None else "active",
                    "capabilities": sorted(profile.capabilities),
                    "reasoning_efforts": list(self.supported_reasoning_efforts(name)),
                    "available": available,
                    "default": name == default,
                    "unavailable_reason": (
                        "当前为离线模式，仅可使用离线模型。" if not available else None
                    ),
                }
            )
        return options

    def supported_reasoning_efforts(self, model_profile: str) -> tuple[str, ...]:
        """Return the effort levels that can be applied to a configured profile."""
        profile = self.profile(model_profile)
        provider = self._providers[profile.provider]
        if provider.protocol == "fake":
            return ()
        fields = self._parameter_fields(profile, provider)
        if "reasoning_effort" in fields:
            schema = fields["reasoning_effort"]
            values = schema.get("enum", [])
            if not values:
                values = [
                    item
                    for variant in schema.get("anyOf", [])
                    for item in variant.get("enum", [])
                ]
            return tuple(item for item in _EFFORT_ORDER if item in values)
        if "thinking_budget" in fields or "thinking_enable" in fields:
            return tuple(item for item in ("low", "medium", "high"))
        return ()

    def profile_with_reasoning_effort(
        self, model_profile: str, reasoning_effort: str
    ) -> ModelProfile:
        """Return a validated profile with a per-run thinking setting applied."""
        profile = self.profile(model_profile)
        provider = self._providers[profile.provider]
        allowed = self.supported_reasoning_efforts(model_profile)
        if reasoning_effort not in allowed:
            supported = ", ".join(allowed) or "none"
            raise ValueError(
                f"reasoning effort {reasoning_effort!r} is not supported by "
                f"model profile {model_profile!r}; supported: {supported}"
            )
        parameters = dict(profile.parameters)
        fields = parameters_class(self._vendor_for(provider)).model_fields
        if "reasoning_effort" in fields:
            parameters["reasoning_effort"] = reasoning_effort
        if "thinking_enable" in fields:
            parameters["thinking_enable"] = True
        if "thinking_mode" in fields:
            parameters["thinking_mode"] = "enabled"
        if "thinking_budget" in fields:
            parameters["thinking_budget"] = _THINKING_BUDGETS[reasoning_effort]
        return profile.model_copy(update={"parameters": parameters})

    def profile_for_role(self, role: str, model_profile: str | None = None) -> ModelProfile:
        try:
            profile_name = model_profile or self._bindings[role]
        except KeyError as exc:
            raise KeyError(f"no model binding for role {role}") from exc
        try:
            return self.profile(profile_name)
        except KeyError as exc:
            if model_profile:
                raise
            raise KeyError(f"role {role} references unknown model profile {profile_name}") from exc

    def chat_model_for_role(
        self,
        role: str,
        model_profile: str | None = None,
        *,
        reasoning_effort: str | None = None,
    ) -> BaseChatModel:
        """Build the LangChain chat model bound to ``role``."""
        profile = self.profile_for_role(role, model_profile)
        try:
            provider = self._providers[profile.provider]
        except KeyError as exc:
            raise KeyError(f"unknown model provider: {profile.provider}") from exc
        if reasoning_effort is not None:
            profile = self.profile_with_reasoning_effort(profile.name, reasoning_effort)
        return build_chat_model(profile, provider, self._card_for(profile, provider))

    def _card_for(self, profile: ModelProfile, provider: ProviderConfig) -> ModelCard | None:
        if self._catalog is None or provider.protocol == "fake" or not provider.vendor:
            return None
        return self._catalog.get(provider.vendor, profile.model_name)

    def _parameter_fields(
        self, profile: ModelProfile, provider: ProviderConfig
    ) -> dict[str, object]:
        if self._catalog is not None and provider.vendor:
            try:
                card = self._catalog.get(provider.vendor, profile.model_name)
            except KeyError:
                card = None
            if card is not None:
                return card.parameter_schema.get("properties", {})
        return parameters_class(self._vendor_for(provider)).model_json_schema().get("properties", {})

    @staticmethod
    def _vendor_for(provider: ProviderConfig) -> str:
        vendor = provider.vendor or _PROTOCOL_VENDORS.get(provider.protocol)
        if not vendor:
            raise KeyError(f"unknown model vendor for protocol {provider.protocol!r}")
        return vendor
