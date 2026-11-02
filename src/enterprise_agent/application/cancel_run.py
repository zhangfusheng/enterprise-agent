from enterprise_agent.infrastructure.repositories.runs import RunRepository


def cancel_run(repository: RunRepository, tenant_id: str, run_id: str) -> None:
    repository.request_cancel(tenant_id, run_id)

