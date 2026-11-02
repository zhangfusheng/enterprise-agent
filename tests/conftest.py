from pathlib import Path

import pytest

from enterprise_agent.bootstrap.container import Container
from enterprise_agent.bootstrap.settings import Settings


@pytest.fixture
def container(tmp_path: Path) -> Container:
    return Container(
        Settings(
            database_url=f"sqlite:///{tmp_path}/app.sqlite",
            checkpoint_url=f"sqlite:///{tmp_path}/checkpoint.sqlite",
            artifact_root=tmp_path / "artifacts",
            offline_mode=True,
        )
    )

