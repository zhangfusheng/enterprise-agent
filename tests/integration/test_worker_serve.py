from dotenv import load_dotenv
from sqlalchemy import select

from apps.worker.main import Worker
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate, RunStatus
from enterprise_agent.infrastructure.database.models import WorkItemRow


def _serve(container, monkeypatch, request: RunCreate) -> tuple[str, Worker]:
    load_dotenv()
    run_id = submit_run(
        container.runs,
        IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True),
        request,
    )
    monkeypatch.setattr("apps.worker.main.get_container", lambda: container)

    def stop_when_idle(_seconds: float) -> None:
        worker.stop_requested = True

    monkeypatch.setattr("apps.worker.main.time.sleep", stop_when_idle)
    worker = Worker()
    worker.serve()
    return run_id, worker


def _assert_finished(container, run_id: str, worker: Worker) -> None:
    with container.sessions() as session:
        item = session.scalar(select(WorkItemRow).where(WorkItemRow.run_id == run_id))
    assert item is not None
    assert item.status == "DONE"
    assert item.worker_id == worker.worker_id


def test_serve_executes_queued_run(container, monkeypatch):
    run_id, worker = _serve(container, monkeypatch, RunCreate(input="请直接回答什么是幂等性？"))
    view = container.runs.get_run_view("tenant-a", run_id)
    assert view.status == RunStatus.SUCCEEDED
    assert view.route == "direct"
    _assert_finished(container, run_id, worker)


def test_serve_executes_research_task(container, monkeypatch):
    run_id, worker = _serve(container, monkeypatch, RunCreate(input="请搜索网页并返回来源"))
    view = container.runs.get_run_view("tenant-a", run_id)
    state = container.runs.get_run_unscoped(run_id).state_json
    assert view.status == RunStatus.SUCCEEDED
    assert view.route == "single"
    assert state["task_results"]["task_worker"]["sources"]
    _assert_finished(container, run_id, worker)


def test_serve_executes_multi_agent(container, monkeypatch):
    run_id, worker = _serve(container, monkeypatch, RunCreate(input="请搜索网页并分析，生成报告"))
    view = container.runs.get_run_view("tenant-a", run_id)
    state = container.runs.get_run_unscoped(run_id).state_json
    assert view.status == RunStatus.SUCCEEDED
    assert view.route == "multi"
    assert state["task_results"]["task_research"]["sources"]
    assert state["task_results"]["task_analysis"]["artifact_refs"]
    artifacts = container.artifacts.list("tenant-a", run_id)
    assert len(artifacts) == 1
    assert artifacts[0].name == "report.md"
    _assert_finished(container, run_id, worker)
