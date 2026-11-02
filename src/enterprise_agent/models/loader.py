from pathlib import Path
from typing import Any

import yaml

from .config import ModelConfig
from .validation import validate_model_config


def load_model_config(path: Path) -> ModelConfig:
    with path.open(encoding="utf-8") as file:
        raw: dict[str, Any] = yaml.safe_load(file) or {}
    return validate_model_config(ModelConfig.model_validate(raw))
