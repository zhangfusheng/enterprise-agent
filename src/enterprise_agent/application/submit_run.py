from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate
from enterprise_agent.infrastructure.repositories.runs import RunRepository


def submit_run(repository: RunRepository, identity: IdentityContext, request: RunCreate, config_snapshot: dict | None = None) -> str:
    run_id = repository.create(identity.tenant_id, identity.user_id, request)
    if config_snapshot:
        repository.update_run(identity.tenant_id, run_id, {"config_snapshot": config_snapshot})
    return run_id

