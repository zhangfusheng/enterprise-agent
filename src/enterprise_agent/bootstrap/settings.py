from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EA_", env_file=".env", extra="ignore")

    environment: str = "production"
    database_url: str = "sqlite:///./enterprise-agent.db"
    checkpoint_url: str = "sqlite:///./enterprise-agent-checkpoints.sqlite"
    artifact_root: Path = Path("./artifacts")
    offline_mode: bool = True
    # External network access is a separate capability from the model/runtime
    # being online. Offline mode uses deterministic local fixtures; production
    # must opt in explicitly.
    allow_external_network: bool = False
    dev_identity: bool = True
    worker_poll_seconds: float = Field(default=1.0, gt=0)
    lease_seconds: int = Field(default=30, ge=5)
    lease_renew_interval_seconds: int = Field(default=10, ge=1)
    max_claim_attempts: int = Field(default=8, ge=1)
    max_execution_attempts: int = Field(default=5, ge=1)
    max_lease_losses: int = Field(default=5, ge=1)
    max_replans: int = Field(default=2, ge=0)
    max_tasks: int = Field(default=12, ge=1)
    max_agent_depth: int = Field(default=2, ge=0)
    max_agents: int = Field(default=8, ge=1)
    max_model_calls: int = Field(default=40, ge=1)
    max_tool_calls: int = Field(default=80, ge=1)
    max_steps: int = Field(default=100, ge=1)
    max_runtime_seconds: int = Field(default=300, ge=1)
    research_max_search_calls: int = Field(default=3, ge=1)
    research_max_fetch_calls: int = Field(default=8, ge=0)
    research_max_tool_calls: int = Field(default=12, ge=1)
    research_max_duplicate_calls: int = Field(default=1, ge=0)
    research_min_usable_sources: int = Field(default=5, ge=1)
    research_max_candidate_sources: int = Field(default=30, ge=1)
    research_min_content_length: int = Field(default=80, ge=1)
    research_require_fetch: bool = True
    search_primary_provider: str = "tavily"
    search_fallback_providers: str = ""
    search_provider_timeout_seconds: float = Field(default=8.0, gt=0)
    search_provider_retry_count: int = Field(default=1, ge=0, le=3)
    search_provider_retry_backoff_seconds: float = Field(default=0.25, ge=0)
    search_max_results: int = Field(default=10, ge=1, le=20)
    tavily_api_key_ref: str = "TAVILY_API_KEY"
    tavily_search_depth: str = "basic"
    doubao_search_api_key_ref: str = "DOUBAO_SEARCH_API_KEY"
    doubao_search_max_query_chars: int = Field(default=500, ge=1)
    doubao_search_query_rewrite: bool = False
    openai_api_key_ref: str = "OPENAI_API_KEY"
    anthropic_api_key_ref: str = "ANTHROPIC_API_KEY"
    real_tests: bool = False
    log_level: str = "INFO"
    log_file: Path | None = None

    @field_validator("database_url", "checkpoint_url")
    @classmethod
    def reject_blank_urls(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("database and checkpoint URLs must not be blank")
        return value

    @field_validator("log_level")
    @classmethod
    def known_log_level(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized

    @field_validator("search_primary_provider", "tavily_search_depth")
    @classmethod
    def known_search_tokens(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("search provider settings must not be blank")
        return normalized

    @field_validator("search_fallback_providers")
    @classmethod
    def normalize_search_fallbacks(cls, value: str) -> str:
        return ",".join(
            item.strip().lower() for item in value.split(",") if item.strip()
        )

    @field_validator("log_file", mode="before")
    @classmethod
    def blank_log_file_is_unset(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return value

    def validate_startup(self) -> None:
        if self.environment == "production" and self.dev_identity:
            raise ValueError("EA_DEV_IDENTITY=true is not allowed in production")
        if self.environment == "production" and self.offline_mode:
            raise ValueError("EA_OFFLINE_MODE=true is not allowed in production")
        self.artifact_root.mkdir(parents=True, exist_ok=True)

    @property
    def search_fallback_provider_names(self) -> tuple[str, ...]:
        return tuple(
            item.strip().lower()
            for item in self.search_fallback_providers.split(",")
            if item.strip()
        )


def load_local_env(path: Path | None = None) -> None:
    """Publish a dotenv file into the process environment.

    ``Settings`` reads ``EA_*`` into the settings object only. Provider secrets
    stay out of that object and are read later from ``os.environ`` by the
    variable name in ``credential_ref``. Variables already set on the process
    are left unchanged. A missing file is ignored.
    """
    dotenv_path = path if path is not None else Path.cwd() / ".env"
    load_dotenv(dotenv_path, override=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_local_env()
    settings = Settings()
    settings.validate_startup()
    return settings

