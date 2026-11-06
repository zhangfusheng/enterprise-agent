"""Live research-worker case. It follows the orchestration execute path.

Skip unless ``EA_REAL_TESTS=1``. A credential-free run must not report this
case as passed. The provider secret is read from the environment, with the
repository ``.env`` loaded only when that variable is unset.

The case boots the process composition root with production settings, then
does what the execute node does: bind a run-scoped gateway, build the
supervisor context, and dispatch a research task. The model and Tavily
search are the ones configured for that process.
"""

from __future__ import annotations

import os
import pprint
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import load_dotenv

from enterprise_agent.agents.research.planning import PLAN_PROMPT_VERSION
from enterprise_agent.bootstrap.container import Container
from enterprise_agent.bootstrap.settings import Settings
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.plans import TaskSpec
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.infrastructure.observability.metrics import (
    begin_run_metrics,
    metrics_snapshot,
    reset_run_metrics,
)
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.tools.web.providers import TavilySearchProvider

pytestmark = pytest.mark.skipif(
    os.environ.get("EA_REAL_TESTS", "").strip().lower() not in {"1", "true", "yes"},
    reason="set EA_REAL_TESTS=1 to call the configured research model and web search",
)

_QUERY = "搜索 Python 语言的官方网站，给出可核对的公开来源。"


@pytest.fixture(scope="module")
def live_research(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Container]:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    root = tmp_path_factory.mktemp("live-research")
    container = Container(
        Settings(
            environment="production",
            dev_identity=False,
            offline_mode=False,
            allow_external_network=True,
            database_url=f"sqlite:///{root}/app.sqlite",
            checkpoint_url=f"sqlite:///{root}/checkpoint.sqlite",
            artifact_root=root / "artifacts",
            search_primary_provider="tavily",
            search_fallback_providers="",
        )
    )
    profile = container.models.profile_for_role("research")
    if profile.provider == "offline":
        container.close()
        pytest.fail("research model is offline; the live worker would not call a provider")
    try:
        yield container
    finally:
        container.close()


def test_research_worker_collects_live_sources(live_research: Container) -> None:
    container = live_research
    profile = container.models.profile_for_role("research")
    assert isinstance(container.search_provider.primary_provider, TavilySearchProvider)
    assert "web_search" in container.policy.get_role_policy("research").tools

    task = TaskSpec(
        task_id="task_research",
        goal_ids=["goal_main"],
        agent_role="research",
        task_description=_QUERY,
        expected_output="来源列表",
    )
    metrics = begin_run_metrics()
    try:
        result, ledger = _execute(container, task)
    finally:
        snapshot = metrics_snapshot()
        reset_run_metrics(metrics)

    pprint.pprint(result.model_dump(mode="json"))
    assert result.status in {ResultStatus.SUCCESS, ResultStatus.PARTIAL}
    assert result.agent_id == "agent_research"
    assert result.structured_data["query"] == _QUERY
    assert result.sources
    for source in result.sources:
        assert isinstance(source["title"], str) and source["title"].strip()
        assert isinstance(source["url"], str) and source["url"].startswith(("http://", "https://"))
    assert ledger.used.get("tool_calls", 0) >= 1
    assert _tool_names(container) <= {"web_search", "web_fetch"}
    assert "web_search" in _tool_names(container)

    calls = [call for call in snapshot["calls"] if isinstance(call, dict)]
    assert calls
    assert {call["role"] for call in calls} == {"research"}
    assert {call["model_version"] for call in calls} == {profile.model_name}
    assert {call["prompt_version"] for call in calls} == {PLAN_PROMPT_VERSION}
    assert {call["outcome"] for call in calls} == {"success"}


def _execute(container: Container, task: TaskSpec) -> tuple[AgentResult, BudgetLedger]:
    """Run one research task the way the orchestration execute node does."""
    ledger = BudgetLedger(container.budget_limits)
    cancellation = CancellationToken()
    container.workers.bind_run(ledger, cancellation)
    context = ExecutionContext(
        run_id="run-live-research",
        tenant_id="local-tenant",
        user_id="local-user",
        agent_id="agent_supervisor",
        workspace_root=".",
        allowed_roles=container.policy.roles(),
        allowed_tools=container.policy.tools(),
        metadata={"role": "research", "scopes": container.workers.tool_scopes()},
    )
    ledger.consume("agents")
    ledger.consume("steps")
    result = container.workers.execute(
        task,
        context,
        {},
        delegate=False,
        agent_count=ledger.used.get("agents", 0),
    )
    return result, ledger


def _tool_names(container: Container) -> set[str]:
    gateway = container.research.gateway
    assert gateway is not None
    names: set[str] = set()
    for operation_id in gateway.idempotency_store:
        names.add(operation_id.split(":")[-2])
    return names
