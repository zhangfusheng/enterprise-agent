"""Registry of worker roles one Deep Agents harness is allowed to assemble."""

from __future__ import annotations

from collections.abc import Sequence

from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)

from enterprise_agent.agents.harness.chat import ENTERPRISE_PROVIDER, RoleHarnessChatModel
from enterprise_agent.agents.harness.spec import HarnessRole


class HarnessRoleRegistry:
    """Roles supplied by the composition root.

    The harness does not choose a default set. Register each worker before
    building the harness. The role's chat model selects its Deep Agents profile
    through ``enterprise:<role>``.
    """

    def __init__(self, roles: Sequence[HarnessRole] = ()) -> None:
        self._roles: dict[str, HarnessRole] = {}
        for role in roles:
            self.register(role)

    def register(self, role: HarnessRole) -> None:
        if not isinstance(role, HarnessRole):
            raise TypeError("harness role must be a HarnessRole")
        declared = _declared_model_name(role.chat_model)
        if declared != role.name:
            raise ValueError(
                f"{role.chat_model.__name__}.model_name defaults to {declared!r}, "
                f"but the harness role is {role.name!r}"
            )
        if role.name in self._roles:
            raise ValueError(f"duplicate harness role: {role.name}")
        _register_profile(role)
        self._roles[role.name] = role

    def get(self, name: str) -> HarnessRole:
        try:
            return self._roles[name]
        except KeyError as exc:
            known = ", ".join(sorted(self._roles)) or "(none)"
            raise KeyError(f"unknown harness role: {name}; registered roles: {known}") from exc

    def roles(self) -> set[str]:
        return set(self._roles)


def _declared_model_name(model_cls: type[RoleHarnessChatModel]) -> str:
    field = model_cls.model_fields["model_name"]
    default = field.default
    if field.is_required() or not isinstance(default, str) or not default.strip():
        raise ValueError(f"{model_cls.__name__} must default model_name to its role name")
    return default


def _register_profile(role: HarnessRole) -> None:
    register_harness_profile(
        f"{ENTERPRISE_PROVIDER}:{role.name}",
        HarnessProfile(
            excluded_tools=role.excluded_tools,
            general_purpose_subagent=GeneralPurposeSubagentProfile(
                enabled=role.general_purpose_subagent
            ),
        ),
    )
