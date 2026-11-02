from enterprise_agent.infrastructure.repositories.runs import RunRepository


def query_run(repository: RunRepository, tenant_id: str, run_id: str):
    return repository.get_run_view(tenant_id, run_id)

