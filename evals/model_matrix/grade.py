"""Independent graders.

A run status of SUCCEEDED is recorded and then ignored. Task success is the
conjunction of the automatic rules on the case.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).parent
_VALUE_TOLERANCE = 1e-9


def load_dataset(path: Path | None = None) -> dict[str, Any]:
    source = path or _ROOT / "dataset.yaml"
    loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or not isinstance(loaded.get("cases"), list):
        raise ValueError("dataset must contain a cases list")
    return loaded


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Linear percentile. p=0 is the minimum and p=1 is the maximum."""
    if not values:
        return None
    if not 0 <= pct <= 1:
        raise ValueError("percentile must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (len(ordered) - 1) * pct
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    weight = rank - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def grade_case(case: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
    rules = case["validation"]
    route_ok = observation.get("route") == case["expected_route"]
    role_ok = _role_ok(case, observation)
    tools = _tool_names(observation)
    tool_selection_ok = _tools_ok(rules, tools, case.get("allowed_tools") or [])
    tool_argument_ok, calculation_ok = _calculation_ok(rules, observation)
    dag_ok = _dag_ok(rules, observation.get("tasks") or [])
    fixture_ok = _fixture_ok(case, rules, observation)
    rubric_ok = _rubric_ok(rules, observation.get("summary") or "")
    no_tools_ok = (not tools) if rules.get("no_tools") else True
    checks = {
        "route": route_ok,
        "role": role_ok,
        "tool_selection": tool_selection_ok and no_tools_ok,
        "tool_argument": tool_argument_ok,
        "calculation": calculation_ok,
        "dag": dag_ok,
        "fixture": fixture_ok,
        "rubric": rubric_ok,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "failure_class": _failure_class(observation, checks),
    }


def _role_ok(case: Mapping[str, Any], observation: Mapping[str, Any]) -> bool:
    expected = case.get("expected_role")
    if not expected:
        return True
    return observation.get("selected_role") == expected


def _tool_names(observation: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for call in observation.get("tool_calls") or []:
        if isinstance(call, Mapping) and isinstance(call.get("name"), str):
            names.append(call["name"])
    return names


def _tools_ok(rules: Mapping[str, Any], names: Sequence[str], allowed: Sequence[str]) -> bool:
    allowed_set = set(allowed)
    if allowed_set and any(name not in allowed_set for name in names):
        return False
    required_all = list(rules.get("tools_all") or [])
    required_any = list(rules.get("tools_any") or [])
    if any(name not in names for name in required_all):
        return False
    if required_any and not any(name in names for name in required_any):
        return False
    return True


def _calculation_ok(rules: Mapping[str, Any], observation: Mapping[str, Any]) -> tuple[bool, bool]:
    expected = rules.get("calculation")
    if not isinstance(expected, Mapping):
        return True, True
    calls = [
        call
        for call in observation.get("tool_calls") or []
        if isinstance(call, Mapping) and call.get("name") == "calculate"
    ]
    matched = [call for call in calls if _arguments_match(call.get("arguments") or {}, expected)]
    argument_ok = bool(matched)
    accepted = [call for call in matched if not call.get("rejected")]
    value_ok = _close(observation.get("calculation_value"), expected["value"]) or any(
        _close(_computed(call.get("arguments") or {}), expected["value"]) for call in accepted
    )
    return argument_ok, argument_ok and value_ok


def _close(actual: object, expected: object) -> bool:
    if isinstance(actual, bool) or not isinstance(actual, int | float):
        return False
    return abs(float(actual) - float(expected)) <= _VALUE_TOLERANCE


def _computed(arguments: Mapping[str, Any]) -> float | None:
    from enterprise_agent.tools.calculate import calculate

    operation = arguments.get("operation")
    values = arguments.get("values")
    if not isinstance(operation, str) or not isinstance(values, list):
        return None
    try:
        return float(calculate(operation, [float(item) for item in values]))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _arguments_match(arguments: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    if arguments.get("operation") != expected["operation"]:
        return False
    values = arguments.get("values")
    if not isinstance(values, list) or len(values) != len(expected["values"]):
        return False
    expected_values = expected["values"]
    return all(
        abs(float(left) - float(right)) <= _VALUE_TOLERANCE
        for left, right in zip(values, expected_values, strict=True)
    )


def _dag_ok(rules: Mapping[str, Any], tasks: Sequence[Mapping[str, Any]]) -> bool:
    if rules.get("dag") != "research_before_analysis":
        return True
    by_id = {str(task.get("task_id")): task for task in tasks if isinstance(task, Mapping)}
    research = {task_id for task_id, task in by_id.items() if task.get("agent_role") == "research"}
    analysis = [task for task in by_id.values() if task.get("agent_role") == "analysis"]
    if not research or not analysis:
        return False
    return all(_reaches_research(task, by_id, research) for task in analysis)


def _reaches_research(
    task: Mapping[str, Any],
    by_id: Mapping[str, Mapping[str, Any]],
    research: set[str],
) -> bool:
    seen: set[str] = set()
    stack = [str(item) for item in task.get("depends_on") or []]
    while stack:
        current = stack.pop()
        if current in seen or current not in by_id:
            continue
        seen.add(current)
        if current in research or by_id[current].get("agent_role") == "research":
            return True
        stack.extend(str(item) for item in by_id[current].get("depends_on") or [])
    return False


def _fixture_ok(
    case: Mapping[str, Any], rules: Mapping[str, Any], observation: Mapping[str, Any]
) -> bool:
    del case
    if not rules.get("fixture_url"):
        return True
    prefix = "https://fixture.invalid/"
    return any(
        isinstance(url, str) and url.startswith(prefix) for url in observation.get("sources") or []
    )


def _rubric_ok(rules: Mapping[str, Any], summary: str) -> bool:
    rubric = rules.get("rubric")
    if not isinstance(rubric, Mapping):
        return True
    folded = summary.casefold()
    required = [str(item).casefold() for item in rubric.get("all") or []]
    if any(item not in folded for item in required):
        return False
    options = [str(item).casefold() for item in rubric.get("any") or []]
    if not options:
        return True
    hits = sum(item in folded for item in options)
    minimum = int(rubric.get("minimum_hits", 1))
    return hits >= minimum


def _failure_class(observation: Mapping[str, Any], checks: Mapping[str, bool]) -> str | None:
    if observation.get("timed_out"):
        return "timeout"
    error = str(observation.get("error") or "")
    if error:
        lowered = error.casefold()
        if "timeout" in lowered or "timed out" in lowered:
            return "timeout"
        return "execution_error"
    if observation.get("status") == "WAITING_INPUT":
        return "waiting_input"
    if observation.get("status") in {"FAILED", "CANCELLED"} and all(checks.values()):
        return "execution_error"
    order = (
        ("route", "router"),
        ("dag", "dag"),
        ("tool_selection", "tool_selection"),
        ("tool_argument", "tool_argument"),
        ("calculation", "calculation"),
        ("fixture", "tool_result"),
        ("rubric", "answer_rubric"),
        ("role", "role"),
    )
    for check, label in order:
        if not checks[check]:
            return label
    if observation.get("status") not in {"SUCCEEDED", "PARTIAL", None}:
        return "execution_error"
    return None
