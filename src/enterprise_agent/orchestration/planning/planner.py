from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.contracts.plans import GlobalPlan, TaskSpec, TaskStatus
from enterprise_agent.contracts.routing import RouteDecision, RouteKind

_DEFAULT_ROLE = "analysis"
_WORKER_OUTPUT = "AgentResult with summary and evidence"
_RESEARCH_LEAD = "为以下目标收集可核验网页证据："
_RESEARCH_OUTPUT = "带来源的 Research AgentResult"
_RESEARCH_ACCEPTANCE = ("至少返回来源或明确说明搜索不可用",)
_ANALYSIS_LEAD = "基于研究结果分析并交付："
_ANALYSIS_OUTPUT = "带结论和来源的 Markdown 报告"
_RESULT_PREFIX = "result:"


class PlanningError(ValueError):
    """A route cannot be turned into an executable plan."""


@dataclass(frozen=True, slots=True)
class PlanRequest:
    """Facts derived once from a goal and route, then reused by every strategy."""

    run_id: str
    spec: GoalSpec
    route: RouteDecision
    plan_version: int
    completed: frozenset[str]

    def __post_init__(self) -> None:
        if not self.spec.goals:
            raise PlanningError("plan requires at least one goal")
        if self.plan_version < 1:
            raise PlanningError("plan version must be at least 1")

    @property
    def goal_ids(self) -> list[str]:
        return [goal.id for goal in self.spec.goals]

    @property
    def objective(self) -> str:
        return self.spec.goals[0].description

    @property
    def acceptance(self) -> list[str]:
        return [item.description for item in self.spec.acceptance_criteria]


@dataclass(frozen=True, slots=True)
class TaskBlueprint:
    """Declarative task. Role, inputs, and criteria are resolved when a request arrives.

    ``role_from_route`` uses ``RouteDecision.selected_role``, falling back to
    ``default_role``. Otherwise ``role`` is required. A lead is prefixed to the
    primary goal; user text is not interpreted as a format string. Acceptance
    criteria are taken from the goal spec when omitted.
    """

    task_id: str
    expected_output: str
    role: str | None = None
    role_from_route: bool = False
    default_role: str = _DEFAULT_ROLE
    lead: str = ""
    inputs: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    acceptance_criteria: tuple[str, ...] | None = None
    include_spec_inputs: bool = False
    task_key: str | None = None

    def materialize(self, request: PlanRequest) -> TaskSpec:
        criteria = (
            list(self.acceptance_criteria)
            if self.acceptance_criteria is not None
            else request.acceptance
        )
        inputs = [item.name for item in request.spec.inputs] if self.include_spec_inputs else []
        inputs.extend(self.inputs)
        status = (
            TaskStatus.SUCCEEDED
            if self.task_id in request.completed
            or (self.task_key is not None and self.task_key in request.completed)
            else TaskStatus.PENDING
        )
        return TaskSpec(
            task_id=self.task_id,
            task_key=self.task_key or self.task_id,
            goal_ids=request.goal_ids,
            agent_role=self._role(request),
            task_description=f"{self.lead}{request.objective}",
            inputs=inputs,
            depends_on=list(self.depends_on),
            expected_output=self.expected_output,
            acceptance_criteria=criteria,
            status=status,
        )

    def _role(self, request: PlanRequest) -> str:
        chosen: str | None
        if self.role_from_route:
            chosen = request.route.selected_role or self.default_role
        else:
            chosen = self.role
        if not chosen:
            raise PlanningError(f"task {self.task_id} has no agent role")
        return chosen


class PlanStrategy(Protocol):
    """Builds the tasks for one route. Register an implementation to add a route."""

    route: RouteKind

    def build(self, request: PlanRequest) -> list[TaskSpec]: ...


class PipelineStrategy:
    """Ordered blueprints for one route. Dependencies stay on the blueprints."""

    def __init__(self, route: RouteKind, blueprints: Sequence[TaskBlueprint]) -> None:
        steps = tuple(blueprints)
        if not steps:
            raise PlanningError(f"plan strategy {route} has no tasks")
        ids = [step.task_id for step in steps]
        if len(ids) != len(set(ids)):
            raise PlanningError("task ids must be unique")
        known = set(ids)
        for step in steps:
            if not step.role_from_route and not step.role:
                raise PlanningError(f"task {step.task_id} has no agent role")
            if any(dep not in known for dep in step.depends_on):
                raise PlanningError(f"task {step.task_id} references unknown dependency")
            if any(_unknown_result(ref, known) for ref in step.inputs):
                raise PlanningError(f"task {step.task_id} references unknown result")
        self.route = route
        self._blueprints = steps

    def build(self, request: PlanRequest) -> list[TaskSpec]:
        return [step.materialize(request) for step in self._blueprints]


def _builtin_strategies() -> tuple[PlanStrategy, ...]:
    return (
        PipelineStrategy(
            RouteKind.SINGLE,
            (
                TaskBlueprint(
                    task_id="task_worker",
                    task_key="worker",
                    expected_output=_WORKER_OUTPUT,
                    role_from_route=True,
                    include_spec_inputs=True,
                ),
            ),
        ),
        PipelineStrategy(
            RouteKind.MULTI,
            (
                TaskBlueprint(
                    task_id="task_research",
                    task_key="research",
                    role="research",
                    lead=_RESEARCH_LEAD,
                    expected_output=_RESEARCH_OUTPUT,
                    acceptance_criteria=_RESEARCH_ACCEPTANCE,
                ),
                TaskBlueprint(
                    task_id="task_analysis",
                    task_key="analysis",
                    role="analysis",
                    lead=_ANALYSIS_LEAD,
                    expected_output=_ANALYSIS_OUTPUT,
                    inputs=("result:task_research",),
                    depends_on=("task_research",),
                ),
            ),
        ),
    )


class GlobalPlanner:
    """Dispatch a route to a registered strategy.

    Built-in strategies cover a single worker and a research-then-analysis pipeline.
    Add a route by registering a PlanStrategy; this class does not grow per route.
    """

    def __init__(self, strategies: Sequence[PlanStrategy] = ()) -> None:
        self._strategies: dict[RouteKind, PlanStrategy] = {}
        for strategy in (*_builtin_strategies(), *strategies):
            self.register(strategy)

    def register(self, strategy: PlanStrategy) -> None:
        if strategy.route in self._strategies:
            raise PlanningError(f"duplicate plan strategy: {strategy.route}")
        self._strategies[strategy.route] = strategy

    def create(
        self,
        task_id: str,
        spec: GoalSpec,
        route: RouteDecision,
        plan_version: int = 1,
        completed: set[str] | None = None,
    ) -> GlobalPlan:
        request = PlanRequest(
            run_id=task_id,
            spec=spec,
            route=route,
            plan_version=plan_version,
            completed=frozenset(completed or ()),
        )
        return GlobalPlan(
            plan_version=request.plan_version,
            task_id=request.run_id,
            tasks=self._tasks(request),
            created_from_route=request.route.route,
        )

    def _tasks(self, request: PlanRequest) -> list[TaskSpec]:
        strategy = self._strategies.get(request.route.route)
        if strategy is None:
            raise PlanningError(f"no plan strategy for route: {request.route.route}")
        tasks = strategy.build(request)
        if not tasks:
            raise PlanningError(f"plan strategy {request.route.route} produced no tasks")
        ids = [task.task_id for task in tasks]
        if len(ids) != len(set(ids)):
            raise PlanningError("task ids must be unique")
        return tasks


def carry_attempts(plan: GlobalPlan, previous: GlobalPlan | None) -> GlobalPlan:
    """Keep attempt counts when a template is rebuilt for logical tasks."""
    if previous is None:
        return plan
    from enterprise_agent.orchestration.planning.identity import task_matches

    matches = task_matches(plan.tasks, previous.tasks)
    prior = {task.task_id: task.attempts for task in previous.tasks}
    tasks = [
        task.model_copy(
            update={
                "attempts": prior.get(matches.get(task.task_id, task.task_id), task.attempts)
            }
        )
        for task in plan.tasks
    ]
    return plan.model_copy(update={"tasks": tasks})


def _unknown_result(ref: str, known: set[str]) -> bool:
    return ref.startswith(_RESULT_PREFIX) and ref.removeprefix(_RESULT_PREFIX) not in known
