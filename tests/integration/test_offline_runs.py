from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate, RunStatus


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
    container.runs.update_run("tenant-a", run_id, {"additional_input": "这是补充资料", "input": "请分析我的资料"})
    container.runs.requeue_work(run_id)
    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    assert container.runs.get_run_view("tenant-a", run_id).status in {RunStatus.PARTIAL, RunStatus.SUCCEEDED}


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

