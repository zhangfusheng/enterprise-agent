from tests.support.parsers import TwoStageMultiGoalParser

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate, RunStatus
from enterprise_agent.contracts.state import STATE_SCHEMA_VERSION


def _run(container, request: RunCreate):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), request)
    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    container.runs.finish_work("worker-a", run_id)
    return run_id


def test_direct_offline_full_chain(container):
    run_id = _run(container, RunCreate(input="请直接回答什么是幂等性？"))
    view = container.runs.get_run_view("tenant-a", run_id)
    assert view.status == RunStatus.SUCCEEDED
    assert view.route == "direct"


def test_single_research_returns_fixture_source(container):
    run_id = _run(container, RunCreate(input="请搜索网页并返回来源"))
    state = container.runs.get_run_unscoped(run_id).state_json
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.SUCCEEDED
    assert state["task_results"]["task_worker"]["sources"]


def test_multi_research_analysis_creates_report(container):
    run_id = _run(container, RunCreate(input="请搜索网页并分析，生成报告"))
    view = container.runs.get_run_view("tenant-a", run_id)
    assert view.status == RunStatus.SUCCEEDED
    artifacts = container.artifacts.list("tenant-a", run_id)
    assert len(artifacts) == 1
    assert artifacts[0].name == "report.md"


def test_worker_delegates_child_and_records_result(container):
    run_id = _run(container, RunCreate(input="请搜索网页并分析，生成报告", constraints={"request_delegation": True}))
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.SUCCEEDED
    assert container.delegation.records
    assert container.delegation.records[0].depth == 1


def test_missing_input_is_checkpointed_and_resumed(container):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), RunCreate(input="请分析我的资料"))
    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.WAITING_INPUT
    waiting_state = container.runs.get_run_unscoped(run_id).state_json
    assert waiting_state["state_version"] == STATE_SCHEMA_VERSION
    assert waiting_state["goal_spec"]["goals"][0]["description"] == "请分析我的资料"
    assert waiting_state["goal_spec"]["missing_information"] == ["请提供要分析的资料或文件。"]
    assert waiting_state["goal_spec"]["input_requirements"] == [
        {
            "id": "requirement_caller_material",
            "kind": "material",
            "description": "请提供要分析的资料或文件。",
            "goal_ids": ["goal_main"],
            "required": True,
            "minimum_items": 1,
            "status": "missing",
            "satisfied_by": [],
            "source": "rules",
        }
    ]
    assert waiting_state["parse_provenance"] == {
        "parser": "rules",
        "model_version": None,
        "prompt_version": None,
        "compiler_version": "goal-compiler-v1",
        "attempts": 0,
        "fallback_reason": None,
    }
    assert waiting_state["resolved_versions"]["parser"] == "rules"
    assert waiting_state["resolved_versions"]["goal_compiler"] == "goal-compiler-v1"
    container.runs.update_run("tenant-a", run_id, {"additional_input": "这是补充资料", "input": "请分析我的资料"})
    container.runs.requeue_work(run_id)
    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    assert container.runs.get_run_view("tenant-a", run_id).status in {RunStatus.PARTIAL, RunStatus.SUCCEEDED}


def test_multi_goal_repeated_resume_is_stable_across_worker_graphs(container, monkeypatch):
    parser = TwoStageMultiGoalParser()
    monkeypatch.setattr(
        "enterprise_agent.application.execute_run._request_parser",
        lambda *_args, **_kwargs: parser,
    )
    original = "整理两段资料并计算指标"
    expected_goals = ["goal_prepare", "goal_calculate"]
    run_id = submit_run(
        container.runs,
        IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True),
        RunCreate(input=original),
    )

    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    first = container.runs.get_run_unscoped(run_id).state_json
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.WAITING_INPUT
    assert [item["id"] for item in first["goal_spec"]["goals"]] == expected_goals
    assert first["inputs"] == []
    assert "route" not in first

    container.runs.append_input("tenant-a", run_id, "第一段")
    container.runs.requeue_work(run_id, tenant_id="tenant-a")
    assert container.runs.claim_work("worker-b", 30) == run_id
    execute_run(container, run_id, "worker-b")
    second = container.runs.get_run_unscoped(run_id).state_json
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.WAITING_INPUT
    assert [item["id"] for item in second["goal_spec"]["goals"]] == expected_goals
    assert [item["content"] for item in second["inputs"]] == ["第一段"]

    container.runs.append_input("tenant-a", run_id, "第二段")
    container.runs.requeue_work(run_id, tenant_id="tenant-a")
    assert container.runs.claim_work("worker-c", 30) == run_id
    execute_run(container, run_id, "worker-c")
    final = container.runs.get_run_unscoped(run_id).state_json
    assert container.runs.get_run_view("tenant-a", run_id).status in {
        RunStatus.PARTIAL,
        RunStatus.SUCCEEDED,
    }
    assert container.runs.get_run_unscoped(run_id).input_text == original
    assert [item["id"] for item in final["goal_spec"]["goals"]] == expected_goals
    assert [item["content"] for item in final["inputs"]] == ["第一段", "第二段"]
    assert final["parse_provenance"]["compiler_version"] == "goal-compiler-v1"


def test_supplemental_inputs_are_append_only(container):
    original = "请分析我的资料"
    run_id = submit_run(
        container.runs,
        IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True),
        RunCreate(input=original),
    )
    container.runs.transition("tenant-a", run_id, RunStatus.RUNNING)
    container.runs.transition("tenant-a", run_id, RunStatus.WAITING_INPUT)

    container.runs.append_input("tenant-a", run_id, "第一段资料")
    container.runs.append_input("tenant-a", run_id, "第二段资料")
    state = container.runs.get_run_unscoped(run_id).state_json

    assert container.runs.get_run_unscoped(run_id).input_text == original
    assert [item["content"] for item in state["inputs"]] == ["第一段资料", "第二段资料"]


def test_persisted_cancel_prevents_execution(container):
    run_id = submit_run(container.runs, IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True), RunCreate(input="请直接回答一个问题"))
    container.runs.request_cancel("tenant-a", run_id)
    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.CANCELLED

