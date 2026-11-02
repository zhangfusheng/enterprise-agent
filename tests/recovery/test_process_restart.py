import os
import signal
import subprocess
import sys
import time

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate, RunStatus


def test_terminated_worker_is_replaced_by_new_process(container, tmp_path):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), RunCreate(input="请直接回答一个问题"))
    proc = subprocess.Popen([sys.executable, "-m", "tests.recovery.crash_worker", str(container.engine.url), str(container.settings.checkpoint_url), str(container.settings.artifact_root), run_id], cwd=os.getcwd())
    time.sleep(1)
    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=10)
    with container.sessions() as session:
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import update

        from enterprise_agent.infrastructure.database.models import WorkItemRow
        session.execute(update(WorkItemRow).where(WorkItemRow.run_id == run_id).values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        session.commit()
    assert container.runs.claim_work("replacement-worker", 30) == run_id
    execute_run(container, run_id, "replacement-worker")
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.SUCCEEDED

