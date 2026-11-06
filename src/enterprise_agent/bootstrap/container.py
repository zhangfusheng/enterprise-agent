"""Process composition root for the agent runtime.

Process services (database, model registry, tool catalog, authorization) live for
the life of the process. Run services (budget, cancellation, tool idempotency)
are created per run through ``Container.open_gateway`` and installed by
``WorkerRegistry.bind_run``.
"""

from __future__ import annotations

import re
import threading
from functools import partial
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from enterprise_agent.agents.analysis.definition import AnalysisAgent
from enterprise_agent.agents.delegation.service import DelegationService
from enterprise_agent.agents.factory import AgentFactory
from enterprise_agent.agents.harness.deepagents import WORKER_PROMPT_VERSION, DeepAgentsHarness
from enterprise_agent.agents.harness.registry import HarnessRoleRegistry
from enterprise_agent.agents.harness.roles import ANALYSIS, RESEARCH
from enterprise_agent.agents.research.definition import ResearchAgent
from enterprise_agent.agents.research.planning import (
    PLAN_PROMPT_VERSION as RESEARCH_PLAN_PROMPT_VERSION,
)
from enterprise_agent.agents.research.planning import (
    OfflinePlanCompletion,
    ResearchPlanner,
    StructuredPlanCompletion,
)
from enterprise_agent.agents.research.tools import ResearchLoopPolicy
from enterprise_agent.application.research import ResearchApplication
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.governance.authorization import AuthorizationPolicy, RolePolicy
from enterprise_agent.governance.budgets import BudgetLimits
from enterprise_agent.governance.delegation import DelegationGuard
from enterprise_agent.infrastructure.checkpoints.store import CheckpointFactory
from enterprise_agent.infrastructure.database.migrations import create_all
from enterprise_agent.infrastructure.database.session import build_engine, session_factory
from enterprise_agent.infrastructure.repositories.artifacts import ArtifactRepository
from enterprise_agent.infrastructure.repositories.operations import OperationRepository
from enterprise_agent.infrastructure.repositories.runs import RunRepository
from enterprise_agent.infrastructure.repositories.traces import TraceRepository
from enterprise_agent.models.catalog import ModelCatalog
from enterprise_agent.models.config import ModelConfig
from enterprise_agent.models.loader import load_model_config
from enterprise_agent.models.registry import ModelRegistry
from enterprise_agent.models.validation import validate_profile_cards
from enterprise_agent.orchestration.execution.workers import (
    AnalysisWorker,
    ResearchWorker,
    WorkerRegistry,
)
from enterprise_agent.orchestration.planning.llm_planner import (
    PROMPT_VERSION as ORCHESTRATION_PLAN_PROMPT_VERSION,
)
from enterprise_agent.orchestration.routing.llm_parser import PROMPT_VERSION as GOAL_PROMPT_VERSION
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.tools.calculate import calculate
from enterprise_agent.tools.contracts import ToolSpec
from enterprise_agent.tools.gateway import ToolGateway
from enterprise_agent.tools.registry import ToolRegistry
from enterprise_agent.tools.save_report import ReportStore
from enterprise_agent.tools.web.fetch import web_fetch
from enterprise_agent.tools.web.provider import (
    ProviderRegistry,
    SearchProvider,
    SearchProviderRouter,
)
from enterprise_agent.tools.web.providers import DoubaoSearchProvider, TavilySearchProvider
from enterprise_agent.tools.web.search import web_search
from enterprise_agent.tools.web.testing import FixtureSearchProvider
from enterprise_agent.tools.workspace import Workspace, WorkspaceDenied

from .settings import Settings, get_settings

_SCOPE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_WEB_TOOL_TIMEOUT_SECONDS = 20.0


class BootstrapError(RuntimeError):
    """Startup wiring failed closed before the process accepted work."""


class _RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capabilities: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    can_delegate_to: list[str] = Field(default_factory=list)

    @field_validator("capabilities", "tools", "can_delegate_to")
    @classmethod
    def names_are_tokens(cls, values: list[str]) -> list[str]:
        if any(not item.strip() for item in values):
            raise ValueError("policy names must not be blank")
        return values


class _AgentCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(default="agents-v1", min_length=1)
    roles: dict[str, _RoleSpec]

    @field_validator("roles")
    @classmethod
    def roles_are_named(cls, roles: dict[str, _RoleSpec]) -> dict[str, _RoleSpec]:
        if not roles or any(not name.strip() for name in roles):
            raise ValueError("agent policy requires named roles")
        return roles


class Container:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        initialize_db: bool | None = None,
        config_root: Path | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.settings.validate_startup()
        self.engine = build_engine(self.settings.database_url)
        if _should_create_schema(self.settings, initialize_db):
            create_all(self.engine)
        self.sessions = session_factory(self.engine)
        self.runs = RunRepository(self.sessions)
        self.artifacts = ArtifactRepository(self.sessions)
        self.operations = OperationRepository(self.sessions)
        self.traces = TraceRepository(self.sessions)
        self.checkpoints = CheckpointFactory(self.settings.checkpoint_url)
        root = config_root or _default_config_root()
        self.model_config = _load_model_config(root / "models.yaml")
        self.catalog = ModelCatalog(root / "model")
        _assert_model_cards(self.model_config, self.catalog, root / "models.yaml")
        self.models = ModelRegistry(self.model_config, self.catalog)
        self.policy, self.policy_version = _load_policy(root / "agents.yaml")
        self.orchestration_version = _orchestration_version(root / "orchestration.yaml")
        self.report_store = ReportStore(self.settings.artifact_root)
        self.search_provider = _build_search_provider(self.settings)
        self.harness = DeepAgentsHarness(self.models, _harness_roles(), traces=self.traces)
        self.research_planner = ResearchPlanner(
            lambda context: _research_plan_completion(self.models, self.settings, context),
            max_search_calls=self.settings.research_max_search_calls,
            max_fetch_calls=self.settings.research_max_fetch_calls,
            max_candidate_sources=self.settings.research_max_candidate_sources,
            min_verified_sources=self.settings.research_min_usable_sources,
            max_concurrency=self.settings.research_plan_max_concurrency,
            repair_attempts=self.settings.research_plan_repair_attempts,
            allow_partial_results=self.settings.research_plan_allow_partial_results,
        )
        self.budget_limits = _budget_limits(self.settings)
        self.tools = self._build_tools()
        _assert_runtime_contract(self.policy, self.tools, self.models, self.harness.roles.roles())
        self.tool_gateway = self.open_gateway()
        self.research = ResearchAgent(
            self.search_provider,
            planner=self.research_planner,
            gateway=self.tool_gateway,
            loop_policy=ResearchLoopPolicy(
                max_search_calls=self.settings.research_max_search_calls,
                max_fetch_calls=self.settings.research_max_fetch_calls,
                max_total_tool_calls=self.settings.research_max_tool_calls,
                max_duplicate_calls=self.settings.research_max_duplicate_calls,
                min_usable_sources=self.settings.research_min_usable_sources,
                max_candidate_sources=self.settings.research_max_candidate_sources,
                min_content_length=self.settings.research_min_content_length,
                # Offline search uses local fixture candidates without a real
                # page transport. Online/production runs keep the strict
                # body-verification gate enabled.
                require_fetch=(
                    self.settings.research_require_fetch
                    and not self.settings.offline_mode
                ),
            ),
        )
        self.research_application = ResearchApplication(self.research, self.open_gateway)
        self.analysis = AnalysisAgent(
            self.report_store,
            harness=self.harness,
            gateway=self.tool_gateway,
        )
        self.delegation = DelegationService(
            DelegationGuard(
                self.policy,
                self.settings.max_agent_depth,
                self.settings.max_agents,
            ),
            self.analysis,
        )
        self.workers = WorkerRegistry([
            ResearchWorker(self.research),
            AnalysisWorker(self.analysis, self.delegation),
        ])
        missing = sorted(self.workers.roles() - self.harness.roles.roles())
        if missing:
            raise BootstrapError(f"workers have no harness role: {missing}")
        self.agents = AgentFactory(self.research, self.analysis, self.delegation)

    def open_gateway(
        self,
        ledger: BudgetLedger | None = None,
        cancellation: CancellationToken | None = None,
    ) -> ToolGateway:
        """Return a gateway whose budget, cancellation, and idempotency belong to one run."""
        return ToolGateway(
            self.tools,
            ledger or BudgetLedger(self.budget_limits),
            cancellation or CancellationToken(),
            {},
            operations=self.operations,
        )

    def close(self) -> None:
        self.search_provider.close()
        self.engine.dispose()

    @property
    def config_snapshot(self) -> dict[str, Any]:
        providers = {item.name: item for item in self.model_config.providers}
        return {
            "models": {
                "profiles": [
                    {
                        "name": item.name,
                        "provider": item.provider,
                        "model_name": item.model_name,
                        "config_version": providers[item.provider].config_version,
                    }
                    for item in self.model_config.profiles
                ],
                "bindings": [
                    {"role": item.role, "model_profile": item.model_profile}
                    for item in self.model_config.bindings
                ],
            },
            "runtime": {
                "offline_mode": self.settings.offline_mode,
                "allow_external_network": self.settings.allow_external_network,
                "network_mode": (
                    "offline_fixture"
                    if self.settings.offline_mode
                    else "external"
                    if self.settings.allow_external_network
                    else "disabled"
                ),
                "search": {
                    "primary_provider": self.settings.search_primary_provider,
                    "fallback_providers": self.settings.search_fallback_provider_names,
                    "active_providers": self.search_provider.provider_names,
                    "doubao_max_query_chars": self.settings.doubao_search_max_query_chars,
                },
                "research_plan": {
                    "max_search_calls": self.settings.research_max_search_calls,
                    "max_fetch_calls": self.settings.research_max_fetch_calls,
                    "max_concurrency": self.settings.research_plan_max_concurrency,
                    "repair_attempts": self.settings.research_plan_repair_attempts,
                },
                "max_replans": self.settings.max_replans,
                "max_tasks": self.settings.max_tasks,
                "max_agent_depth": self.settings.max_agent_depth,
                "budgets": self.budget_limits.model_dump(mode="json"),
                "research_loop": self.research.loop_policy.snapshot(),
            },
            "tools": sorted(self.tools.names()),
            "roles": sorted(self.policy.roles()),
            "versions": {
                "policy": self.policy_version,
                "orchestration": self.orchestration_version,
                "goal_prompt": GOAL_PROMPT_VERSION,
                "plan_prompt": ORCHESTRATION_PLAN_PROMPT_VERSION,
                "research_plan_prompt": RESEARCH_PLAN_PROMPT_VERSION,
                "worker_prompt": WORKER_PROMPT_VERSION,
            },
        }

    def config_snapshot_for(
        self,
        model_profile: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        """Pin the effective model choice without exposing provider secrets."""
        snapshot = self.config_snapshot
        selected = model_profile
        if not selected and self.settings.offline_mode:
            selected = self.models.offline_profile_name()
        snapshot["models"]["selected_profile"] = selected
        snapshot["models"]["selected_reasoning_effort"] = reasoning_effort
        return snapshot

    def _build_tools(self) -> ToolRegistry:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="web_search",
                description="Search the public web",
                required_scopes={"web"},
                timeout_seconds=_WEB_TOOL_TIMEOUT_SECONDS,
            ),
            partial(
                web_search,
                self.search_provider,
                max_limit=self.settings.search_max_results,
            ),
        )
        registry.register(
            ToolSpec(
                name="web_fetch",
                description="Fetch a public web page",
                required_scopes={"web"},
                timeout_seconds=_WEB_TOOL_TIMEOUT_SECONDS,
            ),
            web_fetch,
        )
        registry.register(
            ToolSpec(name="calculate", description="Deterministic calculation"),
            calculate,
        )
        registry.register(
            ToolSpec(
                name="workspace_read",
                description="Read a file inside one tenant run workspace",
                required_scopes={"workspace"},
            ),
            self._read_workspace,
        )
        registry.register(
            ToolSpec(
                name="save_report",
                description="Save a Markdown report",
                required_scopes={"report"},
                has_side_effect=True,
                idempotent=True,
            ),
            self._save_report,
        )
        return registry

    def _read_workspace(self, tenant_id: str, run_id: str, path: str) -> str:
        if not path or not path.strip():
            raise WorkspaceDenied("path must not be blank")
        root = self.settings.artifact_root / _scope_id("tenant_id", tenant_id) / _scope_id("run_id", run_id)
        return Workspace(root).read(path)

    def _save_report(self, tenant_id: str, run_id: str, markdown: str, operation_id: str):
        return self.report_store.save(tenant_id, run_id, markdown, operation_id)


_container_lock = threading.Lock()
_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is not None:
        return _container
    with _container_lock:
        if _container is None:
            _container = Container()
        return _container


def shutdown_container() -> None:
    global _container
    with _container_lock:
        container = _container
        _container = None
    if container is not None:
        container.close()


def _default_config_root() -> Path:
    root = Path(__file__).resolve().parents[3] / "configs"
    if not root.is_dir():
        raise BootstrapError(f"config directory not found: {root}")
    return root


def _should_create_schema(settings: Settings, initialize_db: bool | None) -> bool:
    if initialize_db is not None:
        return initialize_db
    return settings.environment != "production"


def _build_search_provider(settings: Settings) -> SearchProviderRouter:
    if settings.offline_mode:
        registry = ProviderRegistry([FixtureSearchProvider()])
        return SearchProviderRouter(registry, "fixture", retry_count=0)
    if not settings.allow_external_network:
        raise BootstrapError(
            "external search requires EA_ALLOW_EXTERNAL_NETWORK=true when offline mode is disabled"
        )
    configured_names = (
        settings.search_primary_provider,
        *settings.search_fallback_provider_names,
    )
    providers: list[SearchProvider] = []
    if "tavily" in configured_names:
        providers.append(
            TavilySearchProvider(
                api_key_ref=settings.tavily_api_key_ref,
                timeout=settings.search_provider_timeout_seconds,
                search_depth=settings.tavily_search_depth,
            )
        )
    if "doubao_custom" in configured_names:
        providers.append(
            DoubaoSearchProvider(
                api_key_ref=settings.doubao_search_api_key_ref,
                timeout=settings.search_provider_timeout_seconds,
                max_query_chars=settings.doubao_search_max_query_chars,
                query_rewrite=settings.doubao_search_query_rewrite,
            )
        )
    registry = ProviderRegistry(providers)
    return SearchProviderRouter(
        registry,
        settings.search_primary_provider,
        settings.search_fallback_provider_names,
        retry_count=settings.search_provider_retry_count,
        retry_backoff_seconds=settings.search_provider_retry_backoff_seconds,
    )


def _budget_limits(settings: Settings) -> BudgetLimits:
    return BudgetLimits(
        model_calls=settings.max_model_calls,
        tool_calls=settings.max_tool_calls,
        agents=settings.max_agents,
        steps=settings.max_steps,
        runtime_seconds=settings.max_runtime_seconds,
    )


def _research_plan_completion(
    models: ModelRegistry,
    settings: Settings,
    context: ExecutionContext,
):
    if settings.offline_mode:
        return OfflinePlanCompletion()
    model_profile = context.metadata.get("model_profile")
    reasoning_effort = context.metadata.get("reasoning_effort")
    profile = model_profile if isinstance(model_profile, str) and model_profile else None
    effort = reasoning_effort if isinstance(reasoning_effort, str) and reasoning_effort else None
    model = models.chat_model_for_role("research", profile, reasoning_effort=effort)
    model_config = models.profile_for_role("research", profile)
    return StructuredPlanCompletion(
        model,
        model_version=model_config.model_name,
        prices=model_config.parameters,
        offline=model_config.provider == "offline",
    )


def _load_model_config(path: Path):
    try:
        return load_model_config(path)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        raise BootstrapError(f"invalid model config {path}: {exc}") from exc


def _assert_model_cards(config: ModelConfig, catalog: ModelCatalog, path: Path) -> None:
    try:
        validate_profile_cards(config, catalog)
    except (OSError, ValueError) as exc:
        raise BootstrapError(f"invalid model config {path}: {exc}") from exc


def _load_policy(path: Path) -> tuple[AuthorizationPolicy, str]:
    try:
        catalog = _AgentCatalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    except (OSError, yaml.YAMLError, ValidationError, ValueError) as exc:
        raise BootstrapError(f"invalid agent policy {path}: {exc}") from exc
    policy = AuthorizationPolicy([
        RolePolicy(
            role=role,
            capabilities=frozenset(spec.capabilities),
            tools=frozenset(spec.tools),
            can_delegate_to=frozenset(spec.can_delegate_to),
        )
        for role, spec in catalog.roles.items()
    ])
    for role in sorted(policy.roles()):
        unknown = sorted(policy.get_role_policy(role).can_delegate_to - policy.roles())
        if unknown:
            raise BootstrapError(f"role {role} delegates to unknown roles: {unknown}")
    return policy, catalog.version


def _orchestration_version(path: Path) -> str:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        return "orchestration-v1"
    version = raw.get("version") if isinstance(raw, dict) else None
    if isinstance(version, str) and version.strip():
        return version
    return "orchestration-v1"


def _assert_runtime_contract(
    policy: AuthorizationPolicy,
    tools: ToolRegistry,
    models: ModelRegistry,
    harness_roles: set[str],
) -> None:
    missing = sorted(policy.tools() - tools.names())
    if missing:
        raise BootstrapError(f"policy references unregistered tools: {missing}")
    # A harness can contain optional role implementations. Only roles exposed
    # by the active authorization policy need a model binding at startup.
    del harness_roles
    for role in sorted(policy.roles()):
        try:
            models.profile_for_role(role)
        except KeyError as exc:
            message = exc.args[0] if exc.args else f"role {role} has no model binding"
            raise BootstrapError(message) from exc


def _harness_roles() -> HarnessRoleRegistry:
    """Workers this process executes. Add a role here when adding its worker."""
    return HarnessRoleRegistry((RESEARCH, ANALYSIS))


def _scope_id(label: str, value: str) -> str:
    if not isinstance(value, str) or _SCOPE_ID.fullmatch(value) is None:
        raise WorkspaceDenied(f"invalid {label}")
    return value
