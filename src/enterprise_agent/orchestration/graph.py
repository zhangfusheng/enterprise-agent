from functools import wraps
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.contracts.plans import GlobalPlan, PlanHistoryEntry, TaskSpec, TaskStatus
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.contracts.runs import RunStatus
from enterprise_agent.governance.authorization import AuthorizationPolicy
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.infrastructure.observability.logging import log_orchestration
from enterprise_agent.infrastructure.observability.metrics import record_retry
from enterprise_agent.infrastructure.observability.tracing import trace
from enterprise_agent.orchestration.execution.direct import direct_answer
from enterprise_agent.orchestration.execution.readiness import ready_tasks
from enterprise_agent.orchestration.execution.workers import WorkerRegistry
from enterprise_agent.orchestration.planning.identity import (
    logical_task_key,
    reconcile_plan,
)
from enterprise_agent.orchestration.planning.llm_planner import LLMPlanner, plan_trace
from enterprise_agent.orchestration.planning.planner import GlobalPlanner
from enterprise_agent.orchestration.planning.validation import validate_plan
from enterprise_agent.orchestration.routing.parser import GoalParser, RequestParser, parser_trace
from enterprise_agent.orchestration.routing.router import Router
from enterprise_agent.orchestration.routing.validation import validate_route
from enterprise_agent.orchestration.state import OrchestrationState
from enterprise_agent.orchestration.supervision.aggregator import merge_results
from enterprise_agent.orchestration.supervision.recovery import RecoveryAction, decide_recovery
from enterprise_agent.orchestration.supervision.supervisor import Supervisor
from enterprise_agent.orchestration.supervision.verifier import verify
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.runtime.ownership import raise_if_lease_lost

_RETRYABLE = frozenset({TaskStatus.FAILED, TaskStatus.PARTIAL})


class GraphDependencies:
    """Collaborators for one orchestration graph.

    Add a role by registering a worker. This constructor does not grow per agent.
    """

    def __init__(
        self,
        workers: WorkerRegistry,
        policy: AuthorizationPolicy,
        limits: BudgetLimits,
        *,
        max_tasks: int,
        max_replans: int,
        parser: RequestParser | None = None,
        draft_planner: LLMPlanner | None = None,
        model_profile: str | None = None,
        reasoning_effort: str | None = None,
        external_network_allowed: bool = True,
        network_mode: str = "external",
    ) -> None:
        self.parser = parser or GoalParser()
        self.router = Router()
        self.planner = GlobalPlanner()
        self.draft_planner = draft_planner
        self.supervisor = Supervisor()
        self.workers = workers
        self.policy = policy
        self.limits = limits
        self.max_tasks = max_tasks
        self.max_replans = max_replans
        self.model_profile = model_profile
        self.reasoning_effort = reasoning_effort
        self.external_network_allowed = external_network_allowed
        self.network_mode = network_mode


def _traced(name: str):
    def decorate(method):
        @wraps(method)
        def wrapped(self, state):
            raise_if_lease_lost()
            with trace(name, "chain", inputs=_node_inputs(name, state)) as span:
                result = method(self, state)
                raise_if_lease_lost()
                span.outputs = _node_outputs(name, result)
                return result

        return wrapped

    return decorate


class _OrchestrationNodes:
    def __init__(self, deps: GraphDependencies) -> None:
        self.deps = deps

    @_traced("parse_route")
    def parse_route(self, state: OrchestrationState) -> dict:
        inputs = list(state.get("inputs", []))
        spec = self.deps.parser.parse(state["input_text"], inputs, state.get("constraints", {}))
        if spec.is_waiting_input:
            additional = interrupt({"type": "input_required", "missing": spec.missing_information})
            inputs.append({"name": "user_input", "content": str(additional)})
            spec = self.deps.parser.parse(state["input_text"], inputs, state.get("constraints", {}))
        if spec.is_waiting_input:
            self._log(state, "parse_route", **_parse_fields(spec, state))
            return {
                "goal_spec": spec.model_dump(mode="json"),
                "inputs": inputs,
                "missing_information": spec.missing_information,
                "status": RunStatus.WAITING_INPUT,
            }
        decision = self.deps.router.decide(spec, state.get("requested_role"))
        validate_route(decision, self.deps.policy)
        if decision.requires_external_operation and not _external_network_allowed(
            state, self.deps.external_network_allowed
        ):
            missing = ["需要启用外网搜索权限后才能执行该任务"]
            self._log(
                state,
                "parse_route",
                **_parse_fields(spec, state),
                route=decision.route,
                external_network_allowed=False,
                network_mode=self.deps.network_mode,
                missing_information=missing,
            )
            return {
                "goal_spec": spec.model_dump(mode="json"),
                "route": decision.model_dump(mode="json"),
                "inputs": inputs,
                "missing_information": missing,
                "status": RunStatus.WAITING_INPUT,
            }
        self._log(
            state,
            "parse_route",
            route=decision.route,
            rationale=decision.rationale,
            external_network_allowed=self.deps.external_network_allowed,
            network_mode=self.deps.network_mode,
            **_parse_fields(spec, state),
        )
        return {
            "goal_spec": spec.model_dump(mode="json"),
            "route": decision.model_dump(mode="json"),
            "inputs": inputs,
            "missing_information": spec.missing_information,
            "status": RunStatus.RUNNING,
        }

    @_traced("direct")
    def direct(self, state: OrchestrationState) -> dict:
        text = direct_answer(state["input_text"])
        result = AgentResult(status=ResultStatus.SUCCESS, summary=text)
        return {"summary": text, "result": result.model_dump(mode="json")}

    @_traced("plan")
    def plan(self, state: OrchestrationState) -> dict:
        spec = GoalSpec.model_validate(state["goal_spec"])
        route = RouteDecision.model_validate(state["route"])
        results = {
            key: AgentResult.model_validate(value)
            for key, value in state.get("task_results", {}).items()
        }
        previous = GlobalPlan.model_validate(state["plan"]) if state.get("plan") else None
        completed = {
            logical_task_key(task)
            for task in (previous.tasks if previous is not None else [])
            if task.task_id in results
            and decide_recovery(results[task.task_id]).action == RecoveryAction.PROCEED
        }
        history = [PlanHistoryEntry.model_validate(item) for item in state.get("plan_history", [])]
        unmet = _unmet(state.get("task_results", {}))
        created = self._create_plan(state, spec, route, len(history) + 1, completed, unmet)
        reconciliation = reconcile_plan(created, previous, results)
        global_plan = reconciliation.plan
        results = reconciliation.task_results
        validate_plan(global_plan, {goal.id for goal in spec.goals}, self.deps.policy, self.deps.max_tasks)
        entry = PlanHistoryEntry(
            version=global_plan.plan_version,
            reason="replan" if history else "initial",
            plan=global_plan,
            task_mapping=reconciliation.task_mapping,
            task_actions=reconciliation.task_actions,
            retired_task_ids=list(reconciliation.retired_task_ids),
        )
        trace = plan_trace() if self.deps.draft_planner is not None else None
        self._log(
            state,
            "plan",
            plan_version=global_plan.plan_version,
            task_count=len(global_plan.tasks),
            planner=trace.name if trace else "template",
            model_version=trace.model_version if trace else None,
            prompt_version=trace.prompt_version if trace else None,
            task_mapping=reconciliation.task_mapping,
            task_actions=reconciliation.task_actions,
            retired_task_ids=list(reconciliation.retired_task_ids),
        )
        return {
            "plan": global_plan.model_dump(mode="json"),
            "plan_history": [item.model_dump(mode="json") for item in (*history, entry)],
            "task_results": {
                key: value.model_dump(mode="json") for key, value in results.items()
            },
        }

    def _create_plan(
        self,
        state: OrchestrationState,
        spec: GoalSpec,
        route: RouteDecision,
        plan_version: int,
        completed: set[str],
        unmet: list[str],
    ) -> GlobalPlan:
        if self.deps.draft_planner is None:
            return self.deps.planner.create(state["run_id"], spec, route, plan_version, completed)
        return self.deps.draft_planner.create(
            state["run_id"],
            spec,
            route,
            plan_version,
            completed,
            limits=self.deps.limits,
            budget_used=dict(state.get("budget_used", {})),
            unmet=unmet,
        )

    @_traced("execute")
    def execute(self, state: OrchestrationState) -> dict:
        plan = GlobalPlan.model_validate(state["plan"])
        results = {
            key: AgentResult.model_validate(value)
            for key, value in state.get("task_results", {}).items()
        }
        ledger = BudgetLedger(self.deps.limits, state.get("budget_used", {}))
        cancellation = CancellationToken(bool(state.get("cancel_requested", False)))
        self.deps.workers.bind_run(ledger, cancellation)
        context = self._supervisor_context(state)
        delegate = bool(state.get("constraints", {}).get("request_delegation"))
        supervisor = self.deps.supervisor
        while ready := ready_tasks(plan):
            raise_if_lease_lost()
            for selected in ready:
                raise_if_lease_lost()
                cancellation.raise_if_cancelled()
                plan = supervisor.mark_started(plan, selected.task_id)
                task = _task(plan, selected.task_id)
                ledger.consume("agents")
                ledger.consume("steps")
                results[task.task_id] = self.deps.workers.execute(
                    task,
                    context,
                    results,
                    delegate=delegate,
                    agent_count=ledger.used.get("agents", 0),
                )
                raise_if_lease_lost()
                plan = supervisor.update_task(plan, task.task_id, results[task.task_id])
                finished = _task(plan, task.task_id)
                self._log(
                    state,
                    "execute",
                    task_id=finished.task_id,
                    task_status=finished.status,
                    attempts=finished.attempts,
                    result_status=results[task.task_id].status,
                    recovery_action=decide_recovery(results[task.task_id]).action,
                )
        plan = supervisor.skip_blocked(plan)
        for task in plan.tasks:
            if task.status == TaskStatus.SKIPPED:
                self._log(state, "execute", task_id=task.task_id, task_status=task.status, attempts=task.attempts)
        return {
            "plan": plan.model_dump(mode="json"),
            "task_results": {key: value.model_dump(mode="json") for key, value in results.items()},
            "budget_used": ledger.snapshot(),
        }

    @_traced("aggregate")
    def aggregate(self, state: OrchestrationState) -> dict:
        results = [
            AgentResult.model_validate(value) for value in state.get("task_results", {}).values()
        ]
        result = merge_results(results)
        return {"result": result.model_dump(mode="json"), "summary": result.summary}

    @_traced("replan")
    def replan(self, state: OrchestrationState) -> dict:
        count = state.get("replan_count", 0) + 1
        record_retry()
        self._log(state, "replan", replan_count=count)
        return {"replan_count": count, "status": RunStatus.RUNNING}

    @_traced("accept")
    def accept(self, state: OrchestrationState) -> dict:
        result = AgentResult.model_validate(state["result"])
        spec = GoalSpec.model_validate(state["goal_spec"])
        missing = verify(spec, result)
        unmet = list(result.unmet_requirements)
        for item in missing:
            if item not in unmet:
                unmet.append(item)
        status = _terminal_status(result.status, unmet)
        accepted = result.model_copy(update={"unmet_requirements": unmet})
        self._log(state, "accept", status=status)
        return {
            "result": accepted.model_dump(mode="json"),
            "summary": accepted.summary,
            "status": status,
            "missing_information": missing,
        }

    def route_after_parse(self, state: OrchestrationState) -> str:
        if state.get("missing_information"):
            return END
        route = state["route"]["route"]
        return "direct" if route == RouteKind.DIRECT else "plan"

    def route_after_aggregate(self, state: OrchestrationState) -> str:
        plan = GlobalPlan.model_validate(state["plan"])
        task_results = state.get("task_results", {})
        retryable = any(
            task.status in _RETRYABLE
            and decide_recovery(
                AgentResult.model_validate(task_results[task.task_id])
            ).action
            == RecoveryAction.RETRY
            for task in plan.tasks
            if task.task_id in task_results
        )
        if retryable and state.get("replan_count", 0) < self.deps.max_replans:
            return "replan"
        return "accept"

    def _supervisor_context(self, state: OrchestrationState) -> ExecutionContext:
        return ExecutionContext(
            run_id=state["run_id"],
            tenant_id=state["tenant_id"],
            user_id=state["user_id"],
            agent_id="agent_supervisor",
            workspace_root=".",
            allowed_roles=self.deps.policy.roles(),
            allowed_tools=self.deps.policy.tools(),
            metadata={
                "role": "research",
                "scopes": self.deps.workers.tool_scopes(),
                "external_network_allowed": self.deps.external_network_allowed,
                "network_mode": self.deps.network_mode,
                "model_profile": self.deps.model_profile,
                "reasoning_effort": self.deps.reasoning_effort,
            },
        )

    def _log(self, state: OrchestrationState, node: str, **fields: object) -> None:
        log_orchestration(node, run_id=state["run_id"], **fields)


def build_graph(deps: GraphDependencies, checkpointer=None):
    nodes = _OrchestrationNodes(deps)
    builder = StateGraph(OrchestrationState)
    builder.add_node("parse_route", nodes.parse_route)
    builder.add_node("direct", nodes.direct)
    builder.add_node("plan", nodes.plan)
    builder.add_node("execute", nodes.execute)
    builder.add_node("aggregate", nodes.aggregate)
    builder.add_node("replan", nodes.replan)
    builder.add_node("accept", nodes.accept)
    builder.add_edge(START, "parse_route")
    builder.add_conditional_edges(
        "parse_route",
        nodes.route_after_parse,
        {END: END, "direct": "direct", "plan": "plan"},
    )
    builder.add_edge("direct", "accept")
    builder.add_edge("plan", "execute")
    builder.add_edge("execute", "aggregate")
    builder.add_conditional_edges(
        "aggregate",
        nodes.route_after_aggregate,
        {"replan": "replan", "accept": "accept"},
    )
    builder.add_edge("replan", "plan")
    builder.add_edge("accept", END)
    return builder.compile(checkpointer=checkpointer)


def _unmet(results: dict[str, dict[str, Any]]) -> list[str]:
    found: list[str] = []
    for result in results.values():
        raw = result.get("unmet_requirements", [])
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str) and item not in found:
                found.append(item)
    return found


def _parse_fields(spec: GoalSpec, state: OrchestrationState) -> dict[str, object]:
    trace = parser_trace()
    return {
        "parser": trace.name,
        "model_version": trace.model_version,
        "prompt_version": trace.prompt_version,
        "goal_ids": _goal_ids(spec),
        "input_chars": len(state["input_text"]),
    }


def _goal_ids(spec: GoalSpec) -> list[str]:
    return [goal.id for goal in spec.goals]


def _external_network_allowed(state: OrchestrationState, system_allowed: bool) -> bool:
    """Apply the system permission and an optional per-request denial.

    A request can narrow access, but it can never grant network access that the
    process was not configured to provide.
    """
    constraints = state.get("constraints", {})
    if isinstance(constraints, dict) and constraints.get("allow_external_network") is False:
        # An explicit false is a durable caller restriction. The legacy empty
        # constraints object must not be treated as an explicit denial.
        if "allow_external_network" in constraints:
            return False
    return system_allowed


def _task(plan: GlobalPlan, task_id: str) -> TaskSpec:
    for task in plan.tasks:
        if task.task_id == task_id:
            return task
    raise KeyError(task_id)


def _terminal_status(status: ResultStatus, unmet: list[str]) -> RunStatus:
    if status == ResultStatus.FAILED:
        return RunStatus.FAILED
    if unmet or status == ResultStatus.PARTIAL:
        return RunStatus.PARTIAL
    return RunStatus.SUCCEEDED


def _node_inputs(name: str, state: OrchestrationState) -> dict[str, Any]:
    if name == "parse_route":
        return {"input": state.get("input_text", ""), "input_count": len(state.get("inputs") or [])}
    if name == "direct":
        return {"input": state.get("input_text", "")}
    if name == "plan":
        route = state.get("route") or {}
        return {"route": route.get("route") if isinstance(route, dict) else None}
    if name == "execute":
        plan = state.get("plan") or {}
        raw_tasks = plan.get("tasks") if isinstance(plan, dict) else []
        tasks = raw_tasks if isinstance(raw_tasks, list) else []
        return {
            "tasks": [
                task.get("task_id")
                for task in tasks
                if isinstance(task, dict) and isinstance(task.get("task_id"), str)
            ]
        }
    if name == "replan":
        return {"replan_count": state.get("replan_count", 0)}
    return {"run_id": state.get("run_id")}


def _node_outputs(name: str, result: object) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    if name == "parse_route":
        route = result.get("route") or {}
        return {
            "status": result.get("status"),
            "route": route.get("route") if isinstance(route, dict) else None,
            "missing_information": result.get("missing_information") or [],
        }
    if name == "plan":
        plan = result.get("plan") or {}
        raw_tasks = plan.get("tasks") if isinstance(plan, dict) else []
        tasks = raw_tasks if isinstance(raw_tasks, list) else []
        return {
            "plan_version": plan.get("plan_version") if isinstance(plan, dict) else None,
            "tasks": [
                {
                    "task_id": task.get("task_id"),
                    "task_key": task.get("task_key"),
                    "agent_role": task.get("agent_role"),
                }
                for task in tasks
                if isinstance(task, dict)
            ],
        }
    if name == "execute":
        raw = result.get("task_results") or {}
        if not isinstance(raw, dict):
            return {}
        return {
            "tasks": {
                key: value.get("status")
                for key, value in raw.items()
                if isinstance(value, dict)
            }
        }
    if name == "replan":
        return {"replan_count": result.get("replan_count"), "status": result.get("status")}
    summary = result.get("summary")
    nested = result.get("result")
    status = nested.get("status") if isinstance(nested, dict) else result.get("status")
    return {"status": status, "summary": summary}
