from dataclasses import dataclass

from enterprise_agent.tools.contracts import ToolCallable, ToolSpec


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    function: ToolCallable


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, function: ToolCallable) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec, function)

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"unknown tool: {name}") from exc

    def names(self) -> set[str]:
        return set(self._tools)

    def scopes(self) -> set[str]:
        found: set[str] = set()
        for tool in self._tools.values():
            found.update(tool.spec.required_scopes)
        return found

