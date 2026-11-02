import logging
import logging.handlers
from pathlib import Path

import pytest
from pydantic import ValidationError

from enterprise_agent.bootstrap.settings import Settings
from enterprise_agent.infrastructure.observability.logging import (
    configure_logging,
    log_orchestration,
)

_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


@pytest.fixture
def logging_sandbox():
    yield
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.WARNING)
    enterprise = logging.getLogger("enterprise_agent")
    enterprise.setLevel(logging.NOTSET)
    enterprise.propagate = True
    for name in _UVICORN_LOGGERS:
        named = logging.getLogger(name)
        for handler in named.handlers[:]:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                named.removeHandler(handler)
                handler.close()


def test_log_file_receives_app_records_only(tmp_path: Path, logging_sandbox: None):
    log_file = tmp_path / "logs" / "app.log"
    configure_logging("INFO", log_file)
    log_orchestration("parse_route", run_id="run_1", note="api_key=supersecret")
    logging.getLogger("sqlalchemy.engine").info("sql noise")
    logging.getLogger("sqlalchemy.engine").warning("pool warning")
    logging.getLogger("uvicorn.error").warning("access line")
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = log_file.read_text(encoding="utf-8")
    assert "enterprise_agent.orchestration" in text
    assert "node=parse_route" in text
    assert "run_id=run_1" in text
    assert "api_key=[REDACTED]" in text
    assert "supersecret" not in text
    assert "pool warning" in text
    assert "sql noise" not in text
    assert "access line" in text
    assert any(isinstance(handler, logging.handlers.RotatingFileHandler) for handler in logging.getLogger("uvicorn.error").handlers)

    configure_logging("INFO", None)
    assert not any(isinstance(handler, logging.handlers.RotatingFileHandler) for handler in logging.getLogger().handlers)
    for name in _UVICORN_LOGGERS:
        assert not any(isinstance(handler, logging.handlers.RotatingFileHandler) for handler in logging.getLogger(name).handlers)


def test_settings_normalize_log_destination():
    settings = Settings(log_level="info", log_file="")
    assert settings.log_level == "INFO"
    assert settings.log_file is None

    with pytest.raises(ValidationError, match="log level"):
        Settings(log_level="verbose")
