import random
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class LeaseLost(RuntimeError):
    """The worker no longer holds the fencing token for this run."""


class RetryExhausted(RuntimeError):
    """Claim, execution, or lease-loss limits moved the work item to DEAD."""


class RenewResult(StrEnum):
    RENEWED = "renewed"
    NOT_OWNER = "not_owner"


_RETRY_DELAYS = (5, 15, 45, 120)


class LeaseGuard:
    """In-process signal that the heartbeat lost the fencing token."""

    def __init__(self) -> None:
        self.lost = False
        self.reason = ""

    def mark_lost(self, reason: str) -> None:
        self.lost = True
        self.reason = reason

    def raise_if_lost(self) -> None:
        if self.lost:
            raise LeaseLost(self.reason or "lease lost")


_guard: ContextVar[LeaseGuard | None] = ContextVar("lease_guard", default=None)


@contextmanager
def lease_scope(guard: LeaseGuard):
    token = _guard.set(guard)
    try:
        yield guard
    finally:
        _guard.reset(token)


def raise_if_lease_lost() -> None:
    guard = _guard.get()
    if guard is not None:
        guard.raise_if_lost()


def lease_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return True
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= datetime.now(UTC)


def lease_deadline(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


def renew_interval_seconds(lease_seconds: int, configured: int) -> int:
    """Renew at least three times inside one lease."""
    third = max(1, lease_seconds // 3)
    return max(1, min(configured, third))


def retry_delay_seconds(attempt: int) -> float:
    """Exponential backoff with ±20% jitter. Attempt 1 uses the first delay."""
    index = min(max(attempt, 1), len(_RETRY_DELAYS)) - 1
    base = _RETRY_DELAYS[index]
    spread = base * 0.2
    return base + random.uniform(-spread, spread)
