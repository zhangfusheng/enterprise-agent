from copy import deepcopy

import pytest

from enterprise_agent.contracts.state import (
    STATE_SCHEMA_VERSION,
    StateSchemaError,
    migrate_orchestration_state,
)


def test_legacy_state_is_migrated_without_rewriting_nested_contracts():
    legacy = {
        "goal_spec": {
            "input_requirements": [
                {
                    "id": "requirement_material",
                    "status": "missing",
                    "source": "legacy",
                }
            ]
        },
        "parse_provenance": {"parser": "llm", "prompt_version": "goal-parser-v4"},
    }
    original = deepcopy(legacy)

    migrated = migrate_orchestration_state(legacy)

    assert migrated["state_version"] == STATE_SCHEMA_VERSION
    assert migrated["goal_spec"] == legacy["goal_spec"]
    assert migrated["parse_provenance"] == legacy["parse_provenance"]
    assert legacy == original


def test_current_state_migration_is_idempotent_and_returns_an_independent_copy():
    current = {
        "state_version": STATE_SCHEMA_VERSION,
        "inputs": [{"name": "user_input", "content": "private"}],
    }

    first = migrate_orchestration_state(current)
    second = migrate_orchestration_state(first)

    assert first == second == current
    assert first is not current
    assert first["inputs"] is not current["inputs"]


@pytest.mark.parametrize("version", [True, "1", -1, STATE_SCHEMA_VERSION + 1])
def test_invalid_or_future_versions_fail_closed(version):
    with pytest.raises(StateSchemaError):
        migrate_orchestration_state({"state_version": version})
