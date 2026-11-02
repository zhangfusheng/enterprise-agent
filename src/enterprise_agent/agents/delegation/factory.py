from enterprise_agent.agents.delegation.service import DelegationService


def build_delegation_service(*args, **kwargs) -> DelegationService:
    return DelegationService(*args, **kwargs)

