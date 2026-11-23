"""Run the model matrix against a temporary database and the fixture web snapshot."""

from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.bootstrap.container import Container
from enterprise_agent.bootstrap.settings import Settings, load_local_env
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate
from enterprise_agent.contracts.traces import RunType
from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.orchestration.routing.router import Router
from enterprise_agent.tools.web.provider import ProviderRegistry, SearchProviderRouter
from enterprise_agent.tools.web.testing import FixtureSearchProvider, FixtureWebFetcher

from .cost import cost_for, load_prices
from .grade import grade_case, load_dataset, percentile

_IDENTITY = IdentityContext(tenant_id="eval", user_id="eval", is_authenticated=True)
_GOVERNED_TOOLS = frozenset(
    {"web_search", "web_fetch", "workspace_read", "calculate", "save_report", "submit_claims"}
)
_PROFILES = ("qwen-default", "deepseek-default", "doubao-default")
_MODES = ("auto", "forced")


def build_container(root: Path, *, live_models: bool) -> Container:
    """Create an isolated container whose web tools are the local fixture."""
    root.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        environment="development",
        database_url=f"sqlite:///{root / 'app.sqlite'}",
        checkpoint_url=f"sqlite:///{root / 'checkpoint.sqlite'}",
        artifact_root=root / "artifacts",
        offline_mode=not live_models,
        allow_external_network=True,
        dev_identity=True,
        research_min_usable_sources=1,
        research_cache_read_enabled=False,
        research_cache_write_enabled=False,
        research_cache_fetch_enabled=False,
        lease_seconds=300,
        lease_renew_interval_seconds=30,
        log_file=None,
    )
    with _fixture_backend():
        container = Container(settings)
    if "enterprise-agent.db" in settings.database_url:
        raise RuntimeError("eval container resolved the shared development database")
    return container


@contextmanager
def _fixture_backend() -> Iterator[None]:
    """Serve fixture search while real model clients stay available."""
    import enterprise_agent.bootstrap.container as composition

    original_search = composition._build_search_provider
    original_fetch = composition._build_web_fetcher

    def build_search(_settings: Settings) -> SearchProviderRouter:
        registry = ProviderRegistry([FixtureSearchProvider()])
        return SearchProviderRouter(registry, "fixture", retry_count=0)

    def build_fetch(_settings: Settings) -> FixtureWebFetcher:
        return FixtureWebFetcher()

    composition._build_search_provider = build_search
    composition._build_web_fetcher = build_fetch
    try:
        yield
    finally:
        composition._build_search_provider = original_search
        composition._build_web_fetcher = original_fetch


@contextmanager
def _forced_rules() -> Iterator[None]:
    """Use the rule parser so the route does not depend on the model under test."""
    import enterprise_agent.application.execute_run as execution

    original = execution._request_parser

    def rules(
        container: Container, model_profile: str | None = None, reasoning_effort: str | None = None
    ):
        del container, model_profile, reasoning_effort
        return GoalParser()

    execution._request_parser = rules
    try:
        yield
    finally:
        execution._request_parser = original


def assert_dataset_routes(dataset: Mapping[str, Any]) -> None:
    parser = GoalParser()
    router = Router()
    for case in dataset["cases"]:
        decision = router.decide(parser.parse(case["input"], case.get("inputs") or []))
        if decision.route != case["expected_route"]:
            raise AssertionError(
                f"{case['case_id']} routed {decision.route}, expected {case['expected_route']}"
            )
        expected_role = case.get("expected_role")
        if expected_role and decision.selected_role != expected_role:
            raise AssertionError(
                f"{case['case_id']} selected {decision.selected_role}, expected {expected_role}"
            )


def run_matrix(
    *,
    live_models: bool,
    profiles: tuple[str, ...],
    modes: tuple[str, ...],
    repeats: int,
    output: Path,
    case_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    load_local_env()
    dataset = load_dataset()
    prices = load_prices()
    assert_dataset_routes(dataset)
    if case_ids:
        known = {case["case_id"] for case in dataset["cases"]}
        missing = sorted(set(case_ids) - known)
        if missing:
            raise ValueError(f"unknown case ids: {missing}")
        dataset = {
            **dataset,
            "cases": [case for case in dataset["cases"] if case["case_id"] in set(case_ids)],
        }
    started = datetime.now(UTC)
    root = output.parent / f".work-{output.stem}"
    container = build_container(root, live_models=live_models)
    records: list[dict[str, Any]] = []
    try:
        if live_models:
            _require_credentials(container, profiles)
        for profile in profiles:
            for mode in modes:
                for case in dataset["cases"]:
                    for repeat in range(1, repeats + 1):
                        records.append(
                            _run_case(
                                container,
                                case,
                                profile=profile,
                                mode=mode,
                                repeat=repeat,
                                live_models=live_models,
                                prices=prices,
                            )
                        )
    finally:
        container.close()
    report = {
        "config": _config(dataset, prices, profiles, modes, repeats, live_models, started),
        "records": records,
        "summary": summarize(records),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def summarize(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    routes: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    difficulties: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        groups[f"{record['profile']}|{record['mode']}"].append(record)
        routes[f"{record['profile']}|{record['expected_route']}"].append(record)
        difficulties[f"{record['profile']}|{record['difficulty']}"].append(record)
    return {
        "by_profile_mode": {key: _slice(value) for key, value in sorted(groups.items())},
        "by_profile_route": {key: _slice(value) for key, value in sorted(routes.items())},
        "by_profile_difficulty": {
            key: _slice(value) for key, value in sorted(difficulties.items())
        },
    }


def _slice(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    count = len(records)
    failures: dict[str, int] = defaultdict(int)
    for record in records:
        label = record.get("failure_class")
        if isinstance(label, str):
            failures[label] += 1
    e2e = [float(record["e2e_ms"]) for record in records if record.get("e2e_ms") is not None]
    model = [
        float(record["model_latency_ms"])
        for record in records
        if record.get("model_latency_ms") is not None
    ]
    tool = [
        float(record["tool_latency_ms"])
        for record in records
        if record.get("tool_latency_ms") is not None
    ]
    return {
        "runs": count,
        "task_success_rate": _mean(1.0 if record["task_success"] else 0.0 for record in records),
        "router_accuracy": _mean(1.0 if record["router_correct"] else 0.0 for record in records),
        "tool_selection_accuracy": _mean(
            1.0 if record["checks"]["tool_selection"] else 0.0 for record in records
        ),
        "tool_argument_accuracy": _mean(
            1.0 if record["checks"]["tool_argument"] else 0.0 for record in records
        ),
        "dag_accuracy": _mean(1.0 if record["checks"]["dag"] else 0.0 for record in records),
        "timeout_rate": _mean(
            1.0 if record.get("failure_class") == "timeout" else 0.0 for record in records
        ),
        "failure_rate": _mean(1.0 if _failed(record) else 0.0 for record in records),
        "incomplete_rate": _mean(
            1.0 if record.get("status") == "WAITING_INPUT" else 0.0 for record in records
        ),
        "e2e_ms_p50": percentile(e2e, 0.50),
        "e2e_ms_p95": percentile(e2e, 0.95),
        "model_latency_ms_p50": percentile(model, 0.50),
        "model_latency_ms_p95": percentile(model, 0.95),
        "tool_latency_ms_p50": percentile(tool, 0.50),
        "tool_latency_ms_p95": percentile(tool, 0.95),
        "input_tokens_mean": _mean(float(record["input_tokens"]) for record in records),
        "output_tokens_mean": _mean(float(record["output_tokens"]) for record in records),
        "cost_usd_mean": _mean(
            float(record["cost_usd"]) for record in records if record.get("cost_usd") is not None
        ),
        "failure_classes": dict(sorted(failures.items())),
    }


def _run_case(
    container: Container,
    case: Mapping[str, Any],
    *,
    profile: str,
    mode: str,
    repeat: int,
    live_models: bool,
    prices: Mapping[str, Any],
) -> dict[str, Any]:
    worker = f"eval-{case['case_id']}-{profile}-{mode}-{repeat}"
    started_at = datetime.now(UTC)
    clock = time.perf_counter()
    error = None
    run_id = None
    claimed = False
    context = _forced_rules() if mode == "forced" else nullcontext()
    try:
        with context:
            run_id = submit_run(
                container.runs,
                _IDENTITY,
                RunCreate(
                    input=case["input"],
                    inputs=list(case.get("inputs") or []),
                    model_profile=None if not live_models else profile,
                ),
            )
            claimed_id = container.runs.claim_work(worker, container.settings.lease_seconds)
            claimed = claimed_id == run_id
            if not claimed:
                raise RuntimeError(f"claim returned {claimed_id}, expected {run_id}")
            execute_run(container, run_id, worker)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
    finally:
        if claimed and run_id is not None:
            try:
                container.runs.finish_work(worker, run_id)
            except Exception:
                traceback.print_exc()
    elapsed_ms = int((time.perf_counter() - clock) * 1000)
    observation = _observation(container, run_id, error, elapsed_ms)
    graded = grade_case(case, observation)
    usage = _usage(observation, profile, prices, started_at, live_models)
    return {
        "case_id": case["case_id"],
        "difficulty": case["difficulty"],
        "profile": profile,
        "mode": mode,
        "repeat": repeat,
        "input": case["input"],
        "inputs": case.get("inputs") or [],
        "expected_route": case["expected_route"],
        "expected_result": case["expected_result"],
        "run_id": run_id,
        "task_success": graded["passed"],
        "router_correct": graded["checks"]["route"],
        "checks": graded["checks"],
        "failure_class": graded["failure_class"],
        "judge": case["judge"],
        **observation,
        **usage,
        "started_at": started_at.isoformat(),
    }


def _observation(
    container: Container,
    run_id: str | None,
    error: str | None,
    elapsed_ms: int,
) -> dict[str, Any]:
    empty = {
        "status": None,
        "route": None,
        "selected_role": None,
        "summary": "",
        "tasks": [],
        "tool_calls": [],
        "sources": [],
        "calculation_value": None,
        "error": error,
        "timed_out": False,
        "e2e_ms": elapsed_ms,
        "model_latency_ms": 0,
        "tool_latency_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "production_cost_usd": None,
        "metrics": {},
        "trace_tools": [],
    }
    if run_id is None:
        return empty
    row = container.runs.get_run_unscoped(run_id)
    state = row.state_json if isinstance(row.state_json, dict) else {}
    view = container.runs.get_run_view(_IDENTITY.tenant_id, run_id)
    route = state.get("route") if isinstance(state.get("route"), dict) else {}
    plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    research = metrics.get("research") if isinstance(metrics.get("research"), dict) else {}
    persisted_error = (
        state.get("error_message") if isinstance(state.get("error_message"), str) else None
    )
    tool_calls = _tool_calls(container, run_id, research)
    return {
        "status": view.status.value,
        "route": route.get("route") or view.route,
        "selected_role": route.get("selected_role"),
        "summary": view.summary or state.get("summary") or "",
        "tasks": list(plan.get("tasks") or []),
        "tool_calls": tool_calls,
        "sources": _sources(state),
        "calculation_value": _calculation_value(state),
        "error": error or persisted_error,
        "timed_out": _is_timeout(error or persisted_error),
        "e2e_ms": elapsed_ms,
        "model_latency_ms": int(metrics.get("latency_ms") or 0),
        "tool_latency_ms": int(research.get("total_latency_ms") or 0),
        "input_tokens": int(metrics.get("input_tokens") or 0),
        "output_tokens": int(metrics.get("output_tokens") or 0),
        "production_cost_usd": metrics.get("cost_usd"),
        "metrics": metrics,
        "trace_tools": [call["name"] for call in tool_calls],
    }


def _tool_calls(
    container: Container, run_id: str, research: Mapping[str, Any]
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    view = container.traces.get(_IDENTITY.tenant_id, run_id)
    for span in view.runs:
        if span.run_type != RunType.TOOL or span.name not in _GOVERNED_TOOLS:
            continue
        raw = span.inputs if isinstance(span.inputs, dict) else {}
        arguments = (
            raw.get("arguments") if isinstance(raw.get("arguments"), dict) else _tool_arguments(raw)
        )
        calls.append(
            {
                "name": span.name,
                "arguments": arguments,
                "rejected": _tool_rejected(span.outputs, span.error),
                "latency_ms": span.duration_ms,
            }
        )
    names = {"search": "web_search", "fetch": "web_fetch"}
    for event in research.get("events") or []:
        if not isinstance(event, dict):
            continue
        tool = names.get(str(event.get("stage")))
        if tool is None:
            continue
        calls.append({"name": tool, "arguments": {}, "latency_ms": event.get("latency_ms")})
    return calls


def _tool_arguments(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in raw.items() if key != "tool_call_id"}


def _tool_rejected(outputs: object, error: object) -> bool:
    if error:
        return True
    text = json.dumps(outputs, ensure_ascii=False) if outputs is not None else ""
    return '"ok": false' in text or '"ok":false' in text or "FAILED" in text


def _sources(state: Mapping[str, Any]) -> list[str]:
    found: list[str] = []
    blobs: list[object] = []
    results = state.get("task_results")
    if isinstance(results, dict):
        blobs.extend(results.values())
    final = state.get("result")
    if isinstance(final, dict):
        blobs.append(final)
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        found.extend(_urls(blob.get("sources")))
        structured = blob.get("structured_data")
        if isinstance(structured, dict):
            found.extend(_urls(structured.get("evidence")))
    return list(dict.fromkeys(found))


def _urls(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    urls: list[str] = []
    for item in value:
        if isinstance(item, str):
            urls.append(item)
        elif isinstance(item, dict) and isinstance(item.get("url"), str):
            urls.append(item["url"])
    return urls


def _calculation_value(state: Mapping[str, Any]) -> float | None:
    blobs: list[object] = []
    results = state.get("task_results")
    if isinstance(results, dict):
        blobs.extend(results.values())
    final = state.get("result")
    if isinstance(final, dict):
        blobs.append(final)
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        structured = blob.get("structured_data")
        if not isinstance(structured, dict):
            continue
        value = structured.get("calculation")
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        return float(value)
    return None


def _usage(
    observation: Mapping[str, Any],
    profile: str,
    prices: Mapping[str, Any],
    moment: datetime,
    live_models: bool,
) -> dict[str, Any]:
    if not live_models or profile not in prices.get("profiles", {}):
        offline = not live_models
        return {
            "cost_native": 0.0 if offline else None,
            "cost_usd": 0.0 if offline else None,
            "price_tier": "offline" if offline else "unpriced",
            "price_currency": None,
        }
    priced = cost_for(
        profile,
        dict(prices),
        int(observation["input_tokens"]),
        int(observation["output_tokens"]),
        moment,
    )
    return {
        "cost_native": priced["cost_native"],
        "cost_usd": priced["cost_usd"],
        "price_tier": priced["tier"],
        "price_currency": priced["currency"],
    }


def _require_credentials(container: Container, profiles: tuple[str, ...]) -> None:
    missing: list[str] = []
    providers = {item.name: item for item in container.model_config.providers}
    for name in profiles:
        profile = container.models.profile(name)
        provider = providers[profile.provider]
        ref = provider.credential_ref
        if ref and not os.environ.get(ref, "").strip():
            missing.append(f"{name} requires {ref}")
    if missing:
        raise RuntimeError("missing credentials: " + ", ".join(missing))


def _config(
    dataset: Mapping[str, Any],
    prices: Mapping[str, Any],
    profiles: tuple[str, ...],
    modes: tuple[str, ...],
    repeats: int,
    live_models: bool,
    started: datetime,
) -> dict[str, Any]:
    return {
        "dataset": dataset["version"],
        "prices": prices["version"],
        "profiles": list(profiles),
        "modes": list(modes),
        "repeats": repeats,
        "live_models": live_models,
        "temperature": 0,
        "max_tokens": None,
        "reasoning_effort": None,
        "tool_backend": "fixture",
        "fixture_url_prefix": dataset["fixture"]["url_prefix"],
        "database": "temporary sqlite",
        "started_at": started.isoformat(),
        "price_assumptions": prices["assumptions"],
        "fx": prices["fx"],
        "parser": "rules" if not live_models else "llm in auto mode; rules in forced mode",
        "judge": "Deterministic rubric for direct answers. No model grades its own success.",
    }


def _failed(record: Mapping[str, Any]) -> bool:
    return bool(
        record.get("error")
        or record.get("timed_out")
        or record.get("status") in {"FAILED", "CANCELLED"}
    )


def _is_timeout(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.casefold()
    return "timeout" in lowered or "timed out" in lowered


def _mean(values) -> float | None:
    collected = list(values)
    if not collected:
        return None
    return sum(collected) / len(collected)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the EnterpriseAgent model matrix")
    parser.add_argument("phase", choices=("verify", "formal"))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--profiles", default=",".join(_PROFILES))
    parser.add_argument("--cases", default="", help="Comma-separated case ids to rerun")
    args = parser.parse_args(argv)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    live = args.phase == "formal"
    profiles = (
        ("offline-default",)
        if not live
        else tuple(item.strip() for item in args.profiles.split(",") if item.strip())
    )
    output = args.output or Path("evals/results") / f"model-matrix-{args.phase}-{stamp}.json"
    report = run_matrix(
        live_models=live,
        profiles=profiles,
        modes=_MODES,
        repeats=args.repeats,
        output=output,
        case_ids=tuple(item.strip() for item in args.cases.split(",") if item.strip()),
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
