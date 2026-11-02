from enterprise_agent.infrastructure.repositories.runs import RunRepository


class TaskQueue:
    def __init__(self, repository: RunRepository):
        self.repository = repository

    def submit(self, *args, **kwargs) -> str:
        return self.repository.create(*args, **kwargs)

    def claim(self, worker_id: str, lease_seconds: int) -> str | None:
        return self.repository.claim_work(worker_id, lease_seconds)

