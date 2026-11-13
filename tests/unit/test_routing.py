import pytest

from enterprise_agent.contracts.goals import GoalSpec
from enterprise_agent.contracts.routing import RouteDecision, RouteKind
from enterprise_agent.governance.authorization import (
    AuthorizationError,
    AuthorizationPolicy,
    RolePolicy,
)
from enterprise_agent.orchestration.routing.parser import GoalParser
from enterprise_agent.orchestration.routing.router import Router
from enterprise_agent.orchestration.routing.spec_validation import validate_goal_spec
from enterprise_agent.orchestration.routing.validation import validate_route


def _policy() -> AuthorizationPolicy:
    return AuthorizationPolicy([
        RolePolicy(
            "research",
            frozenset({"web_search", "web_fetch", "workspace_read"}),
            frozenset({"web_search", "web_fetch", "workspace_read"}),
            frozenset({"analysis"}),
        ),
        RolePolicy(
            "analysis",
            frozenset({"analysis", "calculate", "save_report"}),
            frozenset({"calculate", "save_report"}),
            frozenset(),
        ),
    ])


def test_direct_route_has_no_external_capability():
    spec = GoalParser().parse("请解释幂等性")
    decision = Router().decide(spec)
    assert decision.route == RouteKind.DIRECT
    validate_route(decision, _policy())


def test_network_permission_does_not_create_search_demand():
    spec = GoalParser().parse("请解释幂等性", constraints={"allow_external_network": True})
    assert spec.constraints.allow_external_network is True
    assert spec.goals[0].required_capabilities == []
    decision = Router().decide(spec)
    assert decision.route == RouteKind.DIRECT
    assert decision.requires_external_operation is False


def test_single_research_route_is_capability_driven():
    spec = GoalParser().parse("请搜索网页并返回来源")
    decision = Router().decide(spec)
    assert decision.route == RouteKind.SINGLE
    assert decision.selected_role == "research"
    validate_route(decision, _policy())


def test_analysis_does_not_need_workspace_read_to_accept_supplied_material():
    spec = GoalParser().parse(
        "请分析我的资料",
        inputs=[{"name": "user_input", "content": "附件正文"}],
    )
    decision = Router().decide(spec)
    assert decision.route == RouteKind.SINGLE
    assert decision.selected_role == "analysis"
    assert decision.required_capabilities == ["analysis"]
    validate_route(decision, _policy())


def test_analysis_and_calculate_stay_on_one_worker():
    spec = GoalParser().parse("请计算增长率并写出报告")
    decision = Router().decide(spec)
    assert decision.route == RouteKind.SINGLE
    assert decision.selected_role == "analysis"
    assert decision.requires_external_operation is False
    validate_route(decision, _policy())


def test_search_plus_calculate_splits_across_roles():
    spec = GoalParser().parse("请搜索网页并计算增长率")
    decision = Router().decide(spec)
    assert decision.route == RouteKind.MULTI
    assert decision.selected_role is None
    assert set(decision.required_capabilities) == {"web_search", "calculate"}
    validate_route(decision, _policy())


def test_multi_route_is_external_evidence_plus_analysis():
    spec = GoalParser().parse("请搜索网页并比较方案，生成报告")
    decision = Router().decide(spec)
    assert decision.route == RouteKind.MULTI
    validate_route(decision, _policy())


def test_requested_role_cannot_grant_missing_capabilities():
    spec = GoalParser().parse("请计算两个方案的增长率")
    decision = Router().decide(spec, "research")
    assert decision.selected_role == "research"
    with pytest.raises(AuthorizationError, match="lacks capabilities"):
        validate_route(decision, _policy())


def test_requested_role_does_not_turn_a_direct_answer_into_worker_work():
    spec = GoalParser().parse("请比较两个已知方案的设计取舍")
    decision = Router().decide(spec, "research")
    assert decision.route == RouteKind.DIRECT
    assert decision.selected_role is None


def test_inline_markdown_deliverable_can_be_direct():
    spec = GoalParser().parse("将这段文字整理为 Markdown")
    decision = Router().decide(spec)
    assert spec.deliverables[0].delivery_mode == "inline"
    assert decision.route == RouteKind.DIRECT


def test_saved_report_requires_artifact_capability():
    spec = GoalParser().parse("保存一份 Markdown 报告")
    decision = Router().decide(spec)
    assert spec.deliverables[0].delivery_mode == "artifact"
    assert decision.route == RouteKind.SINGLE
    assert decision.required_capabilities == ["save_report"]


@pytest.mark.parametrize(
    "text",
    [
        "什么是幂等性",
        "详细解释一下 Raft 算法",
        "分析为什么 Redis 使用单线程模型",
        "总结下面直接粘贴的文字：系统需要保持幂等",
    ],
)
def test_self_contained_language_tasks_are_direct(text: str):
    decision = Router().decide(GoalParser().parse(text))
    assert decision.route == RouteKind.DIRECT


def test_uploaded_csv_analysis_uses_analysis_worker():
    spec = GoalParser().parse(
        "分析上传的 CSV 数据",
        inputs=[{"name": "sales.csv", "content": "amount\n10"}],
    )
    decision = Router().decide(spec)
    assert decision.route == RouteKind.SINGLE
    assert decision.required_capabilities == ["analysis"]


def test_goal_spec_validator_normalizes_legacy_path_to_artifact():
    spec = GoalParser().parse("生成 Markdown 报告")
    spec.deliverables[0].path = "report.md"
    normalized = validate_goal_spec(spec, _policy())
    assert normalized.deliverables[0].delivery_mode == "artifact"
    decision = Router(_policy()).decide(normalized)
    assert decision.required_capabilities == ["save_report"]


def test_unknown_capability_is_rejected():
    decision = RouteDecision(
        route=RouteKind.SINGLE,
        required_capabilities=["shell"],
        selected_role="analysis",
        rationale="test",
    )
    with pytest.raises(ValueError, match="unsupported capabilities"):
        validate_route(decision, _policy())


def test_direct_route_rejects_a_selected_role():
    decision = RouteDecision(route=RouteKind.DIRECT, selected_role="analysis", rationale="test")
    with pytest.raises(ValueError, match="direct route"):
        validate_route(decision, _policy())


def test_multi_route_rejects_a_single_selected_role():
    decision = RouteDecision(
        route=RouteKind.MULTI,
        required_capabilities=["web_search", "analysis"],
        selected_role="research",
        rationale="test",
        requires_external_operation=True,
        requires_realtime_information=True,
    )
    with pytest.raises(ValueError, match="single role"):
        validate_route(decision, _policy())


def test_waiting_input_is_not_a_route():
    spec = GoalParser().parse("请分析我的资料")
    with pytest.raises(ValueError, match="waiting input"):
        Router().decide(spec)


def test_parser_marks_missing_user_material():
    spec = GoalParser().parse("请分析我的资料")
    assert spec.is_waiting_input
    assert spec.missing_information


def test_goal_spec_requires_a_goal_or_explicit_clarification():
    with pytest.raises(ValueError, match="goal or an explicit clarification"):
        validate_goal_spec(GoalSpec(), _policy())

    clarification = GoalSpec(missing_information=["请提供至少一个可执行目标。"])
    assert validate_goal_spec(clarification, _policy()) == clarification


def test_rule_parser_keeps_a_single_goal():
    spec = GoalParser().parse("先搜索网页，再计算增长率，并生成报告")
    assert [goal.id for goal in spec.goals] == ["goal_main"]

