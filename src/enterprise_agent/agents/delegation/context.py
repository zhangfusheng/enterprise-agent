from enterprise_agent.contracts.context import ExecutionContext


def child_context(parent: ExecutionContext, role: str, agent_id: str, delegation_id: str, allowed_tools: set[str]) -> ExecutionContext:
    return parent.model_copy(update={
        "agent_id": agent_id,
        "parent_agent_id": parent.agent_id,
        "delegation_id": delegation_id,
        "depth": parent.depth + 1,
        "allowed_roles": {role},
        "allowed_tools": parent.allowed_tools & allowed_tools,
        "metadata": {**parent.metadata, "role": role},
    })

