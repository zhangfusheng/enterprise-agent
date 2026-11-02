import time
from datetime import UTC, datetime, timedelta

import pytest

from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.infrastructure.database.models import OperationRow
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.tools.contracts import ToolCall, ToolSpec, ToolStatus
from enterprise_agent.tools.gateway import ToolDenied, ToolGateway
from enterprise_agent.tools.idempotency import stable_operation_id
from enterprise_agent.tools.registry import ToolRegistry
from enterprise_agent.tools.save_report import ReportStore


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run-idem",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_id="agent-analysis",
        allowed_tools={"save_report"},
        workspace_root=".",
        metadata={"scopes": set(), "task_id": "task-1"},
    )


def _gateway(container, function, *, timeout: float = 2, has_side_effect: bool = True) -> ToolGateway:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="save_report",
            description="save",
            timeout_seconds=timeout,
            has_side_effect=has_side_effect,
            idempotent=True,
        ),
        function,
    )
    return ToolGateway(
        registry,
        BudgetLedger(BudgetLimits(model_calls=2, tool_calls=4, agents=1, steps=4, runtime_seconds=30)),
        CancellationToken(),
        {},
        operations=container.operations,
    )


def test_repeated_side_effect_keeps_one_artifact(container, tmp_path):
    calls: list[int] = []
    reports = ReportStore(tmp_path / "artifacts")

    def write(tenant_id: str, run_id: str, markdown: str, operation_id: str):
        calls.append(1)
        return reports.save(tenant_id, run_id, markdown, operation_id)

    gateway = _gateway(container, write)
    context = _context()
    arguments = {
        "tenant_id": "tenant-a",
        "run_id": "run-idem",
        "markdown": "same report",
    }
    first = gateway.call(context, ToolCall(name="save_report", arguments=arguments))
    second = gateway.call(context, ToolCall(name="save_report", arguments=arguments))

    assert first.status == ToolStatus.SUCCESS
    assert second.operation_id == first.operation_id
    assert calls == [1]
    assert len(reports.for_run("run-idem")) == 1


def test_timed_out_side_effect_is_not_retried(container):
    calls: list[int] = []

    def hang(operation_id: str) -> str:
        calls.append(1)
        time.sleep(0.4)
        return operation_id

    gateway = _gateway(container, hang, timeout=0.1)
    context = _context()
    first = gateway.call(context, ToolCall(name="save_report", arguments={}))
    second = gateway.call(context, ToolCall(name="save_report", arguments={}))

    assert first.status == ToolStatus.UNKNOWN
    assert second.status == ToolStatus.UNKNOWN
    assert calls == [1]


def test_interrupted_side_effect_stays_unknown(container):
    calls: list[int] = []
    arguments: dict = {}
    operation_id = stable_operation_id("run-idem", "save_report", arguments, "task-1")
    with container.sessions() as session:
        session.add(
            OperationRow(
                operation_id=operation_id,
                tenant_id="tenant-a",
                run_id="run-idem",
                task_id="task-1",
                tool_name="save_report",
                arguments_hash="0" * 64,
                has_side_effect=True,
                status="STARTED",
                result_json={},
                created_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    def write(operation_id: str) -> str:
        calls.append(1)
        return operation_id

    gateway = _gateway(container, write)
    result = gateway.call(_context(), ToolCall(name="save_report", arguments=arguments))

    assert result.status == ToolStatus.UNKNOWN
    assert calls == []


def test_web_tools_require_explicit_external_network_permission(container):
    registry = ToolRegistry()
    calls: list[int] = []

    def search(query: str, limit: int = 5):
        calls.append(1)
        return [{"title": query, "url": "https://example.com", "snippet": str(limit)}]

    registry.register(ToolSpec(name="web_search", description="search"), search)
    gateway = ToolGateway(
        registry,
        BudgetLedger(BudgetLimits(model_calls=2, tool_calls=4, agents=1, steps=4, runtime_seconds=30)),
        CancellationToken(),
        {},
        operations=container.operations,
    )
    context = _context().model_copy(
        update={
            "allowed_tools": {"web_search"},
            "metadata": {"scopes": set(), "external_network_allowed": False},
        }
    )

    with pytest.raises(ToolDenied, match="external network access is not enabled"):
        gateway.call(context, ToolCall(name="web_search", arguments={"query": "x"}))
    assert calls == []
