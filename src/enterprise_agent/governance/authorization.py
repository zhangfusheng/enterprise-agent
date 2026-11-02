from dataclasses import dataclass

from enterprise_agent.contracts.context import ExecutionContext


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class RolePolicy:
    role: str
    capabilities: frozenset[str]
    tools: frozenset[str]
    can_delegate_to: frozenset[str]


class AuthorizationPolicy:
    def __init__(self, policies: list[RolePolicy]):
        self._policies = {item.role: item for item in policies}

    def roles(self) -> set[str]:
        return set(self._policies)

    def tools(self) -> set[str]:
        found: set[str] = set()
        for policy in self._policies.values():
            found.update(policy.tools)
        return found

    def get_role_policy(self, role: str) -> RolePolicy:
        try:
            return self._policies[role]
        except KeyError as exc:
            raise AuthorizationError(f"unknown agent role: {role}") from exc

    def require_capabilities(self, role: str, required_capabilities: set[str]) -> None:
        policy = self.get_role_policy(role)
        missing = required_capabilities - policy.capabilities
        if missing:
            raise AuthorizationError(f"role {role} lacks capabilities: {sorted(missing)}")

    def authorize_tool(self, context: ExecutionContext, tool_name: str) -> None:
        if tool_name not in context.allowed_tools:
            raise AuthorizationError(f"tool {tool_name} is not allowed for this execution context")
        role = context.metadata.get("role", "subagent")
        if tool_name not in self.get_role_policy(role).tools:
            raise AuthorizationError(f"tool {tool_name} is not allowed for role {role}")

    def issue_delegated_context(
        self,
        parent: ExecutionContext,
        child_role: str,
        child_agent_id: str,
        delegation_id: str,
    ) -> ExecutionContext:
        parent_role = parent.metadata.get("role", "subagent")
        parent_policy = self.get_role_policy(parent_role)
        if child_role not in parent_policy.can_delegate_to:
            raise AuthorizationError(f"{parent_role} cannot delegate to {child_role}")
        child_policy = self.get_role_policy(child_role)
        return parent.model_copy(
            update={
                "agent_id": child_agent_id,
                "parent_agent_id": parent.agent_id,
                "delegation_id": delegation_id,
                "depth": parent.depth + 1,
                "allowed_roles": parent.allowed_roles & {child_role},
                "allowed_tools": parent.allowed_tools & child_policy.tools,
                "metadata": {**parent.metadata, "role": child_role},
            }
        )

