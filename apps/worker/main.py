import logging
import signal
import time
from uuid import uuid4

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.bootstrap.container import get_container, shutdown_container
from enterprise_agent.bootstrap.settings import Settings, load_local_env
from enterprise_agent.infrastructure.observability.logging import configure_logging
from enterprise_agent.runtime.ownership import LeaseLost, RetryExhausted

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self):
        self.container = get_container()
        self.worker_id = f"worker_{uuid4().hex[:16]}"
        self.stop_requested = False

    def stop(self, *_args) -> None:
        self.stop_requested = True

    def serve(self) -> None:
        while not self.stop_requested:
            run_id = self.container.runs.claim_work(
                self.worker_id,
                self.container.settings.lease_seconds,
                max_claim_attempts=self.container.settings.max_claim_attempts,
            )
            if run_id is None:
                time.sleep(self.container.settings.worker_poll_seconds)
                continue
            try:
                execute_run(self.container, run_id, self.worker_id)
            except LeaseLost:
                logger.warning("worker lost the lease run_id=%s", run_id)
            except RetryExhausted:
                logger.error("worker retry limit reached run_id=%s", run_id)
            except Exception:
                logger.exception("worker execution failed run_id=%s", run_id)
                self.container.runs.release_work(self.worker_id, run_id)


def main() -> None:
    load_local_env()
    settings = Settings()
    configure_logging(settings.log_level, settings.log_file)
    worker = Worker()
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    try:
        worker.serve()
    finally:
        shutdown_container()


if __name__ == "__main__":
    main()
