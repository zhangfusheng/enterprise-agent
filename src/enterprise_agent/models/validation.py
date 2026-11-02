from pydantic import ValidationError

from .catalog import ModelCatalog
from .config import ModelConfig
from .parameters import parameters_class, resolve_parameters


class ModelConfigError(ValueError):
    pass


def validate_model_config(config: ModelConfig) -> ModelConfig:
    providers = {item.name: item for item in config.providers}
    profiles = {item.name: item for item in config.profiles}
    if len(providers) != len(config.providers) or len(profiles) != len(config.profiles):
        raise ModelConfigError("provider and profile names must be unique")
    for profile in config.profiles:
        if profile.provider not in providers:
            raise ModelConfigError(f"profile {profile.name} references unknown provider")
        provider = providers[profile.provider]
        if provider.protocol == "fake" and not profile.model_name:
            raise ModelConfigError("fake model_name must not be empty")
        if profile.structured_output and "structured_output" not in profile.capabilities:
            raise ModelConfigError(f"profile {profile.name} lacks structured_output capability")
        if profile.tool_calling and "tool_calling" not in profile.capabilities:
            raise ModelConfigError(f"profile {profile.name} lacks tool_calling capability")
    for binding in config.bindings:
        if binding.model_profile not in profiles:
            raise ModelConfigError(f"binding {binding.role} references unknown model profile")
    return config


def validate_profile_cards(config: ModelConfig, catalog: ModelCatalog) -> None:
    """Require each real profile to name a card and carry valid parameters."""
    providers = {item.name: item for item in config.providers}
    for profile in config.profiles:
        provider = providers[profile.provider]
        if provider.protocol == "fake":
            continue
        if not provider.vendor:
            raise ModelConfigError(f"provider {provider.name} requires a vendor")
        try:
            parameters_class(provider.vendor)
        except KeyError as exc:
            message = exc.args[0] if exc.args else "unknown model vendor"
            raise ModelConfigError(str(message)) from exc
        names = {card.name for card in catalog.list_models(provider.vendor)}
        if profile.model_name not in names:
            raise ModelConfigError(
                f"profile {profile.name} model {profile.model_name!r} "
                f"is not a {provider.vendor} card"
            )
        try:
            resolve_parameters(profile, provider.vendor)
        except ValidationError as exc:
            raise ModelConfigError(
                f"profile {profile.name} parameters are invalid: {exc}"
            ) from exc
