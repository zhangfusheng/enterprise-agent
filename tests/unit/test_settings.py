import os
from pathlib import Path

import pytest

from enterprise_agent.bootstrap.settings import get_settings, load_local_env


def test_load_local_env_publishes_quoted_provider_secret(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('DEEPSEEK_API_KEY="from-dotenv"\n', encoding="utf-8")

    load_local_env(env_file)

    assert os.environ["DEEPSEEK_API_KEY"] == "from-dotenv"


def test_load_local_env_keeps_an_existing_process_value(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "from-process")
    env_file = tmp_path / ".env"
    env_file.write_text("DEEPSEEK_API_KEY=from-file\n", encoding="utf-8")

    load_local_env(env_file)

    assert os.environ["DEEPSEEK_API_KEY"] == "from-process"


def test_load_local_env_ignores_a_missing_file(tmp_path: Path):
    load_local_env(tmp_path / "missing.env")


def test_get_settings_publishes_provider_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "EA_ENVIRONMENT=development",
                "EA_DEV_IDENTITY=true",
                "EA_OFFLINE_MODE=true",
                f"EA_DATABASE_URL=sqlite:///{tmp_path}/app.sqlite",
                f"EA_CHECKPOINT_URL=sqlite:///{tmp_path}/checkpoint.sqlite",
                f"EA_ARTIFACT_ROOT={tmp_path / 'artifacts'}",
                'DEEPSEEK_API_KEY="from-dotenv"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.environment == "development"
        assert os.environ["DEEPSEEK_API_KEY"] == "from-dotenv"
    finally:
        get_settings.cache_clear()
