"""Build a harness from an explicit role list and a configured model registry."""

from enterprise_agent.agents.harness.deepagents import DeepAgentsHarness
from enterprise_agent.agents.harness.registry import HarnessRoleRegistry
from enterprise_agent.agents.harness.spec import HarnessRole
from enterprise_agent.models.config import ModelConfig, ModelProfile, ProviderConfig, RoleBinding
from enterprise_agent.models.registry import ModelRegistry


def bound_harness(*roles: HarnessRole, provider: str = "offline") -> DeepAgentsHarness:
    """Assemble a harness the same way the composition root does."""
    if not roles:
        raise ValueError("bound_harness requires at least one role")
    profile = "bound"
    return DeepAgentsHarness(
        ModelRegistry(
            ModelConfig(
                providers=[ProviderConfig(name=provider, protocol="fake")],
                profiles=[
                    ModelProfile(
                        name=profile,
                        provider=provider,
                        model_name="deterministic-v1",
                        capabilities={"structured_output", "tool_calling"},
                    )
                ],
                bindings=[RoleBinding(role=role.name, model_profile=profile) for role in roles],
            )
        ),
        HarnessRoleRegistry(roles),
    )


def offline_harness(*roles: HarnessRole) -> DeepAgentsHarness:
    """Bind the given roles to the offline provider."""
    return bound_harness(*roles, provider="offline")
