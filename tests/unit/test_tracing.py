from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from enterprise_agent.agents.research.tools import ResearchLoopStopped
from enterprise_agent.application.execute_run import execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate, RunStatus
from enterprise_agent.infrastructure.observability.langchain_trace import LangChainTraceHandler
from enterprise_agent.infrastructure.observability.tracing import (
    build_trace_view,
    record_messages,
    trace,
    tracing_scope,
)


class _Sink:
    def __init__(self) -> None:
        self.spans = {}

    def record(self, span) -> None:
        self.spans[span.id] = span

    def ordered(self):
        return build_trace_view("run-1", list(self.spans.values()))


def test_child_runs_share_the_root_trace_and_sort_under_it():
    sink = _Sink()
    with tracing_scope(sink, run_id="run-1", tenant_id="tenant-a"):
        with trace("orchestration", "chain", inputs={"input": "hello"}) as root:
            with trace("parse_route", "chain") as child:
                child.outputs = {"route": "direct"}
            root.outputs = {"status": "SUCCEEDED"}
    view = sink.ordered()
    assert [item.name for item in view.runs] == ["orchestration", "parse_route"]
    assert view.roots[0].id == view.roots[0].trace_id
    assert view.roots[0].children[0].parent_run_id == view.roots[0].id
    assert view.roots[0].children[0].trace_id == view.roots[0].trace_id
    assert view.runs[0].outputs["status"] == "SUCCEEDED"
    assert view.runs[0].extra["metadata"]["run_id"] == "run-1"


def test_errors_interrupt_and_redaction():
    sink = _Sink()

    class GraphBubbleUp(Exception):
        pass

    class GraphInterrupt(GraphBubbleUp):
        pass

    with tracing_scope(sink, run_id="run-1", tenant_id="tenant-a"):
        try:
            with trace("tool", "tool", inputs={"token": "token=secret-value"}):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
    failed = sink.ordered().runs[0]
    assert failed.error is not None
    assert "RuntimeError" in failed.error
    assert "secret-value" not in str(failed.inputs)
    assert failed.end_time is not None

    sink = _Sink()
    with tracing_scope(sink, run_id="run-1", tenant_id="tenant-a"):
        try:
            with trace("parse_route", "chain"):
                raise GraphInterrupt()
        except GraphInterrupt:
            pass
    interrupted = sink.ordered().runs[0]
    assert interrupted.error is None
    assert interrupted.outputs["interrupted"] is True


def test_message_trace_nests_model_and_tool_calls():
    sink = _Sink()
    with tracing_scope(sink, run_id="run-1", tenant_id="tenant-a"):
        with trace("research", "chain", inputs={"prompt": "find python"}):
            record_messages(
                [
                    HumanMessage(content="find python"),
                    AIMessage(
                        content="",
                        tool_calls=[{"id": "call-1", "name": "web_search", "args": {"query": "python"}}],
                    ),
                    ToolMessage(
                        content='{"ok": false, "error": "search failed"}',
                        name="web_search",
                        tool_call_id="call-1",
                    ),
                ],
                start_time=datetime.now(UTC),
            )
    view = sink.ordered()
    assert [item.name for item in view.runs] == ["research", "chat_model", "web_search"]
    assert [item.run_type for item in view.runs] == ["chain", "llm", "tool"]
    tool = view.runs[2]
    assert tool.parent_run_id == view.runs[0].id
    assert tool.inputs["arguments"] == {"query": "python"}
    assert tool.error == "search failed"
    assert view.roots[0].children[0].name == "chat_model"
    assert view.roots[0].children[1].name == "web_search"


@pytest.mark.parametrize(
    "reason",
    [
        "evidence_sufficient",
        "search_budget_exhausted",
        "fetch_budget_exhausted",
        "tool_budget_exhausted",
        "duplicate_call_limit",
    ],
)
def test_expected_research_stop_is_not_recorded_as_trace_error(reason: str):
    sink = _Sink()
    with tracing_scope(sink, run_id="run-1", tenant_id="tenant-a"):
        with trace("research", "chain") as root:
            handler = LangChainTraceHandler(root)
            handler.on_chain_start(
                serialized={"name": "tools"},
                inputs={},
                run_id="chain-1",
            )
            handler.on_tool_start(
                serialized={"name": "web_search"},
                input_str="{}",
                inputs={},
                run_id="tool-1",
                parent_run_id="chain-1",
            )
            handler.on_tool_error(
                ResearchLoopStopped(reason),
                run_id="tool-1",
            )
            handler.on_chain_error(
                ResearchLoopStopped(reason),
                run_id="chain-1",
            )
    view = sink.ordered()
    assert [item.name for item in view.runs] == ["research", "tools", "web_search"]
    assert all(item.error is None for item in view.runs)


def _run(container, request: RunCreate) -> str:
    run_id = submit_run(
        container.runs,
        IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True),
        request,
    )
    assert container.runs.claim_work("worker-a", 30) == run_id
    execute_run(container, run_id, "worker-a")
    container.runs.finish_work("worker-a", run_id)
    return run_id


def test_direct_run_persists_a_queryable_trace(container):
    run_id = _run(container, RunCreate(input="请直接回答什么是幂等性？"))
    view = container.traces.get("tenant-a", run_id)
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.SUCCEEDED
    assert [item.name for item in view.runs] == ["orchestration", "parse_route", "direct", "accept"]
    assert view.roots[0].outputs["status"] == "SUCCEEDED"
    assert view.roots[0].outputs["route"] == "direct"
    assert view.roots[0].children[0].outputs["route"] == "direct"
    assert container.traces.get("tenant-b", run_id).runs == []


def test_tool_trace_is_stored_for_the_run(container):
    with tracing_scope(container.traces, run_id="run-tools", tenant_id="tenant-a"):
        with trace("research", "chain", inputs={"prompt": "find python"}):
            record_messages(
                [
                    AIMessage(
                        content="",
                        tool_calls=[{"id": "call-1", "name": "web_search", "args": {"query": "python"}}],
                    ),
                    ToolMessage(content='{"ok": true, "value": []}', name="web_search", tool_call_id="call-1"),
                ]
            )
    view = container.traces.get("tenant-a", "run-tools")
    assert [item.name for item in view.runs] == ["research", "chat_model", "web_search"]
    assert view.roots[0].children[1].name == "web_search"
    assert view.roots[0].children[1].parent_run_id == view.roots[0].id
    assert container.traces.get("tenant-b", "run-tools").runs == []
