from apps.api.dependencies import container as container_dependency
from apps.api.dependencies import identity as identity_dependency
from apps.api.main import app
from fastapi.testclient import TestClient

from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.contracts.context import IdentityContext


def test_api_submit_query_events_and_artifacts(container):
    app.dependency_overrides[container_dependency] = lambda: container
    app.dependency_overrides[identity_dependency] = lambda: IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True)
    try:
        client = TestClient(app)
        response = client.post("/api/v1/runs", json={"input": "请搜索网页并分析，生成报告"})
        assert response.status_code == 202
        run_id = response.json()["run_id"]
        assert container.runs.claim_work("api-test-worker", 30) == run_id
        execute_run(container, run_id, "api-test-worker")
        container.runs.finish_work("api-test-worker", run_id)
        result = client.get(f"/api/v1/runs/{run_id}")
        assert result.status_code == 200
        assert result.json()["status"] == "SUCCEEDED"
        events = client.get(f"/api/v1/runs/{run_id}/events")
        assert events.status_code == 200
        assert "run.completed" in events.text
        artifacts = client.get(f"/api/v1/runs/{run_id}/artifacts")
        assert artifacts.status_code == 200
        assert artifacts.json()["artifacts"]
        trace = client.get(f"/api/v1/runs/{run_id}/trace")
        assert trace.status_code == 200
        body = trace.json()
        names = [item["name"] for item in body["runs"]]
        assert names[0] == "orchestration"
        assert "web_search" in names
        assert "save_report" in names
        assert body["roots"][0]["children"]
    finally:
        app.dependency_overrides.clear()


def test_api_model_picker_returns_efforts_and_persists_selection(container):
    container.settings.offline_mode = False
    app.dependency_overrides[container_dependency] = lambda: container
    app.dependency_overrides[identity_dependency] = lambda: IdentityContext(
        tenant_id="tenant-a", user_id="user-a", is_authenticated=True
    )
    try:
        client = TestClient(app)
        models = client.get("/api/v1/models")
        assert models.status_code == 200
        qwen = next(item for item in models.json()["items"] if item["profile"] == "qwen-default")
        assert qwen["reasoning_efforts"] == ["low", "medium", "high"]

        created = client.post(
            "/api/v1/runs",
            json={
                "input": "解释幂等性",
                "model_profile": "qwen-default",
                "reasoning_effort": "high",
            },
        )
        assert created.status_code == 202
        assert created.json()["reasoning_effort"] == "high"
        view = client.get(f"/api/v1/runs/{created.json()['run_id']}")
        assert view.json()["model_profile"] == "qwen-default"
        assert view.json()["reasoning_effort"] == "high"
    finally:
        app.dependency_overrides.clear()


def test_api_sessions_are_persistent_isolated_and_continue_on_same_session(container):
    app.dependency_overrides[container_dependency] = lambda: container
    app.dependency_overrides[identity_dependency] = lambda: IdentityContext(
        tenant_id="tenant-a", user_id="user-a", is_authenticated=True
    )
    try:
        client = TestClient(app)
        created = client.post("/api/v1/runs", json={"input": "解释幂等性"})
        assert created.status_code == 202
        first = created.json()
        session_id = first["session_id"]

        listed = client.get("/api/v1/sessions")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["session_id"] == session_id

        detail = client.get(f"/api/v1/sessions/{session_id}")
        assert detail.status_code == 200
        assert [item["role"] for item in detail.json()["messages"]] == ["user", "assistant"]

        continued = client.post(
            "/api/v1/runs",
            json={"input": "给一个 HTTP 例子", "message": "给一个 HTTP 例子", "session_id": session_id},
        )
        assert continued.status_code == 202
        assert continued.json()["session_id"] == session_id
        second = container.runs.get_run_unscoped(continued.json()["run_id"])
        assert "解释幂等性" in second.input_text
        assert "给一个 HTTP 例子" in second.input_text

        app.dependency_overrides[identity_dependency] = lambda: IdentityContext(
            tenant_id="tenant-b", user_id="user-b", is_authenticated=True
        )
        assert client.get(f"/api/v1/sessions/{session_id}").status_code == 404
        assert client.post(
            "/api/v1/runs", json={"input": "越权", "session_id": session_id}
        ).status_code == 404
    finally:
        app.dependency_overrides.clear()

