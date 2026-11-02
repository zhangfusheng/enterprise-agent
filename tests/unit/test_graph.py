import logging
import pprint

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from enterprise_agent.contracts.plans import TaskStatus
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.contracts.runs import RunStatus
from enterprise_agent.governance.authorization import AuthorizationPolicy, RolePolicy
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.orchestration.execution.workers import WorkerRegistry
from enterprise_agent.orchestration.graph import GraphDependencies, build_graph
from enterprise_agent.orchestration.routing.parser import GoalParser


class _ScriptedWorker:
    def __init__(self, role: str, script: dict[str, list[AgentResult]]):
        self.role = role
        self.script = script
        self.seen: list[tuple[str, int]] = []

    @property
    def bound_agents(self) -> tuple:
        return ()

    def execute(self, task, context, results, *, delegate, agent_count) -> AgentResult:
        del context, results, delegate, agent_count
        self.seen.append((task.task_id, task.attempts))
        queued = self.script.get(task.task_id, [])
        if queued:
            return queued.pop(0)
        return AgentResult(status=ResultStatus.SUCCESS, summary=task.task_id)


class _TwoAnswerParser:
    def parse(self, text, inputs=None, constraints=None):
        spec = GoalParser().parse("请解释幂等性", inputs, constraints)
        if len(inputs or []) < 2:
            return spec.model_copy(update={"missing_information": ["还需要一条补充信息"]})
        return spec


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


def _limits() -> BudgetLimits:
    return BudgetLimits(model_calls=10, tool_calls=10, agents=8, steps=20, runtime_seconds=100)


def _deps(
    *workers: _ScriptedWorker,
    max_replans: int = 2,
    external_network_allowed: bool = True,
) -> GraphDependencies:
    return GraphDependencies(
        WorkerRegistry(workers),
        _policy(),
        _limits(),
        max_tasks=12,
        max_replans=max_replans,
        external_network_allowed=external_network_allowed,
    )


def _state(text: str) -> dict:
    return {
        "run_id": "run-1",
        "tenant_id": "tenant-a",
        "user_id": "user-a",
        "input_text": text,
        "inputs": [],
        "constraints": {},
    }


def _success(**updates) -> AgentResult:
    payload = {"status": ResultStatus.SUCCESS, "summary": "完成"}
    payload.update(updates)
    return AgentResult(**payload)


def _failed(summary: str = "失败") -> AgentResult:
    return AgentResult(status=ResultStatus.FAILED, summary=summary, errors=["down"])


def test_direct_answer_ends_at_acceptance():
    final = build_graph(_deps()).invoke(_state("请解释幂等性"))
    pprint.pp(final)
    assert final["status"] == RunStatus.SUCCEEDED
    assert final["route"]["route"] == "direct"
    assert final["result"]["summary"].startswith("直接回答")
    assert "plan" not in final


def test_external_route_waits_when_online_network_is_not_enabled():
    final = build_graph(_deps(external_network_allowed=False)).invoke(
        _state("请搜索网页并返回来源")
    )

    assert final["status"] == RunStatus.WAITING_INPUT
    assert final["route"]["route"] == "single"
    assert final["missing_information"] == ["需要启用外网搜索权限后才能执行该任务"]
    assert "plan" not in final


def test_route_log_records_versions_without_user_text(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger="enterprise_agent.orchestration")
    text = "请解释幂等性"
    build_graph(_deps()).invoke(_state(text))
    messages = [record.getMessage() for record in caplog.records]
    pprint.pp(messages)
    assert messages
    assert all(text not in message for message in messages)
    joined = "\n".join(messages)
    assert "node=parse_route" in joined
    assert "parser=rules" in joined
    assert "model_version=none" in joined
    assert "prompt_version=none" in joined
    assert "route=direct" in joined
    assert "node=accept" in joined


def test_waiting_input_has_no_route_until_material_arrives():
    graph = build_graph(_deps(_ScriptedWorker("analysis", {})), InMemorySaver())
    config = {"configurable": {"thread_id": "wait-1"}}
    paused = graph.invoke(_state("请分析我的资料"), config)
    pprint.pp(paused)
    assert paused["__interrupt__"]
    assert "route" not in graph.get_state(config).values
    final = graph.invoke(Command[tuple[()]](resume="附件正文"), config)
    print("test_waiting_input_has_no_route_until_material_arrives-----------------------------------------------")
    pprint.pp(final)
    assert final["route"]["route"] == "single"
    assert final["route"]["selected_role"] == "analysis"
    assert final["inputs"] == [{"name": "user_input", "content": "附件正文"}]
    assert final["status"] == RunStatus.SUCCEEDED


def test_waiting_input_restarts_from_all_persisted_answers():
    graph = build_graph(
        GraphDependencies(
            WorkerRegistry(()),
            _policy(),
            _limits(),
            max_tasks=12,
            max_replans=2,
            parser=_TwoAnswerParser(),
        ),
        InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "wait-2"}}
    first = graph.invoke(_state("请解释幂等性"), config)
    assert first["__interrupt__"]

    one_answer = _state("请解释幂等性")
    one_answer["inputs"] = [{"name": "user_input", "content": "第一条"}]
    second = graph.invoke(one_answer, config)
    assert second["__interrupt__"]

    all_answers = _state("请解释幂等性")
    all_answers["inputs"] = [
        {"name": "user_input", "content": "第一条"},
        {"name": "user_input", "content": "第二条"},
    ]
    final = graph.invoke(all_answers, config)
    assert final["status"] == RunStatus.SUCCEEDED
    assert final["inputs"] == all_answers["inputs"]


def test_failed_dependency_skips_downstream_and_retries_only_the_failure():
    research = _ScriptedWorker("research", {"task_research": [_failed("搜索失败")]})
    analysis = _ScriptedWorker(
        "analysis",
        {"task_analysis": [_success(summary="报告", artifact_refs=["art-1"], sources=[{"url": "https://a.example"}])]},
    )
    final = build_graph(_deps(research, analysis)).invoke(_state("请搜索网页并计算增长率"))
    pprint.pp(final)
    assert research.seen == [("task_research", 1), ("task_research", 2)]
    assert analysis.seen == [("task_analysis", 1)]
    tasks = {task["task_id"]: task for task in final["plan"]["tasks"]}
    assert tasks["task_research"]["status"] == TaskStatus.SUCCEEDED
    assert tasks["task_research"]["attempts"] == 2
    assert tasks["task_research"]["result_ref"] == "task_research"
    assert tasks["task_analysis"]["status"] == TaskStatus.SUCCEEDED
    assert tasks["task_analysis"]["attempts"] == 1
    assert final["replan_count"] == 1
    assert final["result"]["artifact_refs"] == ["art-1"]
    assert final["result"]["sources"] == [{"url": "https://a.example"}]
    assert final["status"] == RunStatus.SUCCEEDED


def test_succeeded_task_is_not_executed_again():
    research = _ScriptedWorker(
        "research",
        {"task_research": [_success(summary="来源", sources=[{"url": "https://a.example"}])]},
    )
    analysis = _ScriptedWorker("analysis", {"task_analysis": [_failed("报告失败")]})
    final = build_graph(_deps(research, analysis)).invoke(_state("请搜索网页并分析，生成报告"))
    assert research.seen == [("task_research", 1)]
    assert [attempts for _, attempts in analysis.seen] == [1, 2]
    assert final["status"] == RunStatus.SUCCEEDED
    assert final["result"]["sources"] == [{"url": "https://a.example"}]


def test_exhausted_replan_accepts_the_failure_and_skips_dependents():
    research = _ScriptedWorker("research", {"task_research": [_failed(), _failed(), _failed()]})
    analysis = _ScriptedWorker("analysis", {})
    final = build_graph(_deps(research, analysis, max_replans=0)).invoke(_state("请搜索网页并分析，生成报告"))
    assert research.seen == [("task_research", 1)]
    assert analysis.seen == []
    tasks = {task["task_id"]: task for task in final["plan"]["tasks"]}
    assert tasks["task_research"]["status"] == TaskStatus.FAILED
    assert tasks["task_analysis"]["status"] == TaskStatus.SKIPPED
    assert final["status"] == RunStatus.FAILED
    assert final["result"]["summary"] == "失败"


def test_research_fetch_budget_failure_does_not_trigger_replan():
    research = _ScriptedWorker(
        "research",
        {
            "task_research": [
                AgentResult(
                    status=ResultStatus.FAILED,
                    summary="研究未完成",
                    structured_data={
                        "research": {
                            "stop_reason": "fetch_budget_exhausted",
                            "verified_source_count": 0,
                        }
                    },
                    errors=["抓取次数达到 research 局部预算"],
                )
            ]
        },
    )
    analysis = _ScriptedWorker("analysis", {})

    final = build_graph(_deps(research, analysis, max_replans=2)).invoke(
        _state("请搜索网页并分析，生成报告")
    )

    assert research.seen == [("task_research", 1)]
    assert final.get("replan_count", 0) == 0
    assert final["status"] == RunStatus.FAILED
    tasks = {task["task_id"]: task for task in final["plan"]["tasks"]}
    assert tasks["task_research"]["status"] == TaskStatus.FAILED
    assert tasks["task_analysis"]["status"] == TaskStatus.SKIPPED
