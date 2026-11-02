from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from apps.api.dependencies import container as container_dependency
from apps.api.dependencies import identity as identity_dependency
from apps.api.main import app
from fastapi import Header
from fastapi.testclient import TestClient

from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate
from enterprise_agent.infrastructure.database.models import Base, RunRow, TraceRunRow
from enterprise_agent.infrastructure.database.session import build_engine, session_factory
from enterprise_agent.infrastructure.observability.tracing import TraceSpan
from enterprise_agent.infrastructure.repositories.runs import RunRepository
from enterprise_agent.infrastructure.repositories.traces import TraceRepository

STAMP = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def trace_app(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path}/traces.sqlite")
    Base.metadata.create_all(engine)
    sessions = session_factory(engine)
    instance = SimpleNamespace(
        runs=RunRepository(sessions), traces=TraceRepository(sessions), sessions=sessions
    )

    def identity(x_tenant_id: str = Header("tenant-a")):
        return IdentityContext(tenant_id=x_tenant_id, user_id="tester", is_authenticated=True)

    app.dependency_overrides[container_dependency] = lambda: instance
    app.dependency_overrides[identity_dependency] = identity
    try:
        yield instance, TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def seed(
    instance,
    suffix,
    *,
    tenant="tenant-a",
    offset=0,
    status="SUCCEEDED",
    opened=False,
    error=None,
    tags=None,
):
    run_id = instance.runs.create(tenant, "tester", RunCreate(input="test"))
    with instance.sessions() as session:
        row = session.get(RunRow, run_id)
        row.status = status
        row.route = "direct"
        session.commit()
    span = TraceSpan(
        id=f"root-{suffix}",
        trace_id=f"root-{suffix}",
        parent_run_id=None,
        name=f"orchestration-{suffix}",
        run_type="chain",
        start_time=STAMP + timedelta(seconds=offset),
        end_time=None if opened else STAMP + timedelta(seconds=offset + 2),
        dotted_order=f"order-{suffix}",
        tenant_id=tenant,
        run_id=run_id,
        error=error,
        tags=tags or [],
    )
    instance.traces.record(span)
    return run_id, span


def child(instance, root, *, error=None, replay=False):
    instance.traces.record(
        TraceSpan(
            id=f"child-{root.id}",
            trace_id=root.trace_id,
            parent_run_id=root.id,
            name="web_search",
            run_type="tool",
            start_time=root.start_time,
            end_time=root.end_time,
            dotted_order=f"{root.dotted_order}.child",
            tenant_id=root.tenant_id,
            run_id=root.run_id,
            error=error,
            inputs={"tool_call_id": "call-1"} if replay else {},
        )
    )


def test_list_filters_counts_pagination_and_tenant_scope(trace_app):
    instance, client = trace_app
    run_a, root_a = seed(instance, "a", tags=["prod", "production"])
    child(instance, root_a, error="lookup failed")
    seed(instance, "b", tags=["production"])
    seed(instance, "foreign", tenant="tenant-b", offset=10)
    first = client.get("/api/v1/traces", params={"limit": 1}).json()
    assert first["items"][0]["trace_id"] == "root-b"
    assert first["next_cursor"]
    # A new trace cannot push an already-read page into the next keyset page.
    seed(instance, "new", offset=20)
    second = client.get(
        "/api/v1/traces", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    item = second["items"][0]
    assert item["run_id"] == run_a
    assert second["next_cursor"] is None
    assert item["span_count"] == 2 and item["error_count"] == 1
    assert item["has_error"] is True and item["status"] == "success"
    assert item["duration_ms"] == 2000 and item["start_time"].endswith("Z")
    assert len(client.get("/api/v1/traces", params={"has_error": "true"}).json()["items"]) == 1
    assert len(client.get("/api/v1/traces", params={"tag": "prod"}).json()["items"]) == 1
    assert len(client.get("/api/v1/traces", params={"q": run_a}).json()["items"]) == 1
    assert client.get("/api/v1/traces", params={"q": "%"}).json()["items"] == []
    assert (
        len(
            client.get(
                "/api/v1/traces", params={"start_time": (STAMP + timedelta(seconds=1)).isoformat()}
            ).json()["items"]
        )
        == 1
    )
    assert (
        len(
            client.get(
                "/api/v1/traces", params={"end_time": STAMP.isoformat(), "name": "orchestration-a"}
            ).json()["items"]
        )
        == 1
    )
    foreign = client.get("/api/v1/traces", headers={"X-Tenant-Id": "tenant-b"}).json()
    assert [row["trace_id"] for row in foreign["items"]] == ["root-foreign"]


def test_detail_export_summary_redaction_and_isolation(trace_app):
    instance, client = trace_app
    run_id, root = seed(instance, "detail")
    child(instance, root, error="token=hidden", replay=True)
    # Simulate historical data predating key-based redaction.
    with instance.sessions() as session:
        row = session.get(TraceRunRow, root.id)
        row.inputs_json = {"api_key": "private-key", "nested": {"authorization": "Bearer hidden"}}
        session.commit()
    path = f"/api/v1/runs/{run_id}/trace"
    response = client.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["run_status"] == "SUCCEEDED"
    assert body["roots"][0]["has_error"] is True
    assert body["runs"][1]["depth"] == 1
    assert body["runs"][1]["timing_source"] == "reconstructed"
    assert body["runs"][1]["duration_ms"] is None
    assert "private-key" not in response.text and "Bearer hidden" not in response.text
    assert "token=hidden" not in response.text
    exported = client.get(f"{path}/export")
    assert exported.json() == body
    assert "attachment" in exported.headers["content-disposition"]
    summary_path = f"/api/v1/traces/{root.trace_id}/summary"
    assert client.get(summary_path).json()["span_count"] == 2
    for url in [path, f"{path}/export", summary_path]:
        assert client.get(url, headers={"X-Tenant-Id": "tenant-b"}).status_code == 404
    assert client.get("/api/v1/traces/missing/summary").status_code == 404


def test_running_interrupted_incomplete_and_multiple_roots(trace_app):
    instance, client = trace_app
    live_id, root = seed(instance, "live", opened=True, status="RUNNING")
    stopped_id, _ = seed(instance, "stopped", opened=True, status="FAILED")
    _, interrupted = seed(instance, "interrupted", status="WAITING_INPUT")
    interrupted.outputs = {"interrupted": True}
    instance.traces.record(interrupted)
    resumed = TraceSpan(
        id="root-resumed",
        trace_id="root-resumed",
        parent_run_id=None,
        name="orchestration",
        run_type="chain",
        start_time=STAMP,
        dotted_order="resumed",
        run_id=live_id,
        tenant_id="tenant-a",
    )
    instance.traces.record(resumed)
    body = client.get(f"/api/v1/runs/{live_id}/trace").json()
    assert len(body["roots"]) == 2
    assert all(span["status"] == "running" for span in body["runs"])
    assert (
        client.get(f"/api/v1/runs/{stopped_id}/trace").json()["runs"][0]["status"] == "incomplete"
    )
    for status, expected in [("running", 2), ("incomplete", 1), ("interrupted", 1)]:
        response = client.get("/api/v1/traces", params={"status": status}).json()
        assert len(response["items"]) == expected
        assert all(item["status"] == status for item in response["items"])
    assert client.get(f"/api/v1/traces/{root.id}/summary").json()["duration_ms"] is None


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"cursor": "not a cursor"},
        {"cursor": "e30="},
        {"status": "made-up"},
        {"start_time": "bad"},
        {"start_time": "2026-02-01T00:00:00Z", "end_time": "2026-01-01T00:00:00Z"},
    ],
)
def test_invalid_queries_are_rejected(trace_app, params):
    _, client = trace_app
    assert client.get("/api/v1/traces", params=params).status_code == 422
