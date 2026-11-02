from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    role: str
    capabilities: frozenset[str]
    tools: frozenset[str]
    can_delegate_to: frozenset[str]


class AgentRegistry:
    def __init__(self, definitions: list[AgentDefinition]):
        self._definitions = {item.role: item for item in definitions}

    def get(self, role: str) -> AgentDefinition:
        try:
            return self._definitions[role]
        except KeyError as exc:
            raise KeyError(f"unknown agent role: {role}") from exc

    def roles(self) -> set[str]:
        return set(self._definitions)

