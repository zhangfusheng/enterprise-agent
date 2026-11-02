from pathlib import Path

import pytest
from sqlalchemy import inspect

from enterprise_agent.bootstrap.container import BootstrapError, Container, _load_policy
from enterprise_agent.bootstrap.settings import Settings
from enterprise_agent.tools.workspace import WorkspaceDenied


def test_open_gateway_isolates_run_state(container: Container):
    first = container.open_gateway()
    second = container.open_gateway()

    assert first is not second
    assert first.registry is container.tools
    assert first.registry is second.registry
    assert first.budget is not second.budget
    assert first.cancellation is not second.cancellation
    assert first.idempotency_store == {}
    assert first.idempotency_store is not second.idempotency_store


def test_config_snapshot_uses_each_profiles_provider(container: Container):
    snapshot = container.config_snapshot
    providers = {item.name: item.config_version for item in container.model_config.providers}

    for profile in container.model_config.profiles:
        snapped = next(item for item in snapshot["models"]["profiles"] if item["name"] == profile.name)
        assert snapped["config_version"] == providers[profile.provider]
        assert "credential_ref" not in snapped
    assert "web_search" in snapshot["tools"]
    assert "research" in snapshot["roles"]
    assert snapshot["runtime"]["budgets"]["tool_calls"] == container.settings.max_tool_calls
    assert snapshot["versions"]["policy"] == "agents-v1"
    assert snapshot["versions"]["orchestration"] == "orchestration-v1"
    assert snapshot["versions"]["goal_prompt"] == "goal-parser-v2"
    assert snapshot["versions"]["plan_prompt"] == "plan-draft-v1"
    assert snapshot["versions"]["worker_prompt"] == "worker-prompt-v1"


def test_workspace_read_stays_inside_the_run(container: Container):
    root = container.settings.artifact_root / "tenant-a" / "run_1"
    root.mkdir(parents=True)
    (root / "notes.txt").write_text("evidence", encoding="utf-8")
    read = container.tools.get("workspace_read").function

    assert read(tenant_id="tenant-a", run_id="run_1", path="notes.txt") == "evidence"
    with pytest.raises(WorkspaceDenied):
        read(tenant_id="tenant-a", run_id="run_1", path="../run_2/notes.txt")
    with pytest.raises(WorkspaceDenied):
        read(tenant_id="../tenant-b", run_id="run_1", path="notes.txt")


def test_policy_rejects_unknown_delegate(tmp_path: Path):
    path = tmp_path / "agents.yaml"
    path.write_text(
        "roles:\n  research:\n    capabilities: []\n    tools: []\n    can_delegate_to: [missing]\n",
        encoding="utf-8",
    )

    with pytest.raises(BootstrapError, match="unknown roles"):
        _load_policy(path)


def test_production_startup_does_not_create_schema(tmp_path: Path):
    settings = Settings(
        environment="production",
        offline_mode=False,
        dev_identity=False,
        database_url=f"sqlite:///{tmp_path}/app.sqlite",
        checkpoint_url=f"sqlite:///{tmp_path}/checkpoint.sqlite",
        artifact_root=tmp_path / "artifacts",
    )
    instance = Container(settings)
    try:
        assert inspect(instance.engine).get_table_names() == []
    finally:
        instance.close()

