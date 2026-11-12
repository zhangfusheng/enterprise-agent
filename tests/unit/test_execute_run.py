import pytest

from enterprise_agent.application.execute_run import _assistant_content, execute_run
from enterprise_agent.application.submit_run import submit_run
from enterprise_agent.contracts.context import IdentityContext
from enterprise_agent.contracts.runs import RunCreate, RunStatus


class _Graph:
    def __init__(self, result):
        self.result = result

    def invoke(self, *_args, **_kwargs):
        return self.result


def _broken_graph(*_args, **_kwargs):
    raise RuntimeError("graph down")


def _submit(container) -> str:
    run_id = submit_run(
        container.runs,
        IdentityContext(tenant_id="tenant-a", user_id="user-a", is_authenticated=True),
        RunCreate(input="请直接回答一个问题"),
    )
    assert container.runs.claim_work("worker-a", 30) == run_id
    return run_id


def test_execution_error_marks_run_failed(container, monkeypatch):
    monkeypatch.setattr("enterprise_agent.application.execute_run.build_graph", _broken_graph)
    run_id = _submit(container)
    execute_run(container, run_id, "worker-a")
    view = container.runs.get_run_view("tenant-a", run_id)
    state = container.runs.get_run_unscoped(run_id).state_json
    assert view.status == RunStatus.FAILED
    assert view.summary == "执行失败"
    assert state["error"] == "RuntimeError"
    assert state["error_message"] == "RuntimeError: graph down"


def test_unrecorded_failure_propagates_for_retry(container, monkeypatch):
    monkeypatch.setattr("enterprise_agent.application.execute_run.build_graph", _broken_graph)

    def db_down(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(container.runs, "commit_outcome", db_down)
    run_id = _submit(container)
    with pytest.raises(RuntimeError, match="db down"):
        execute_run(container, run_id, "worker-a")
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.RUNNING


def test_non_mapping_graph_result_marks_run_failed(container, monkeypatch):
    monkeypatch.setattr(
        "enterprise_agent.application.execute_run.build_graph",
        lambda *_args, **_kwargs: _Graph(["not", "a", "mapping"]),
    )
    run_id = _submit(container)
    execute_run(container, run_id, "worker-a")
    state = container.runs.get_run_unscoped(run_id).state_json
    assert container.runs.get_run_view("tenant-a", run_id).status == RunStatus.FAILED
    assert state["error"] == "TypeError"


def test_assistant_content_exposes_saved_report_and_search_sources():
    content = _assistant_content(
        {
            "summary": "收集到 1 条已验证来源。",
            "task_results": {
                "task_research": {
                    "sources": [
                        {
                            "title": "来源标题",
                            "url": "https://example.com/article",
                            "snippet": "搜索结果摘要",
                        }
                    ],
                    "structured_data": {"evidence": []},
                },
                "task_analysis": {
                    "sources": [
                        {
                            "title": "来源标题",
                            "url": "https://example.com/article",
                            "snippet": "搜索结果摘要",
                        }
                    ],
                    "structured_data": {
                        "report_markdown": "# 分析报告\n\n这是报告正文。",
                    },
                },
            },
        }
    )

    assert content is not None
    assert "# 分析报告" in content
    assert "这是报告正文。" in content
    assert "来源标题" in content
    assert "搜索结果摘要" in content
    assert "收集到 1 条已验证来源" not in content


def test_assistant_content_formats_search_results_without_report():
    content = _assistant_content(
        {
            "summary": "收集到 1 条已验证来源。",
            "task_results": {
                "task_research": {
                    "sources": [
                        {
                            "title": "来源标题",
                            "url": "https://example.com/article",
                            "content": "网页正文内容",
                        }
                    ]
                }
            },
        }
    )

    assert content == (
        "## 搜索结果\n\n"
        "### 1. 来源标题\n\n"
        "来源：https://example.com/article\n\n"
        "网页正文内容"
    )
