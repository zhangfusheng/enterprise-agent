import logging
import logging.config
import logging.handlers
import re
from pathlib import Path
from typing import Any

SECRET_PATTERN = re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,]+")
logger = logging.getLogger("enterprise_agent.orchestration")
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def configure_logging(level: str, log_file: Path | None) -> None:
    """Attach process handlers once, at startup.

    Application loggers stay at ``level`` and propagate to the root handlers.
    Library loggers stay on the root's WARNING floor. Uvicorn keeps the stderr
    handlers it installed before importing the app; a log file is added beside them.
    """
    _clear_file_handlers()
    handlers: dict[str, dict[str, Any]] = {
        "stderr": {
            "class": "logging.StreamHandler",
            "formatter": "plain",
            "stream": "ext://sys.stderr",
        }
    }
    handler_names = ["stderr"]
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "formatter": "plain",
            "filename": str(log_file),
            "maxBytes": 50_000_000,
            "backupCount": 5,
            "encoding": "utf-8",
        }
        handler_names.append("file")
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"plain": {"format": _LOG_FORMAT}},
            "handlers": handlers,
            "root": {"level": "WARNING", "handlers": handler_names},
            "loggers": {"enterprise_agent": {"level": level, "propagate": True}},
        }
    )
    if log_file is None:
        return
    file_handler = next(
        handler for handler in logging.getLogger().handlers if isinstance(handler, logging.handlers.RotatingFileHandler)
    )
    for name in _UVICORN_LOGGERS:
        named = logging.getLogger(name)
        if file_handler not in named.handlers:
            named.addHandler(file_handler)


def _clear_file_handlers() -> None:
    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)
        handler.close()
    for name in _UVICORN_LOGGERS:
        named = logging.getLogger(name)
        for handler in named.handlers[:]:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                named.removeHandler(handler)


def redact(value: str) -> str:
    return SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


def log_orchestration(node: str, **fields: object) -> None:
    """Log one orchestration event without treating user text as a field.

    ``model_version`` and ``prompt_version`` stay empty until a model call owns
    the step. Callers pass identifiers and lengths, not raw user content.
    """
    payload: dict[str, object] = {"node": node, "model_version": None, "prompt_version": None}
    payload.update(fields)
    message = " ".join(f"{key}={_field(value)}" for key, value in payload.items())
    logger.info("%s", redact(message))


def _field(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, list):
        return ",".join(str(item) for item in value) or "none"
    return str(value)

