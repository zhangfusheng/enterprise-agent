"""Declarative description of one worker role inside the Deep Agents harness."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enterprise_agent.agents.harness.chat import RoleHarnessChatModel

HIDDEN_BUILTIN_TOOLS = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
        "task",
        "write_todos",
    }
)


@dataclass(frozen=True, slots=True)
class HarnessRole:
    """One role's prompt, chat model, and Deep Agents tool surface.

    ``application_tools_only`` roles can call only the tools passed into
    ``invoke``. They cannot see the built-in filesystem, shell, task, or todo
    tools, and they cannot enable the general-purpose subagent. Other roles
    keep those Deep Agents tools and receive todo middleware.
    """

    name: str
    system_prompt: str
    chat_model: type[RoleHarnessChatModel]
    application_tools_only: bool = False
    excluded_tools: frozenset[str] = frozenset()
    general_purpose_subagent: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name
            or self.name != self.name.strip()
            or any(character.isspace() for character in self.name)
        ):
            raise ValueError(f"invalid harness role name: {self.name!r}")
        if not isinstance(self.system_prompt, str) or not self.system_prompt.strip():
            raise ValueError(f"harness role {self.name} requires a system prompt")
        if not isinstance(self.chat_model, type):
            raise TypeError(f"harness role {self.name} chat_model must be a class")
        from enterprise_agent.agents.harness.chat import RoleHarnessChatModel

        if not issubclass(self.chat_model, RoleHarnessChatModel):
            raise TypeError(
                f"harness role {self.name} chat_model must subclass RoleHarnessChatModel"
            )
        if self.application_tools_only and self.general_purpose_subagent:
            raise ValueError(
                f"role {self.name} limited to application tools "
                "cannot enable the general-purpose subagent"
            )
        if self.application_tools_only and not self.excluded_tools:
            raise ValueError(
                f"role {self.name} limited to application tools must exclude built-in tools"
            )
        _require_tool_names(self.name, self.excluded_tools)


def application_tools_role(
    name: str,
    system_prompt: str,
    chat_model: type[RoleHarnessChatModel],
) -> HarnessRole:
    """Build a role that can use only the tools passed into ``invoke``."""
    return HarnessRole(
        name=name,
        system_prompt=system_prompt,
        chat_model=chat_model,
        application_tools_only=True,
        excluded_tools=HIDDEN_BUILTIN_TOOLS,
        general_purpose_subagent=False,
    )


def _require_tool_names(role: str, tools: Iterable[str]) -> None:
    for name in tools:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"harness role {role} has an invalid excluded tool name: {name!r}")
