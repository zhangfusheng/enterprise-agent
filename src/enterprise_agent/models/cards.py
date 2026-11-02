"""Vendor model cards.

A card lists one model and the form schema for that model. The request body is the
user's ``parameters`` dict, validated by the vendor Parameters class.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_EXACT_TYPES = {"text/plain", "application/x-thinking", "application/pdf"}
_MEDIA_PREFIXES = ("image/", "audio/", "video/")
_THINKING_FIELDS = (
    "thinking_enable",
    "thinking_budget",
    "thinking_mode",
    "thinking_display",
)


class ModelCard(BaseModel):
    """One selectable model and the form schema derived from its vendor parameters."""

    name: str
    label: str
    status: Literal["active", "deprecated", "sunset"] = "active"
    deprecated_at: datetime | None = None
    input_types: list[str]
    output_types: list[str]
    context_size: int
    output_size: int
    parameter_schema: dict[str, Any]

    @classmethod
    def from_yaml(cls, path: Path, parameters_cls: type[BaseModel]) -> "ModelCard":
        """Load one card. Callers skip the file when this raises."""
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        source = _CardSource.model_validate(raw)
        return cls(
            name=source.name,
            label=source.label,
            status=source.status,
            deprecated_at=source.deprecated_at,
            input_types=source.input_types,
            output_types=source.output_types,
            context_size=source.context_size,
            output_size=source.output_size,
            parameter_schema=_parameter_schema(parameters_cls, source),
        )


class _CardSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    label: str = Field(min_length=1)
    status: Literal["active", "deprecated", "sunset"] = "active"
    deprecated_at: datetime | None = None
    input_types: list[str] = Field(default_factory=lambda: ["text/plain"])
    output_types: list[str] = Field(default_factory=lambda: ["text/plain"])
    context_size: int = Field(gt=0)
    output_size: int = Field(gt=0)
    parameter_overrides: dict[str, Any] | None = None

    @field_validator("input_types", "output_types")
    @classmethod
    def types_are_known(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("type list must not be empty")
        unknown = [item for item in values if not _allowed_type(item)]
        if unknown:
            raise ValueError(f"unsupported content types: {unknown}")
        return values


def _allowed_type(value: str) -> bool:
    if value in _EXACT_TYPES:
        return True
    return any(value.startswith(prefix) and len(value) > len(prefix) for prefix in _MEDIA_PREFIXES)


def _parameter_schema(parameters_cls: type[BaseModel], source: _CardSource) -> dict[str, Any]:
    raw_schema = parameters_cls.model_json_schema()
    properties = {
        key: dict(value)
        for key, value in raw_schema.get("properties", {}).items()
        if isinstance(value, dict)
    }
    required = [item for item in raw_schema.get("required", []) if isinstance(item, str)]
    if "application/x-thinking" not in source.output_types:
        for key in _THINKING_FIELDS:
            properties.pop(key, None)
    if not any(item.startswith("audio/") for item in source.output_types):
        properties.pop("voice", None)
    if "max_tokens" in properties:
        properties["max_tokens"]["maximum"] = source.output_size
    for key, override in (source.parameter_overrides or {}).items():
        if override is None or (isinstance(override, dict) and override.get("hidden") is True):
            properties.pop(key, None)
            continue
        if key not in properties or not isinstance(override, dict):
            if key in properties:
                raise ValueError(f"parameter override {key} must be a mapping or null")
            continue
        properties[key] = {**properties[key], **override}
    return {
        "type": "object",
        "properties": properties,
        "required": [item for item in required if item in properties],
    }
