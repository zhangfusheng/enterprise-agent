import logging
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ConfigDict

from enterprise_agent.bootstrap.container import BootstrapError, Container
from enterprise_agent.bootstrap.settings import Settings
from enterprise_agent.models.cards import ModelCard
from enterprise_agent.models.catalog import ModelCatalog
from enterprise_agent.models.config import ModelConfig, ModelProfile, ProviderConfig
from enterprise_agent.models.loader import load_model_config
from enterprise_agent.models.parameters.dashscope import DashScopeParameters
from enterprise_agent.models.providers.base import build_chat_model
from enterprise_agent.models.registry import ModelRegistry
from enterprise_agent.models.validation import validate_profile_cards


class _Form(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int
    temperature: float = 1
    voice: str | None = None
    thinking_enable: bool = False
    thinking_budget: int | None = None


def test_from_yaml_trims_schema_and_merges_overrides(tmp_path: Path):
    path = tmp_path / "demo.yaml"
    path.write_text(
        """
name: demo-model
label: Demo
deprecated_at: "2026-01-02T03:04:05Z"
input_types: ["text/plain", "image/*"]
output_types: ["text/plain", "application/x-thinking", "audio/*"]
context_size: 1000
output_size: 200
parameter_overrides:
  temperature:
    default: 0.2
    maximum: 1
    enum: [0, 0.2]
    anyOf:
      - type: number
  max_tokens: null
  voice:
    hidden: true
  not_a_field:
    default: 1
""",
        encoding="utf-8",
    )

    card = ModelCard.from_yaml(path, _Form)

    assert card.name == "demo-model"
    assert card.deprecated_at is not None
    assert card.deprecated_at.isoformat().startswith("2026-01-02T03:04:05")
    properties = card.parameter_schema["properties"]
    assert "max_tokens" not in properties
    assert "voice" not in properties
    assert "not_a_field" not in properties
    assert "thinking_enable" in properties
    assert properties["temperature"]["default"] == 0.2
    assert properties["temperature"]["maximum"] == 1
    assert properties["temperature"]["enum"] == [0, 0.2]
    assert properties["temperature"]["anyOf"] == [{"type": "number"}]
    assert card.parameter_schema["required"] == []


def test_from_yaml_drops_thinking_and_voice_and_caps_max_tokens(tmp_path: Path):
    path = tmp_path / "plain.yaml"
    path.write_text(
        """
name: plain
label: Plain
context_size: 8
output_size: 4
""",
        encoding="utf-8",
    )

    card = ModelCard.from_yaml(path, DashScopeParameters)
    properties = card.parameter_schema["properties"]

    assert card.input_types == ["text/plain"]
    assert card.output_types == ["text/plain"]
    assert card.status == "active"
    for key in ("thinking_enable", "thinking_budget", "thinking_mode", "thinking_display", "voice"):
        assert key not in properties
    assert "parallel_tool_calls" in properties
    assert properties["max_tokens"]["maximum"] == 4


def test_catalog_skips_bad_files_and_duplicate_names(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    directory = tmp_path / "openai_chat"
    directory.mkdir()
    (directory / "b.yaml").write_text(
        "name: zeta\nlabel: Zeta\ncontext_size: 8\noutput_size: 4\n",
        encoding="utf-8",
    )
    (directory / "a.yaml").write_text(
        "name: alpha\nlabel: Alpha\ncontext_size: 8\noutput_size: 4\n",
        encoding="utf-8",
    )
    (directory / "dup.yaml").write_text(
        "name: alpha\nlabel: Duplicate\ncontext_size: 8\noutput_size: 4\n",
        encoding="utf-8",
    )
    (directory / "bad.yaml").write_text("status: sideways\n", encoding="utf-8")
    (directory / "llama.yaml").write_text(
        'name: "llama3.2:latest"\nlabel: Llama\ncontext_size: 8\noutput_size: 4\n',
        encoding="utf-8",
    )

    empty = tmp_path / "empty"
    empty.mkdir()
    with caplog.at_level(logging.WARNING):
        cards = ModelCatalog(tmp_path).list_models("openai_chat")
        overridden = ModelCatalog(tmp_path / "missing").list_models(
            "ollama",
            custom_yaml_dir=directory,
        )
        isolated = ModelCatalog(tmp_path).list_models("openai_chat", custom_yaml_dir=empty)

    assert [card.name for card in cards] == ["alpha", "llama3.2:latest", "zeta"]
    assert [card.label for card in cards if card.name == "alpha"] == ["Alpha"]
    assert [card.name for card in overridden] == ["alpha", "llama3.2:latest", "zeta"]
    assert isolated == []
    assert "bad.yaml" in caplog.text
    assert "dup.yaml" in caplog.text


def test_empty_vendor_directory_returns_no_cards(tmp_path: Path):
    (tmp_path / "gemini").mkdir()
    assert ModelCatalog(tmp_path).list_models("gemini") == []
    assert ModelCatalog(tmp_path).list_models("missing-vendor") == []


def test_shipped_cards_match_configured_models():
    root = Path(__file__).resolve().parents[2]
    catalog = ModelCatalog(root / "configs" / "model")
    openai = catalog.get("openai_chat", "gpt-4o-mini")
    anthropic = catalog.get("anthropic", "claude-3-5-haiku-latest")
    qwen = catalog.get("dashscope", "qwen3.8-max")
    doubao = catalog.get("doubao", "doubao-seed-2-1-pro-260915")
    deepseek = catalog.get("deepseek", "deepseek-v4-flash")

    assert openai.label == "GPT-4o mini"
    assert openai.context_size == 128000
    assert openai.output_size == 16384
    assert anthropic.label == "Claude 3.5 Haiku"
    assert anthropic.input_types == ["text/plain"]
    assert "reasoning_effort" in openai.parameter_schema["properties"]
    assert "thinking_enable" not in openai.parameter_schema["properties"]
    assert qwen.label == "Qwen3.8 Max"
    assert qwen.context_size == 1000000
    assert "thinking_enable" in qwen.parameter_schema["properties"]
    assert "image/*" in qwen.input_types
    assert doubao.label == "Doubao Seed 2.1 Pro"
    assert doubao.context_size == 1048576
    assert doubao.output_size == 262144
    assert "reasoning_effort" in doubao.parameter_schema["properties"]
    assert "thinking_mode" in doubao.parameter_schema["properties"]
    assert [card.name for card in catalog.list_models("dashscope")] == [
        "qwen-plus",
        "qwen3.8-flash",
        "qwen3.8-max",
    ]
    assert [card.name for card in catalog.list_models("doubao")] == [
        "doubao-seed-2-1-lite-260915",
        "doubao-seed-2-1-pro-260915",
        "doubao-seed-2-1-turbo-260628",
    ]
    assert deepseek.label == "DeepSeek V4 Flash"
    assert deepseek.context_size == 1000000
    assert deepseek.output_size == 393216
    assert "image/*" in deepseek.input_types
    assert "thinking_enable" in deepseek.parameter_schema["properties"]
    assert "reasoning_effort" in deepseek.parameter_schema["properties"]
    assert [card.name for card in catalog.list_models("deepseek")] == ["deepseek-v4-flash"]
    validate_profile_cards(load_model_config(root / "configs" / "models.yaml"), catalog)


def test_fake_profile_skips_card_lookup(tmp_path: Path):
    config = _config(protocol="fake", vendor=None, model_name="deterministic-v1")
    validate_profile_cards(config, ModelCatalog(tmp_path))


def test_registry_exposes_and_applies_reasoning_effort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-from-env")
    _write_card(tmp_path, "openai_chat", "gpt-4o-mini")
    registry = ModelRegistry(
        _config(
            protocol="openai_compatible",
            vendor="openai_chat",
            model_name="gpt-4o-mini",
            credential_ref="OPENAI_API_KEY",
        ),
        ModelCatalog(tmp_path),
    )

    assert registry.supported_reasoning_efforts("research") == (
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
    )

    class _Chat:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    from unittest.mock import patch

    with patch("langchain_openai.ChatOpenAI", _Chat):
        model = registry.chat_model_for_role("research", reasoning_effort="high")

    assert model.kwargs["reasoning_effort"] == "high"


def test_unknown_model_and_bad_parameters_fail_closed(tmp_path: Path):
    _write_card(tmp_path, "openai_chat", "gpt-4o-mini")
    catalog = ModelCatalog(tmp_path)
    missing = _config(protocol="openai_compatible", vendor="openai_chat", model_name="gpt-unknown")
    with pytest.raises(Exception, match="not a openai_chat card"):
        validate_profile_cards(missing, catalog)

    invalid = _config(
        protocol="openai_compatible",
        vendor="openai_chat",
        model_name="gpt-4o-mini",
        parameters={"temperature": 9},
    )
    with pytest.raises(Exception, match="parameters are invalid"):
        validate_profile_cards(invalid, catalog)


def test_container_rejects_unknown_model_and_invalid_parameters(tmp_path: Path):
    settings = _settings(tmp_path)
    unknown = _runtime_root(tmp_path / "unknown", model_name="gpt-unknown")
    with pytest.raises(BootstrapError, match="not a openai_chat card"):
        Container(settings, config_root=unknown)

    invalid = _runtime_root(
        tmp_path / "invalid",
        model_name="gpt-4o-mini",
        parameters={"temperature": 9},
        with_card=True,
    )
    with pytest.raises(BootstrapError, match="parameters are invalid"):
        Container(settings, config_root=invalid)

    offline = _runtime_root(tmp_path / "offline", protocol="fake", vendor=None, model_name="x")
    container = Container(settings, config_root=offline)
    try:
        assert container.models.profile_for_role("research").model_name == "x"
    finally:
        container.close()


def test_registry_assigns_input_types_and_leaves_context_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-from-env")
    _write_card(
        tmp_path,
        "openai_chat",
        "gpt-4o-mini",
        extra={"input_types": ["text/plain", "image/*"], "context_size": 128000},
    )
    registry = ModelRegistry(
        _config(
            protocol="openai_compatible",
            vendor="openai_chat",
            model_name="gpt-4o-mini",
            credential_ref="OPENAI_API_KEY",
            endpoint="https://api.openai.com/v1",
        ),
        ModelCatalog(tmp_path),
    )

    class _Chat:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    from unittest.mock import patch

    with patch("langchain_openai.ChatOpenAI", _Chat):
        model = registry.chat_model_for_role("research")

    assert model.input_types == ["text/plain", "image/*"]
    assert not hasattr(model, "context_size")
    assert model.kwargs["temperature"] == 0


def test_parameters_temperature_overrides_profile_temperature(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-from-env")
    constructed: list[dict] = []

    class _Chat:
        def __init__(self, **kwargs: object) -> None:
            constructed.append(kwargs)

    from unittest.mock import patch

    profile = ModelProfile(
        name="openai",
        provider="openai",
        model_name="gpt-4o-mini",
        temperature=0,
        parameters={"temperature": 0.4, "max_tokens": 32, "voice": "alloy"},
        capabilities={"structured_output", "tool_calling"},
    )
    with patch("langchain_openai.ChatOpenAI", _Chat):
        build_chat_model(
            profile,
            ProviderConfig(
                name="openai",
                protocol="openai_compatible",
                endpoint="https://api.openai.com/v1",
                credential_ref="OPENAI_API_KEY",
            ),
        )

    assert constructed[0]["temperature"] == 0.4
    assert constructed[0]["max_completion_tokens"] == 32
    assert constructed[0]["model_kwargs"]["audio"] == {"voice": "alloy", "format": "pcm16"}
    assert constructed[0]["model_kwargs"]["modalities"] == ["text", "audio"]


def test_parallel_tool_calls_are_sent_only_with_tools(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-from-env")

    class _Chat:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def bind_tools(self, tools: list[object], **kwargs: object) -> dict[str, object]:
            return {"tools": tools, "kwargs": kwargs}

    from unittest.mock import patch

    profile = ModelProfile(
        name="dashscope",
        provider="dashscope",
        model_name="qwen-plus",
        parameters={
            "parallel_tool_calls": False,
            "thinking_enable": True,
            "thinking_budget": 16,
            "top_k": 3,
            "max_tokens": 64,
        },
        capabilities={"structured_output", "tool_calling"},
    )
    with patch("langchain_openai.ChatOpenAI", _Chat):
        model = build_chat_model(
            profile,
            ProviderConfig(name="dashscope", protocol="dashscope", credential_ref="DASHSCOPE_API_KEY"),
        )

    assert model.kwargs["max_tokens"] == 64
    assert model.kwargs["extra_body"] == {
        "enable_thinking": True,
        "thinking_budget": 16,
        "top_k": 3,
    }
    bound = model.bind_tools([{"type": "function"}])
    assert bound["kwargs"]["parallel_tool_calls"] is False
    idle = model.bind_tools([])
    assert "parallel_tool_calls" not in idle["kwargs"]


def test_doubao_keeps_the_answer_limit_and_disables_parallel_tools(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("ARK_API_KEY", "ark-from-env")

    class _Chat:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def bind_tools(self, tools: list[object], **kwargs: object) -> dict[str, object]:
            return {"tools": tools, "kwargs": kwargs}

    from unittest.mock import patch

    profile = ModelProfile(
        name="doubao",
        provider="doubao",
        model_name="doubao-seed-2-1-pro-260915",
        parameters={
            "max_tokens": 4096,
            "thinking_enable": True,
            "reasoning_effort": "medium",
            "parallel_tool_calls": False,
        },
        capabilities={"structured_output", "tool_calling"},
    )
    with patch("langchain_openai.ChatOpenAI", _Chat):
        model = build_chat_model(
            profile,
            ProviderConfig(
                name="doubao",
                protocol="doubao",
                vendor="doubao",
                credential_ref="ARK_API_KEY",
            ),
        )

    assert "max_tokens" not in model.kwargs
    assert model.kwargs["reasoning_effort"] == "medium"
    assert model.kwargs["extra_body"] == {
        "thinking": {"type": "enabled"},
        "max_tokens": 4096,
    }
    bound = model.bind_tools([{"type": "function"}])
    assert bound["kwargs"]["parallel_tool_calls"] is False


def _config(
    *,
    protocol: str,
    vendor: str | None,
    model_name: str,
    parameters: dict | None = None,
    credential_ref: str | None = None,
    endpoint: str | None = None,
) -> ModelConfig:
    provider: dict = {"name": "vendor", "protocol": protocol}
    if vendor is not None:
        provider["vendor"] = vendor
    if credential_ref is not None:
        provider["credential_ref"] = credential_ref
    if endpoint is not None:
        provider["endpoint"] = endpoint
    profile: dict = {
        "name": "research",
        "provider": "vendor",
        "model_name": model_name,
        "capabilities": ["structured_output", "tool_calling"],
    }
    if parameters is not None:
        profile["parameters"] = parameters
    return ModelConfig.model_validate(
        {
            "providers": [provider],
            "profiles": [profile],
            "bindings": [{"role": "research", "model_profile": "research"}],
        }
    )


def _write_card(root: Path, vendor: str, name: str, extra: dict | None = None) -> None:
    directory = root / vendor
    directory.mkdir(parents=True, exist_ok=True)
    body = {
        "name": name,
        "label": name,
        "context_size": 128,
        "output_size": 32,
    }
    if extra:
        body.update(extra)
    (directory / f"{name}.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/app.sqlite",
        checkpoint_url=f"sqlite:///{tmp_path}/checkpoint.sqlite",
        artifact_root=tmp_path / "artifacts",
    )


def _runtime_root(
    root: Path,
    *,
    protocol: str = "openai_compatible",
    vendor: str | None = "openai_chat",
    model_name: str = "gpt-4o-mini",
    parameters: dict | None = None,
    with_card: bool = False,
) -> Path:
    root.mkdir()
    (root / "agents.yaml").write_text(
        "roles:\n  research:\n    capabilities: []\n    tools: []\n    can_delegate_to: []\n",
        encoding="utf-8",
    )
    provider = {"name": "vendor", "protocol": protocol, "config_version": "1"}
    if vendor is not None:
        provider["vendor"] = vendor
    if protocol != "fake":
        provider["credential_ref"] = "OPENAI_API_KEY"
        provider["endpoint"] = "https://api.openai.com/v1"
    profile = {
        "name": "research",
        "provider": "vendor",
        "model_name": model_name,
        "capabilities": [],
        "structured_output": False,
        "tool_calling": False,
    }
    if parameters is not None:
        profile["parameters"] = parameters
    (root / "models.yaml").write_text(
        yaml.safe_dump(
            {
                "providers": [provider],
                "profiles": [profile],
                "bindings": [{"role": "research", "model_profile": "research"}],
            }
        ),
        encoding="utf-8",
    )
    if with_card:
        _write_card(root / "model", vendor or "openai_chat", model_name)
    return root
