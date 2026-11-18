"""Versioned contract for durable orchestration state."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

STATE_SCHEMA_VERSION = 1


class StateSchemaError(ValueError):
    """Durable state cannot be safely interpreted by this application."""


def migrate_orchestration_state(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Return current state without mutating the persisted source mapping."""

    state = deepcopy(dict(raw))
    raw_version = state.get("state_version", 0)
    if isinstance(raw_version, bool) or not isinstance(raw_version, int):
        raise StateSchemaError("orchestration state_version must be an integer")
    if raw_version < 0:
        raise StateSchemaError("orchestration state_version cannot be negative")
    if raw_version > STATE_SCHEMA_VERSION:
        raise StateSchemaError(
            "orchestration state version is newer than this application supports"
        )

    version = raw_version
    while version < STATE_SCHEMA_VERSION:
        if version == 0:
            state = _migrate_v0_to_v1(state)
            version = 1
            continue
        raise StateSchemaError(f"missing orchestration state migration from v{version}")
    return state


def _migrate_v0_to_v1(state: dict[str, Any]) -> dict[str, Any]:
    """Declare legacy state as v1 without inferring or rewriting user intent."""

    return {**state, "state_version": 1}
