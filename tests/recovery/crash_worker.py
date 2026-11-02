import sys
import time
from pathlib import Path

from enterprise_agent.bootstrap.container import Container
from enterprise_agent.bootstrap.settings import Settings

if __name__ == "__main__":
    database_url, checkpoint_url, artifact_root, run_id = sys.argv[1:]
    container = Container(Settings(database_url=database_url, checkpoint_url=checkpoint_url, artifact_root=Path(artifact_root)))
    assert container.runs.claim_work("crash-worker", 5) == run_id
    time.sleep(60)

