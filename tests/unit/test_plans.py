import pytest

from enterprise_agent.contracts.plans import GlobalPlan, TaskSpec
from enterprise_agent.governance.authorization import AuthorizationPolicy, RolePolicy
from enterprise_agent.orchestration.planning.validation import PlanValidationError, validate_plan


@pytest.fixture
def policy():
    return AuthorizationPolicy([RolePolicy("research", frozenset({"web_search"}), frozenset({"web_search"}), frozenset()), RolePolicy("analysis", frozenset({"analysis"}), frozenset({"calculate"}), frozenset())])


def test_plan_rejects_cycle(policy):
    plan = GlobalPlan(plan_version=1, task_id="r", created_from_route="multi", tasks=[TaskSpec(task_id="task_a", goal_ids=["goal_main"], agent_role="research", task_description="task a", expected_output="x", depends_on=["task_b"]), TaskSpec(task_id="task_b", goal_ids=["goal_main"], agent_role="analysis", task_description="task b", expected_output="x", depends_on=["task_a"])])
    with pytest.raises(PlanValidationError, match="cycle"):
        validate_plan(plan, {"goal_main"}, policy, 5)


def test_plan_rejects_unknown_result_reference(policy):
    plan = GlobalPlan(plan_version=1, task_id="r", created_from_route="multi", tasks=[TaskSpec(task_id="task_a", goal_ids=["goal_main"], agent_role="analysis", task_description="task a", expected_output="x", inputs=["result:missing"])])
    with pytest.raises(PlanValidationError, match="unknown result"):
        validate_plan(plan, {"goal_main"}, policy, 5)

