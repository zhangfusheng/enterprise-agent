from typing import Protocol


class RunStore(Protocol):
    def get_run_view(self, tenant_id: str, run_id: str): ...

