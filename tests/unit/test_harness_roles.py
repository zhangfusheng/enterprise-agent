import pytest
from tests.support.harness import offline_harness

from enterprise_agent.agents.harness.chat import RoleHarnessChatModel
from enterprise_agent.agents.harness.deepagents import DeepAgentsHarness
from enterprise_agent.agents.harness.registry import HarnessRoleRegistry
from enterprise_agent.agents.harness.roles import (
    ANALYSIS,
    RESEARCH,
    AnalysisHarnessChatModel,
    ResearchHarnessChatModel,
)
from enterprise_agent.agents.harness.spec import HarnessRole, application_tools_role
from enterprise_agent.infrastructure.observability.tracing import build_trace_view, tracing_scope


class WriterHarnessChatModel(RoleHarnessChatModel):
    """Selects the writer harness profile."""

    model_name: str = "writer"


def _writer() -> HarnessRole:
    return HarnessRole(
        name="writer",
        system_prompt="你是 writer Worker。只返回可交付结果。",
        chat_model=WriterHarnessChatModel,
    )


def test_registered_roles_use_their_configured_models():
    harness = offline_harness(RESEARCH, ANALYSIS)
    research = harness._wrap_model(harness.roles.get("research"), harness._chat_model("research"))
    analysis = harness._wrap_model(harness.roles.get("analysis"), harness._chat_model("analysis"))

    assert harness.roles.roles() == {"research", "analysis"}
    assert isinstance(research, ResearchHarnessChatModel)
    assert isinstance(analysis, AnalysisHarnessChatModel)
    assert research._get_ls_params() == {"ls_provider": "enterprise", "ls_model_name": "research"}
    assert analysis._get_ls_params() == {"ls_provider": "enterprise", "ls_model_name": "analysis"}


def test_unknown_role_is_rejected():
    with pytest.raises(KeyError, match="unknown harness role: writer"):
        offline_harness(RESEARCH).build("writer")


def test_blank_prompt_is_rejected():
    with pytest.raises(ValueError, match="non-empty"):
        offline_harness(RESEARCH).invoke("research", "  ", thread_id="thread-1")


def test_thread_id_is_required():
    with pytest.raises(ValueError, match="thread_id"):
        offline_harness(RESEARCH).invoke("research", "查询", thread_id=" ")


def test_harness_rejects_a_missing_registry():
    with pytest.raises(TypeError, match="ModelRegistry"):
        DeepAgentsHarness(object(), HarnessRoleRegistry())  # type: ignore[arg-type]


def test_configured_model_answers_the_worker():
    harness = offline_harness(_writer())

    messages = harness.invoke("writer", "写一段说明", thread_id="thread-1")

    assert "写一段说明" in str(messages[-1].content)


def test_deep_agent_nodes_are_recorded_as_measured_trace_spans():
    class Sink:
        def __init__(self) -> None:
            self.spans = {}

        def record(self, span) -> None:
            self.spans[span.id] = span

    sink = Sink()
    with tracing_scope(sink, run_id="trace-worker", tenant_id="tenant-a"):
        offline_harness(_writer()).invoke("writer", "写一段说明", thread_id="thread-1")

    view = build_trace_view("trace-worker", list(sink.spans.values()))
    names = [span.name for span in view.runs]
    assert names[:4] == ["writer", "LangGraph", "PatchToolCallsMiddleware.before_agent", "model"]
    assert "OfflineChatModel" in names
    assert all(span.timing_source == "measured" for span in view.runs)


def test_duplicate_role_is_rejected():
    role = application_tools_role("writer", "你是 writer Worker。", WriterHarnessChatModel)
    registry = HarnessRoleRegistry([role])

    with pytest.raises(ValueError, match="duplicate harness role"):
        registry.register(role)


def test_application_tools_role_must_hide_builtin_tools():
    with pytest.raises(ValueError, match="must exclude built-in tools"):
        HarnessRole(
            name="writer",
            system_prompt="你是 writer Worker。",
            chat_model=WriterHarnessChatModel,
            application_tools_only=True,
            general_purpose_subagent=False,
        )


def test_application_tools_role_cannot_enable_the_general_purpose_subagent():
    with pytest.raises(ValueError, match="general-purpose subagent"):
        HarnessRole(
            name="writer",
            system_prompt="你是 writer Worker。",
            chat_model=WriterHarnessChatModel,
            application_tools_only=True,
            excluded_tools=frozenset({"execute"}),
        )


def test_model_name_must_match_the_role_name():
    role = HarnessRole(
        name="editor",
        system_prompt="你是 editor Worker。",
        chat_model=WriterHarnessChatModel,
    )

    with pytest.raises(ValueError, match="model_name"):
        HarnessRoleRegistry().register(role)
