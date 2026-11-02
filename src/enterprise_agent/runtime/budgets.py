import threading
from datetime import UTC, datetime

from enterprise_agent.governance.budgets import BudgetLimits


class BudgetExceeded(RuntimeError):
    pass


class BudgetLedger:
    def __init__(self, limits: BudgetLimits, used: dict[str, int] | None = None, started_at: datetime | None = None):
        self.limits = limits
        self.used = {key: int(value) for key, value in (used or {}).items()}
        self.started_at = started_at or datetime.now(UTC)
        self._lock = threading.RLock()

    def consume(self, kind: str, amount: int = 1) -> None:
        if amount < 0:
            raise ValueError("budget amount must be non-negative")
        with self._lock:
            limit = getattr(self.limits, kind)
            next_value = self.used.get(kind, 0) + amount
            if next_value > limit:
                raise BudgetExceeded(f"budget exceeded for {kind}: {next_value}>{limit}")
            self.used[kind] = next_value

    def check_runtime(self) -> None:
        elapsed = (datetime.now(UTC) - self.started_at).total_seconds()
        if elapsed > self.limits.runtime_seconds:
            raise BudgetExceeded("runtime budget exceeded")

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self.used)

