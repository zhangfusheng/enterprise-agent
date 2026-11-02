"""Protocol registry for chat-model providers.

Each provider package calls ``register`` when it is imported. ``build_chat_model``
looks the protocol name up in that table. Adding a vendor does not require a new
branch in this module.
"""

import os
from collections.abc import Callable

from langchain_core.language_models import BaseChatModel

from enterprise_agent.models.binding import assign_input_types
from enterprise_agent.models.cards import ModelCard
from enterprise_agent.models.config import ModelProfile, ProviderConfig

Builder = Callable[[ModelProfile, ProviderConfig], BaseChatModel]

_BUILDERS: dict[str, Builder] = {}


def register(protocol: str) -> Callable[[Builder], Builder]:
    """Record ``builder`` as the constructor for ``protocol``."""

    def decorate(builder: Builder) -> Builder:
        _BUILDERS[protocol] = builder
        return builder

    return decorate


def registered_protocols() -> list[str]:
    """Return the protocol names imported so far, in sorted order."""
    _ensure_providers()
    return sorted(_BUILDERS)


def require_api_key(provider: ProviderConfig) -> str:
    """Read the provider secret from the environment variable it names.

    The returned value is for the client constructor only. Error text names the
    variable and never includes the secret itself.
    """
    name = provider.credential_ref
    if not name:
        raise RuntimeError(f"provider {provider.name} has no credential reference")
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"credential referenced by provider {provider.name} is unavailable ({name})"
        )
    return value


def build_chat_model(
    profile: ModelProfile,
    provider: ProviderConfig,
    card: ModelCard | None = None,
) -> BaseChatModel:
    """Construct the LangChain chat model selected by ``provider.protocol``.

    When ``card`` is present, its input types are stored on the model. The card
    context window is left unused on this chat path.
    """
    _ensure_providers()
    builder = _BUILDERS.get(provider.protocol)
    if builder is None:
        known = ", ".join(sorted(_BUILDERS)) or "(none)"
        raise ValueError(
            f"unknown model protocol {provider.protocol!r}; registered protocols: {known}"
        )
    model = builder(profile, provider)
    if card is not None:
        assign_input_types(model, card.input_types)
    return model


def _ensure_providers() -> None:
    if _BUILDERS:
        return
    import enterprise_agent.models.providers  # noqa: F401
