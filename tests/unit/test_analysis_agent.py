import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from tests.support.harness import offline_harness
from tests.support.scripted import ScriptedChatModel, tool_call

from enterprise_agent.agents.analysis.definition import AnalysisAgent
from enterprise_agent.agents.analysis.schemas import AnalysisRequest
from enterprise_agent.agents.analysis.tools import RunControl, bind_analysis_tools
from enterprise_agent.agents.harness.deepagents import DeepAgentsHarness
from enterprise_agent.agents.harness.roles import ANALYSIS
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import ResultStatus
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.runtime.budgets import BudgetExceeded, BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationRequested, CancellationToken
from enterprise_agent.tools.calculate import calculate
from enterprise_agent.tools.contracts import ToolCall, ToolResult, ToolSpec, ToolStatus
from enterprise_agent.tools.gateway import ToolDenied, ToolGateway
from enterprise_agent.tools.registry import ToolRegistry
from enterprise_agent.tools.save_report import ReportStore

_FORBIDDEN = {
    "ls",
    "read_file",
    "write_file",
    "edit_file",
    "delete",
    "glob",
    "grep",
    "execute",
    "task",
    "write_todos",
    "workspace_read",
}


def _agent(*args, **kwargs) -> AnalysisAgent:
    steps = kwargs.pop("steps", None)
    if "harness" not in kwargs:
        harness = offline_harness(ANALYSIS)
        if steps is not None:
            model = ScriptedChatModel(steps=list(steps))
            harness.models.chat_model_for_role = lambda role, model=model: model
            harness.scripted = model
        kwargs["harness"] = harness
    return AnalysisAgent(*args, **kwargs)


def _context() -> ExecutionContext:
    return ExecutionContext(
        run_id="run",
        tenant_id="tenant",
        user_id="user",
        agent_id="agent_analysis",
        parent_agent_id="agent_supervisor",
        workspace_root=".",
        allowed_tools={"calculate", "save_report"},
        metadata={"role": "analysis", "scopes": {"report"}},
    )


def _source(**updates: object) -> dict:
    item = {
        "title": "Growth",
        "url": "https://example.com/growth",
        "snippet": "series",
    }
    item.update(updates)
    return item


class _RecordingGateway:
    def __init__(self, inner: ToolGateway) -> None:
        self.inner = inner
        self.calls: list[ToolCall] = []

    def call(self, context: ExecutionContext, request: ToolCall) -> ToolResult:
        self.calls.append(request)
        return self.inner.call(context, request)

    @property
    def budget(self) -> BudgetLedger:
        return self.inner.budget


class _Gateway:
    def __init__(self, results: list[ToolResult]) -> None:
        self.results = list(results)
        self.calls: list[ToolCall] = []

    def call(self, context: ExecutionContext, request: ToolCall) -> ToolResult:
        del context
        self.calls.append(request)
        return self.results.pop(0)


class _Harness:
    def __init__(self, messages: list) -> None:
        self.messages = messages
        self.thread_id: str | None = None
        self.prompt = ""

    def invoke(self, role: str, prompt: str, tools: list | None = None, thread_id: str | None = None):
        del role, tools
        self.thread_id = thread_id
        self.prompt = prompt
        return self.messages


def _save_message(markdown: str, artifact_id: str, call_id: str) -> list:
    return [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "save_report",
                    "args": {"markdown": markdown},
                    "id": call_id,
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps(
                {"ok": True, "status": "SUCCESS", "artifact_id": artifact_id},
                ensure_ascii=False,
            ),
            name="save_report",
            tool_call_id=call_id,
        ),
    ]


def test_model_calculate_then_save_records_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    offered: list[list[str]] = []
    schemas: list[dict[str, set[str]]] = []
    observed: dict[str, object] = {}
    original_invoke = DeepAgentsHarness.invoke

    def invoke(self, role, prompt, tools=None, *, thread_id):
        observed["thread_id"] = thread_id
        observed["prompt"] = prompt
        return original_invoke(self, role, prompt, tools=tools, thread_id=thread_id)

    monkeypatch.setattr(DeepAgentsHarness, "invoke", invoke)
    store = ReportStore(tmp_path)
    registry = ToolRegistry()
    registry.register(ToolSpec(name="calculate", description="calculate"), calculate)
    registry.register(
        ToolSpec(
            name="save_report",
            description="save",
            required_scopes={"report"},
            has_side_effect=True,
        ),
        store.save,
    )
    gateway = _RecordingGateway(
        ToolGateway(
            registry,
            BudgetLedger(
                BudgetLimits(model_calls=0, tool_calls=4, agents=1, steps=1, runtime_seconds=30)
            ),
            CancellationToken(),
            {},
        )
    )
    report = "# 分析报告\n\n增长率 0.25"
    evidence = _source(values=[80, 100], content="page body", metadata={"secret": "nope"})
    agent = _agent(
        store,
        gateway=gateway,
        steps=[
            tool_call("calculate", {"operation": "growth_rate", "values": [80.0, 100.0]}, "c1"),
            tool_call("save_report", {"markdown": report}, "s1"),
            AIMessage(content="已完成分析。"),
        ],
    )
    result = agent.run(
        AnalysisRequest(objective="计算增长率并生成报告", evidence=[evidence]),
        _context(),
    )
    for tools in agent.harness.scripted.offered:
        offered.append(_tool_names(tools))
        schemas.append(_schema_properties(tools))

    assert observed["thread_id"] == "run"
    prompt = str(observed["prompt"])
    assert "page body" in prompt
    assert "nope" not in prompt
    assert result.status == ResultStatus.SUCCESS
    assert result.summary == "已完成分析。"
    assert result.structured_data["calculation"] == 0.25
    assert result.structured_data["calculation_operation"] == "growth_rate"
    assert result.structured_data["report_markdown"].startswith("# 分析报告")
    assert "0.25" in result.structured_data["report_markdown"]
    assert result.artifact_refs
    assert result.sources == [
        {
            "title": "Growth",
            "url": "https://example.com/growth",
            "snippet": "series",
            "content": "page body",
            "values": [80.0, 100.0],
        }
    ]
    assert [call.name for call in gateway.calls] == ["calculate", "save_report"]
    assert gateway.calls[0].arguments == {"operation": "growth_rate", "values": [80.0, 100.0]}
    assert gateway.calls[1].arguments["markdown"] == result.structured_data["report_markdown"]
    assert gateway.calls[1].arguments["operation_id"] == "run:analysis-report"
    assert gateway.calls[1].operation_id == "run:analysis-report"
    assert gateway.budget.used["tool_calls"] == 2
    visible = [names for names in offered if names]
    assert visible
    assert all(set(names) <= {"calculate", "save_report"} for names in visible)
    assert all(_FORBIDDEN.isdisjoint(names) for names in visible)
    save_schema = next(item["save_report"] for item in schemas if "save_report" in item)
    assert save_schema == {"markdown"}
    calculate_schema = next(item["calculate"] for item in schemas if "calculate" in item)
    assert calculate_schema == {"operation", "values"}


def test_invalid_url_is_an_error_and_stays_out_of_the_prompt(tmp_path: Path):
    harness = _Harness([*_save_message("MODEL REPORT", "art-1", "s1"), AIMessage(content="模型摘要")])
    result = _agent(ReportStore(tmp_path), harness=harness).run(
        AnalysisRequest(
            objective="生成报告",
            evidence=[
                _source(content="kept body"),
                {"title": "local", "url": "file:///tmp/secret", "snippet": "hidden"},
            ],
        ),
        _context(),
    )

    assert result.status == ResultStatus.PARTIAL
    assert result.errors == ["来源链接不是 http(s)"]
    assert result.sources == [
        {
            "title": "Growth",
            "url": "https://example.com/growth",
            "snippet": "series",
            "content": "kept body",
        }
    ]
    assert "file:///tmp/secret" not in harness.prompt
    assert "kept body" in harness.prompt
    assert harness.thread_id == "run"


def test_empty_evidence_cannot_succeed(tmp_path: Path):
    result = _agent(
        ReportStore(tmp_path),
        steps=[
            tool_call("save_report", {"markdown": "# 分析报告"}, "s1"),
            AIMessage(content="已完成分析。"),
        ],
    ).run(
        AnalysisRequest(objective="生成报告"),
        _context(),
    )

    assert result.status == ResultStatus.PARTIAL
    assert result.unmet_requirements == ["缺少研究证据"]
    assert result.summary == "已完成分析。"
    assert result.artifact_refs
    assert result.structured_data["evidence_count"] == 0


def test_report_and_summary_come_from_the_trace():
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "calculate",
                    "args": {"operation": "growth_rate", "values": [80, 100]},
                    "id": "c1",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(
            content=json.dumps({"ok": True, "status": "SUCCESS", "value": 0.25}),
            name="calculate",
            tool_call_id="c1",
        ),
        *_save_message("MODEL REPORT", "art-1", "s1"),
        AIMessage(content="模型摘要"),
    ]
    result = _agent(harness=_Harness(messages)).run(
        AnalysisRequest(objective="计算增长率", evidence=[_source(values=[80, 100])]),
        _context(),
    )

    assert result.status == ResultStatus.SUCCESS
    assert result.summary == "模型摘要"
    assert result.structured_data["report_markdown"] == "MODEL REPORT"
    assert result.structured_data["calculation"] == 0.25
    assert result.artifact_refs == ["art-1"]


def test_required_calculate_without_success_is_partial():
    result = _agent(
        harness=_Harness([*_save_message("MODEL REPORT", "art-1", "s1"), AIMessage(content="模型摘要")])
    ).run(
        AnalysisRequest(objective="计算求和", evidence=[_source(values=[1, 2, 3])]),
        _context(),
    )

    assert result.status == ResultStatus.PARTIAL
    assert result.unmet_requirements == ["缺少成功的计算"]
    assert result.structured_data["calculation"] is None
    assert result.structured_data["report_markdown"] == "MODEL REPORT"


def test_save_failure_and_unknown_keep_their_status(tmp_path: Path):
    request = AnalysisRequest(objective="生成报告", evidence=[_source()])
    failed = _Gateway([ToolResult(status=ToolStatus.FAILED, error="disk")])
    unknown = _Gateway([ToolResult(status=ToolStatus.UNKNOWN, error="tool timed out")])

    failed_result = _agent(
        ReportStore(tmp_path),
        gateway=failed,
        steps=[
            tool_call("save_report", {"markdown": "# 分析报告"}, "s1"),
            AIMessage(content="已完成分析。"),
        ],
    ).run(request, _context())
    unknown_result = _agent(
        ReportStore(tmp_path),
        gateway=unknown,
        steps=[
            tool_call("save_report", {"markdown": "# 分析报告"}, "s2"),
            AIMessage(content="已完成分析。"),
        ],
    ).run(request, _context())

    assert failed_result.status == ResultStatus.FAILED
    assert failed_result.artifact_refs == []
    assert failed_result.structured_data["report_markdown"] is None
    assert failed_result.errors == ["disk"]
    assert failed_result.summary == "已完成分析。"
    assert unknown_result.status == ResultStatus.UNKNOWN
    assert unknown_result.errors == ["tool timed out"]


def test_duplicate_save_is_recorded():
    messages = [
        *_save_message("FIRST", "art-1", "s1"),
        *_save_message("SECOND", "art-2", "s2"),
        AIMessage(content="模型摘要"),
    ]
    result = _agent(harness=_Harness(messages)).run(
        AnalysisRequest(objective="生成报告", evidence=[_source()]),
        _context(),
    )

    assert result.status == ResultStatus.PARTIAL
    assert "重复保存报告" in result.errors
    assert result.structured_data["report_markdown"] == "FIRST"
    assert result.artifact_refs == ["art-1", "art-2"]


def test_calculate_rejects_values_that_are_not_in_the_evidence():
    gateway = _Gateway([])
    tools = bind_analysis_tools(
        evidence=[_source(values=[80, 100])],
        reports=None,
        gateway=gateway,
        context=_context(),
        control=RunControl(),
    )
    calculate_tool = next(tool for tool in tools if tool.name == "calculate")
    save_tool = next(tool for tool in tools if tool.name == "save_report")

    rejected = json.loads(calculate_tool.invoke({"operation": "sum", "values": [1, 2]}))

    assert rejected["status"] == "FAILED"
    assert rejected["error"] == "数值不在证据中"
    assert gateway.calls == []
    assert set(save_tool.args) == {"markdown"}
    assert set(calculate_tool.args) == {"operation", "values"}


@pytest.mark.parametrize(
    "error",
    [CancellationRequested("stop"), BudgetExceeded("budget"), ToolDenied("denied")],
)
def test_run_control_errors_propagate(error: Exception, tmp_path: Path):
    class _Raising:
        def call(self, context: ExecutionContext, request: ToolCall) -> ToolResult:
            raise error

    with pytest.raises(type(error)):
        _agent(
            ReportStore(tmp_path),
            gateway=_Raising(),
            steps=[tool_call("save_report", {"markdown": "# 分析报告"}, "s1")],
        ).run(
            AnalysisRequest(objective="生成报告", evidence=[_source()]),
            _context(),
        )


def _tool_names(tools) -> list[str]:
    names: list[str] = []
    for tool in tools or []:
        if isinstance(tool, dict):
            function = tool.get("function", tool)
            name = function.get("name") if isinstance(function, dict) else None
        else:
            name = getattr(tool, "name", None)
        if isinstance(name, str):
            names.append(name)
    return names


def _schema_properties(tools) -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for tool in tools or []:
        if isinstance(tool, dict):
            function = tool.get("function", tool)
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            parameters = function.get("parameters")
            properties = parameters.get("properties") if isinstance(parameters, dict) else None
            if isinstance(name, str) and isinstance(properties, dict):
                found[name] = set(properties)
            continue
        name = getattr(tool, "name", None)
        args = getattr(tool, "args", None)
        if isinstance(name, str) and isinstance(args, dict):
            found[name] = set(args)
    return found
