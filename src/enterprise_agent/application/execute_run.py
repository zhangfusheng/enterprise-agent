"""Execute one leased run and persist its outcome."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from enterprise_agent.bootstrap.container import Container
from enterprise_agent.bootstrap.settings import Settings
from enterprise_agent.contracts.events import EventType
from enterprise_agent.contracts.plans import GlobalPlan
from enterprise_agent.contracts.runs import RunStatus
from enterprise_agent.infrastructure.database.models import RunRow
from enterprise_agent.infrastructure.observability.logging import log_orchestration
from enterprise_agent.infrastructure.observability.metrics import (
    begin_run_metrics,
    metrics_snapshot,
    reset_run_metrics,
)
from enterprise_agent.infrastructure.observability.tracing import trace, tracing_scope
from enterprise_agent.infrastructure.repositories.runs import OutcomeRecord, RunOutcome
from enterprise_agent.orchestration.graph import GraphDependencies, build_graph
from enterprise_agent.orchestration.planning.llm_planner import LLMPlanner, plan_trace
from enterprise_agent.orchestration.routing.llm_parser import LLMGoalParser, StructuredGoalModel
from enterprise_agent.orchestration.routing.parser import GoalParser, RequestParser, parser_trace
from enterprise_agent.orchestration.state import OrchestrationState, PlanHistoryState
from enterprise_agent.runtime.lease_heartbeat import LeaseHeartbeat
from enterprise_agent.runtime.lifecycle import ALLOWED_TRANSITIONS, InvalidTransition
from enterprise_agent.runtime.ownership import (
    LeaseGuard,
    LeaseLost,
    lease_scope,
    renew_interval_seconds,
)

logger = logging.getLogger(__name__)

_TERMINAL = frozenset(status for status, allowed in ALLOWED_TRANSITIONS.items() if not allowed)
_STARTABLE = frozenset({RunStatus.PENDING, RunStatus.WAITING_INPUT})
_COMPLETED = frozenset({RunStatus.SUCCEEDED, RunStatus.PARTIAL})
_STATE_KEYS = (
    "goal_spec",
    "inputs",
    "route",
    "plan",
    "plan_history",
    "task_results",
    "result",
    "missing_information",
    "replan_count",
)
_ERROR_LIMIT = 500


def execute_run(container: Container, run_id: str, worker_id: str) -> None:
    """Execute one leased run and persist a terminal or waiting status.

    Returns without writing when this worker no longer owns the lease.
    Raises LeaseLost when the fencing token is rejected, and raises when a
    failure outcome cannot be recorded so the caller can retry.
    """
    _RunExecution(container, run_id, worker_id).run()


class _RunExecution:
    def __init__(self, container: Container, run_id: str, worker_id: str) -> None:
        self.container = container
        self.run_id = run_id
        self.worker_id = worker_id
        self.tenant_id = ""
        self.input_text = ""
        self.inputs: list[dict[str, Any]] = []
        self.constraints: dict[str, Any] = {}
        self.model_profile: str | None = None
        self.reasoning_effort: str | None = None
        self.parser: RequestParser = GoalParser()
        self.draft_planner: LLMPlanner | None = None
        self.lease_token = ""
        self._seen_delegations: set[str] = set()

    def run(self) -> None:
        row = self.container.runs.get_run_unscoped(self.run_id)
        self.tenant_id = row.tenant_id
        lease = self.container.runs.current_lease(self.worker_id, self.run_id)
        if lease is None:
            return
        self.lease_token = lease.token
        guard = LeaseGuard()
        settings = self.container.settings
        heartbeat = LeaseHeartbeat(
            renew=lambda: self.container.runs.renew_lease(
                self.worker_id,
                self.run_id,
                self.lease_token,
                settings.lease_seconds,
            ),
            interval_seconds=renew_interval_seconds(
                settings.lease_seconds,
                settings.lease_renew_interval_seconds,
            ),
            guard=guard,
        )
        metrics = begin_run_metrics()
        try:
            with lease_scope(guard), heartbeat:
                self.container.runs.begin_execution(
                    self.worker_id,
                    self.run_id,
                    self.lease_token,
                    max_execution_attempts=settings.max_execution_attempts,
                )
                self._pin_config(row)
                self._execute(row)
        except LeaseLost:
            self.container.runs.note_lease_loss(
                self.worker_id,
                self.run_id,
                self.lease_token,
                max_lease_losses=settings.max_lease_losses,
            )
            raise
        finally:
            reset_run_metrics(metrics)

    def _execute(self, row: RunRow) -> None:
        current = RunStatus(row.status)
        if row.cancel_requested:
            self._cancel(current)
            return
        if current in _STARTABLE:
            self.container.runs.mark_running(
                self.tenant_id, self.run_id, self.worker_id, self.lease_token
            )
        try:
            final = self._invoke(row)
            self._persist(row, final)
        except LeaseLost:
            raise
        except Exception as exc:
            self._record_failure(exc)

    def _cancel(self, current: RunStatus) -> None:
        if current in _TERMINAL:
            return
        self._commit(
            RunOutcome(
                status=RunStatus.CANCELLED,
                state={},
                events=(
                    OutcomeRecord(EventType.RUN_CANCELLED, "lifecycle", "Worker 发现取消请求"),
                ),
            )
        )

    def _invoke(self, row: RunRow) -> Mapping[str, Any]:
        data = _state_data(row)
        # Keep the original request immutable. Supplemental answers are
        # durable input materials, not replacements for the request.
        self.input_text = _text(row.input_text, "input")
        self.inputs = _inputs(data.get("inputs", []))
        # Recover runs created before supplemental inputs became append-only.
        legacy_additional = _optional_text(data.get("additional_input"), "additional_input")
        if legacy_additional:
            self.inputs = _append_input(self.inputs, legacy_additional)
        self.constraints = _object(data.get("constraints", {}), "constraints")
        self.model_profile = self._resolve_model_profile(data.get("model_profile"))
        self.reasoning_effort = self._resolve_reasoning_effort(
            data.get("reasoning_effort"), self.model_profile
        )
        self._seen_delegations = {record.delegation_id for record in self.container.delegation.records}
        self.parser = _request_parser(
            self.container, self.model_profile, self.reasoning_effort
        )
        self.draft_planner = _request_planner(
            self.container, self.model_profile, self.reasoning_effort
        )
        deps = GraphDependencies(
            self.container.workers,
            self.container.policy,
            self.container.budget_limits,
            max_tasks=self.container.settings.max_tasks,
            max_replans=self.container.settings.max_replans,
            parser=self.parser,
            draft_planner=self.draft_planner,
            model_profile=self.model_profile,
            reasoning_effort=self.reasoning_effort,
            external_network_allowed=(
                self.container.settings.allow_external_network
                or self.container.settings.offline_mode
            ),
            network_mode=_network_mode(self.container.settings),
        )
        config = {
            "configurable": {"thread_id": self.run_id},
            "recursion_limit": self.container.settings.max_steps,
        }
        with tracing_scope(self.container.traces, run_id=self.run_id, tenant_id=self.tenant_id):
            with trace("orchestration", "chain", inputs={"input": self.input_text}) as span:
                with self.container.checkpoints.open() as saver:
                    graph = build_graph(deps, saver)
                    payload = self._graph_input(row, data)
                    final = graph.invoke(payload, config)
                if not isinstance(final, Mapping):
                    raise TypeError(
                        f"orchestration graph returned {type(final).__name__}, expected a mapping"
                    )
                span.outputs = _trace_outputs(final)
                return final

    def _graph_input(self, row: RunRow, data: Mapping[str, Any]) -> OrchestrationState:
        # WAITING_INPUT can represent either an active interrupt or a graph
        # that already returned WAITING_INPUT after a partial answer. Starting
        # from durable state handles both cases and never resumes a finished
        # graph with Command(resume=...).
        state: OrchestrationState = {
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "user_id": row.user_id,
            "input_text": self.input_text,
            "inputs": self.inputs,
            "constraints": self.constraints,
            "requested_role": _optional_text(data.get("requested_role"), "requested_role"),
            "model_profile": self.model_profile,
            "reasoning_effort": self.reasoning_effort,
            "external_network_allowed": (
                self.container.settings.allow_external_network
                or self.container.settings.offline_mode
            ),
            "task_results": _task_results(data.get("task_results", {})),
            "plan_history": _plan_history(data.get("plan_history", [])),
            "budget_used": _budget_used(row.budget_json),
            "replan_count": _integer(data.get("replan_count", 0), "replan_count"),
            "cancel_requested": bool(row.cancel_requested),
        }
        return state

    def _persist(self, row: RunRow, final: Mapping[str, Any]) -> None:
        if final.get("__interrupt__"):
            self._persist_interrupt(row, final)
            return
        status = RunStatus(final.get("status", RunStatus.FAILED))
        events: list[OutcomeRecord] = []
        plan = _optional_plan(final)
        if plan is not None:
            events.append(
                OutcomeRecord(
                    EventType.PLAN_CREATED,
                    "planning",
                    f"已创建计划 v{plan.plan_version}",
                    {"task_count": len(plan.tasks), "plan_version": plan.plan_version},
                )
            )
        events.extend(_route_event(final))
        events.extend(_task_events(final))
        artifacts = tuple(self.container.report_store.for_run(self.run_id))
        events.extend(
            OutcomeRecord(
                EventType.ARTIFACT_CREATED,
                "delivery",
                f"已生成产物 {artifact.name}",
                {"artifact_id": artifact.artifact_id},
            )
            for artifact in artifacts
        )
        delegations = tuple(
            record
            for record in self.container.delegation.records
            if record.delegation_id not in self._seen_delegations
        )
        events.extend(
            OutcomeRecord(
                EventType.DELEGATED,
                "delegation",
                f"已委派 {record.role} 子 Agent",
                {"delegation_id": record.delegation_id, "status": record.status},
            )
            for record in delegations
        )
        state = {key: final[key] for key in _STATE_KEYS if key in final}
        state["inputs"] = self.inputs
        state["additional_input"] = None
        state.update(self._observation(final))
        if status == RunStatus.WAITING_INPUT:
            completion = OutcomeRecord(EventType.INPUT_REQUESTED, "lifecycle", "等待用户补充信息")
        elif status in _COMPLETED:
            completion = OutcomeRecord(EventType.RUN_COMPLETED, "lifecycle", f"Run 结束：{status}")
        else:
            completion = OutcomeRecord(EventType.RUN_FAILED, "lifecycle", f"Run 结束：{status}")
        events.append(completion)
        self._commit(
            RunOutcome(
                status=status,
                state=state,
                route=_route_name(final),
                summary=final.get("summary"),
                budget=_budget(final, row),
                events=tuple(events),
                plan=plan,
                artifacts=artifacts,
                delegations=delegations,
            )
        )

    def _persist_interrupt(self, row: RunRow, final: Mapping[str, Any]) -> None:
        spec = _snapshot_parser(self.parser).parse(self.input_text, self.inputs, self.constraints)
        self._commit(
            RunOutcome(
                status=RunStatus.WAITING_INPUT,
                state={
                    "goal_spec": spec.model_dump(mode="json"),
                    "inputs": self.inputs,
                    "missing_information": spec.missing_information,
                    "additional_input": None,
                    **self._observation(final),
                },
                budget=_budget(final, row),
                events=(
                    OutcomeRecord(EventType.INPUT_REQUESTED, "lifecycle", "等待用户补充信息"),
                ),
            )
        )

    def _commit(self, outcome: RunOutcome) -> None:
        self.container.runs.commit_outcome(
            self.tenant_id,
            self.run_id,
            self.worker_id,
            self.lease_token,
            outcome,
        )

    def _record_failure(self, exc: Exception) -> None:
        logger.exception("Run %s failed during execution", self.run_id)
        message = f"{type(exc).__name__}: {exc}"
        if len(message) > _ERROR_LIMIT:
            message = message[: _ERROR_LIMIT - 3] + "..."
        try:
            self._commit(
                RunOutcome(
                    status=RunStatus.FAILED,
                    state={
                        "error": type(exc).__name__,
                        "error_message": message,
                        **self._observation({}),
                    },
                    summary="执行失败",
                    events=(
                        OutcomeRecord(EventType.RUN_FAILED, "lifecycle", "Worker 执行失败"),
                    ),
                )
            )
        except LeaseLost:
            raise
        except InvalidTransition:
            logger.warning("Run %s status no longer accepts FAILED: %s", self.run_id, exc)

    def _pin_config(self, row: RunRow) -> None:
        state = row.state_json if isinstance(row.state_json, dict) else {}
        if "config_snapshot" in state:
            return
        selected = state.get("model_profile")
        if not isinstance(selected, str) or not selected.strip():
            selected = None
        requested_effort = state.get("reasoning_effort")
        if not isinstance(requested_effort, str) or not requested_effort.strip():
            requested_effort = None
        self.container.runs.update_run(
            self.tenant_id,
            self.run_id,
            {
                "config_snapshot": self.container.config_snapshot_for(
                    selected, requested_effort
                )
            },
        )

    def _resolve_model_profile(self, requested: object) -> str | None:
        if isinstance(requested, str) and requested.strip():
            return requested.strip()
        if not self.container.settings.offline_mode:
            return None
        return self.container.models.offline_profile_name()

    def _resolve_reasoning_effort(
        self, requested: object, model_profile: str | None
    ) -> str | None:
        if not isinstance(requested, str) or not requested.strip() or not model_profile:
            return None
        effort = requested.strip()
        if effort not in self.container.models.supported_reasoning_efforts(model_profile):
            raise ValueError(
                f"reasoning effort {effort!r} is not supported by model profile "
                f"{model_profile!r}"
            )
        return effort

    def _observation(self, final: Mapping[str, Any]) -> dict[str, Any]:
        metrics = metrics_snapshot()
        log_orchestration(
            "metrics",
            run_id=self.run_id,
            model_calls=metrics["model_calls"],
            structured_output_failures=metrics["structured_output_failures"],
            fallbacks=metrics["fallbacks"],
            retries=metrics["retries"],
            input_tokens=metrics["input_tokens"],
            output_tokens=metrics["output_tokens"],
            latency_ms=metrics["latency_ms"],
            cost_usd=metrics["cost_usd"],
        )
        return {"metrics": metrics, "resolved_versions": _resolved_versions(final, self.draft_planner)}


def _request_parser(
    container: Container,
    model_profile: str | None = None,
    reasoning_effort: str | None = None,
) -> RequestParser:
    """Use the router model online. Offline runs and a missing model stay on rules."""
    if container.settings.offline_mode:
        return GoalParser()
    try:
        if reasoning_effort is not None:
            model = container.models.chat_model_for_role(
                "router", model_profile, reasoning_effort=reasoning_effort
            )
        elif model_profile is None:
            model = container.models.chat_model_for_role("router")
        else:
            model = container.models.chat_model_for_role("router", model_profile)
        profile = container.models.profile_for_role("router", model_profile)
        capabilities: set[str] = set()
        for role in container.policy.roles():
            capabilities.update(container.policy.get_role_policy(role).capabilities)
        return LLMGoalParser(
            StructuredGoalModel(model, prices=profile.parameters, offline=profile.provider == "offline"),
            capabilities,
            model_version=profile.model_name,
        )
    except Exception:
        logger.info("router model is unavailable; goal parsing stays on rules")
        return GoalParser()


def _request_planner(
    container: Container,
    model_profile: str | None = None,
    reasoning_effort: str | None = None,
) -> LLMPlanner | None:
    """Use the planner model online. Offline runs and a missing model stay on templates."""
    if container.settings.offline_mode:
        return None
    try:
        if reasoning_effort is not None:
            model = container.models.chat_model_for_role(
                "planner", model_profile, reasoning_effort=reasoning_effort
            )
        elif model_profile is None:
            model = container.models.chat_model_for_role("planner")
        else:
            model = container.models.chat_model_for_role("planner", model_profile)
        profile = container.models.profile_for_role("planner", model_profile)
        roles = container.workers.roles()
        capabilities: set[str] = set()
        for role in roles:
            capabilities.update(container.policy.get_role_policy(role).capabilities)
        return LLMPlanner(
            StructuredGoalModel(model, prices=profile.parameters, offline=profile.provider == "offline"),
            policy=container.policy,
            worker_roles=roles,
            capabilities=capabilities,
            max_tasks=container.settings.max_tasks,
            model_version=profile.model_name,
        )
    except Exception:
        logger.info("planner model is unavailable; planning stays on templates")
        return None


def _resolved_versions(final: Mapping[str, Any], planner: LLMPlanner | None) -> dict[str, Any]:
    parser = parser_trace()
    versions: dict[str, Any] = {
        "parser": parser.name,
        "parser_model": parser.model_version,
        "parser_prompt": parser.prompt_version,
    }
    if "plan" not in final:
        return versions
    if planner is None:
        versions["planner"] = "template"
        return versions
    trace = plan_trace()
    versions["planner"] = trace.name
    versions["planner_model"] = trace.model_version
    versions["planner_prompt"] = trace.prompt_version
    return versions


def _snapshot_parser(parser: RequestParser) -> GoalParser:
    fallback = getattr(parser, "fallback", None)
    if isinstance(fallback, GoalParser):
        return fallback
    if isinstance(parser, GoalParser):
        return parser
    return GoalParser()


def _network_mode(settings: Settings) -> str:
    if settings.offline_mode:
        return "offline_fixture"
    if settings.allow_external_network:
        return "external"
    return "disabled"


def _state_data(row: RunRow) -> dict[str, Any]:
    raw = row.state_json or {}
    if not isinstance(raw, Mapping):
        raise TypeError("run state_json must be a mapping")
    return dict(raw)


def _budget(final: Mapping[str, Any], row: RunRow) -> dict[str, Any] | None:
    raw = final["budget_used"] if "budget_used" in final else row.budget_json
    if raw is None:
        return None
    return dict(_mapping(raw, "budget_used"))


def _trace_outputs(final: Mapping[str, Any]) -> dict[str, Any]:
    if final.get("__interrupt__"):
        return {"status": RunStatus.WAITING_INPUT, "interrupted": True}
    return {
        "status": final.get("status"),
        "route": _route_name(final),
        "summary": final.get("summary"),
    }


def _optional_plan(final: Mapping[str, Any]) -> GlobalPlan | None:
    raw = final.get("plan")
    if not raw:
        return None
    return GlobalPlan.model_validate(raw)


def _route_event(final: Mapping[str, Any]) -> tuple[OutcomeRecord, ...]:
    route = _optional_mapping(final.get("route"), "route")
    if route is None:
        return ()
    return (
        OutcomeRecord(
            EventType.ROUTED,
            "routing",
            f"已选择 {route.get('route')}",
            {"route": route.get("route"), "rationale": route.get("rationale")},
        ),
    )


def _task_events(final: Mapping[str, Any]) -> tuple[OutcomeRecord, ...]:
    raw = final.get("task_results", {})
    if not raw:
        return ()
    results = _mapping(raw, "task_results")
    events: list[OutcomeRecord] = []
    for task_id, result in results.items():
        payload = _mapping(result, f"task_results[{task_id}]")
        events.append(
            OutcomeRecord(
                EventType.TASK_FINISHED,
                "execution",
                f"任务 {task_id} 已完成",
                {"status": payload.get("status"), "artifact_refs": payload.get("artifact_refs", [])},
            )
        )
    return tuple(events)


def _route_name(final: Mapping[str, Any]) -> str | None:
    route = _optional_mapping(final.get("route"), "route")
    if route is None:
        return None
    name = route.get("route")
    return name if isinstance(name, str) else None


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    return value


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    return _text(value, field)


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an integer")
    return value


def _object(value: Any, field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in _mapping(value, field).items():
        if not isinstance(key, str):
            raise TypeError(f"{field} keys must be strings")
        result[key] = item
    return result


def _inputs(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TypeError("inputs must be a list")
    return [_object(item, f"inputs[{index}]") for index, item in enumerate(value)]


def _append_input(inputs: list[dict[str, Any]], content: str) -> list[dict[str, Any]]:
    """Append one legacy answer without duplicating a retry."""
    if any(
        item.get("name") == "user_input" and item.get("content") == content
        for item in inputs
    ):
        return inputs
    return [*inputs, {"name": "user_input", "content": content}]


def _task_results(value: Any) -> dict[str, dict[str, Any]]:
    raw = _object(value, "task_results")
    return {key: _object(item, f"task_results[{key}]") for key, item in raw.items()}


def _plan_history(value: Any) -> list[PlanHistoryState]:
    if not isinstance(value, list):
        raise TypeError("plan_history must be a list")
    return [_history_entry(item, index) for index, item in enumerate(value)]


def _history_entry(value: Any, index: int) -> PlanHistoryState:
    field = f"plan_history[{index}]"
    raw = _object(value, field)
    reason = raw.get("reason")
    if not isinstance(reason, str):
        raise TypeError(f"{field}.reason must be a string")
    return {
        "version": _integer(raw.get("version"), f"{field}.version"),
        "reason": reason,
        "plan": _object(raw.get("plan"), f"{field}.plan"),
        "task_mapping": _string_mapping(raw.get("task_mapping", {}), f"{field}.task_mapping"),
        "task_actions": _string_mapping(raw.get("task_actions", {}), f"{field}.task_actions"),
        "retired_task_ids": _string_list(
            raw.get("retired_task_ids", []), f"{field}.retired_task_ids"
        ),
    }


def _budget_used(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    used: dict[str, int] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise TypeError("budget_used keys must be strings")
        used[key] = _integer(item, f"budget_used[{key}]")
    return used


def _optional_mapping(value: Any, field: str) -> Mapping[str, Any] | None:
    if not value:
        return None
    return _mapping(value, field)


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise TypeError(f"{field} must be a mapping")


def _string_mapping(value: Any, field: str) -> dict[str, str]:
    raw = _mapping(value, field)
    result: dict[str, str] = {}
    for key, item in raw.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise TypeError(f"{field} must contain string keys and values")
        result[key] = item
    return result


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"{field} must contain strings")
    return list(value)
