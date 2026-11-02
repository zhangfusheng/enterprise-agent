from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.governance.authorization import AuthorizationError, AuthorizationPolicy


class DelegationGuard:
    def __init__(self, policy: AuthorizationPolicy, max_depth: int, max_agents: int):
        self.policy = policy
        self.max_depth = max_depth
        self.max_agents = max_agents

    def check(self, parent: ExecutionContext, child_role: str, agent_count: int) -> None:
        if parent.depth >= self.max_depth:
            raise AuthorizationError("maximum delegation depth reached")
        if agent_count >= self.max_agents:
            raise AuthorizationError("maximum run agent count reached")
        if child_role not in parent.allowed_roles:
            raise AuthorizationError(f"child role {child_role} is outside delegated role scope")

