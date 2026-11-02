from enterprise_agent.infrastructure.repositories.runs import RunRepository


def resume_run(repository: RunRepository, tenant_id: str, run_id: str, additional_input: str) -> None:
    repository.append_input(tenant_id, run_id, additional_input)
    repository.requeue_work(run_id, tenant_id=tenant_id)

