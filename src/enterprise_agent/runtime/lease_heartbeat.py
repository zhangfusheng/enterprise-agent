"""Background lease renewal for one owned run."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from enterprise_agent.runtime.ownership import LeaseGuard, RenewResult

logger = logging.getLogger(__name__)


class LeaseHeartbeat:
    """Renew a fencing token until the execution scope exits.

    A database error is logged and retried on the next interval. A confirmed
    ``NOT_OWNER`` result marks the guard so the execution stops writing.
    """

    def __init__(
        self,
        renew: Callable[[], RenewResult],
        interval_seconds: float,
        guard: LeaseGuard,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("lease renew interval must be positive")
        self._renew = renew
        self._interval = interval_seconds
        self._guard = guard
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="lease-heartbeat", daemon=True)
        self._failures = 0

    def __enter__(self) -> LeaseHeartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 1)

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            if self._guard.lost:
                return
            try:
                result = self._renew()
            except Exception:
                self._failures += 1
                logger.warning(
                    "lease renew failed failures=%s",
                    self._failures,
                    exc_info=self._failures == 1,
                )
                if self._failures >= 3:
                    logger.error("lease renew still failing failures=%s", self._failures)
                continue
            self._failures = 0
            if result != RenewResult.RENEWED:
                self._guard.mark_lost("lease renew rejected: not the current owner")
                logger.error("lease renew rejected: worker is not the current owner")
                return
