from unittest.mock import patch

import pytest
from pydantic import BaseModel, SecretStr, ValidationError

from enterprise_agent.models.config import ModelProfile, ProviderConfig
from enterprise_agent.models.providers.base import build_chat_model, registered_protocols
from enterprise_agent.models.providers.fake.model import OfflineChatModel


class Output(BaseModel):
    answer: str


class NestedOutput(BaseModel):
    label: str
    count: int
    enabled: bool
    tags: list[str]
    detail: Output


def _profile(provider: str, model_name: str) -> ModelProfile:
    return ModelProfile(
        name=f"{provider}-profile",
        provider=provider,
        model_name=model_name,
        capabilities={"structured_output", "tool_calling"},
    )


def _provider(
    protocol: str,
    *,
    name: str | None = None,
    endpoint: str | None = None,
    credential_ref: str | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        name=name or protocol,
        protocol=protocol,
        endpoint=endpoint,
        credential_ref=credential_ref,
    )


def _fake() -> OfflineChatModel:
    model = build_chat_model(_profile("offline", "deterministic-v1"), _provider("fake"))
    assert isinstance(model, OfflineChatModel)
    return model


def test_fake_model_echoes_last_user_text():
    model = _fake()
    echoed = model.invoke("什么是幂等性？")
    assert echoed.content == "离线模型已处理：什么是幂等性？"

    blank = model.invoke("")
    assert blank.content == "已按离线确定性模型处理请求。"

    long_text = "字" * 600
    truncated = model.invoke(long_text)
    assert truncated.content == f"离线模型已处理：{'字' * 500}"


def test_fake_model_structured_output_uses_schema_defaults():
    result = _fake().with_structured_output(Output).invoke("hello")
    assert isinstance(result, Output)
    assert result.answer == ""


def test_fake_model_fills_nested_tool_arguments():
    message = _fake().bind_tools([NestedOutput], tool_choice="NestedOutput").invoke("hello")
    assert message.tool_calls[0]["args"] == {
        "label": "",
        "count": 0,
        "enabled": False,
        "tags": [],
        "detail": {"answer": ""},
    }


def test_unknown_tool_choice_lists_available_names():
    tools = [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    with pytest.raises(ValueError, match="web_search") as caught:
        _fake().bind_tools(tools, tool_choice="missing").invoke("hello")
    assert "missing" in str(caught.value)


def test_missing_credential_names_the_variable_only(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    secret = "sk-test-secret-value"
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY") as caught:
        build_chat_model(
            _profile("openai", "gpt-4o-mini"),
            _provider(
                "openai_compatible",
                endpoint="https://api.openai.com/v1",
                credential_ref="OPENAI_API_KEY",
            ),
        )
    message = str(caught.value)
    assert secret not in message
    assert "sk-" not in message


def test_unknown_protocol_lists_registered_names():
    with pytest.raises(ValueError, match="registered protocols") as caught:
        build_chat_model(_profile("custom", "x"), _provider("not-a-protocol"))
    message = str(caught.value)
    for protocol in ("openai_compatible", "anthropic", "dashscope", "doubao", "deepseek", "fake"):
        assert protocol in message


def test_registry_resolves_builtin_protocols():
    assert set(registered_protocols()) >= {
        "openai_compatible",
        "anthropic",
        "dashscope",
        "doubao",
        "deepseek",
        "fake",
    }


def test_openai_and_dashscope_receive_config_and_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-from-env")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-from-env")
    constructed: list[dict] = []

    class _Chat:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    with patch("langchain_openai.ChatOpenAI", _Chat):
        build_chat_model(
            _profile("openai", "gpt-4o-mini"),
            _provider(
                "openai_compatible",
                endpoint="https://api.openai.com/v1",
                credential_ref="OPENAI_API_KEY",
            ),
        )
        build_chat_model(
            _profile("dashscope", "qwen-plus"),
            _provider(
                "dashscope",
                endpoint="https://dashscope.example/v1",
                credential_ref="DASHSCOPE_API_KEY",
            ),
        )
        build_chat_model(
            _profile("dashscope", "qwen-plus"),
            _provider("dashscope", credential_ref="DASHSCOPE_API_KEY"),
        )

    assert constructed[0]["model"] == "gpt-4o-mini"
    assert _secret(constructed[0]["api_key"]) == "openai-from-env"
    assert constructed[0]["base_url"] == "https://api.openai.com/v1"
    assert constructed[1]["model"] == "qwen-plus"
    assert _secret(constructed[1]["api_key"]) == "dashscope-from-env"
    assert constructed[1]["base_url"] == "https://dashscope.example/v1"
    assert constructed[2]["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_doubao_receives_config_and_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ARK_API_KEY", "ark-from-env")
    constructed: list[dict] = []

    class _Chat:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    with patch("langchain_openai.ChatOpenAI", _Chat):
        build_chat_model(
            _profile("doubao", "doubao-seed-2-1-pro-260915"),
            _provider(
                "doubao",
                endpoint="https://ark.example/api/v3",
                credential_ref="ARK_API_KEY",
            ),
        )
        build_chat_model(
            _profile("doubao", "doubao-seed-2-1-lite-260915"),
            _provider("doubao", credential_ref="ARK_API_KEY"),
        )

    assert constructed[0]["model"] == "doubao-seed-2-1-pro-260915"
    assert _secret(constructed[0]["api_key"]) == "ark-from-env"
    assert constructed[0]["base_url"] == "https://ark.example/api/v3"
    assert constructed[0]["extra_body"]["thinking"] == {"type": "disabled"}
    assert constructed[1]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert "max_tokens" not in constructed[1]


def test_deepseek_receives_config_and_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-from-env")
    constructed: list[dict] = []

    class _Chat:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    with patch("langchain_openai.ChatOpenAI", _Chat):
        build_chat_model(
            _profile("deepseek", "deepseek-v4-flash"),
            _provider(
                "deepseek",
                endpoint="https://api.deepseek.example",
                credential_ref="DEEPSEEK_API_KEY",
            ),
        )
        build_chat_model(
            _profile("deepseek", "deepseek-v4-flash"),
            _provider("deepseek", credential_ref="DEEPSEEK_API_KEY"),
        )

    assert constructed[0]["model"] == "deepseek-v4-flash"
    assert _secret(constructed[0]["api_key"]) == "deepseek-from-env"
    assert constructed[0]["base_url"] == "https://api.deepseek.example"
    assert constructed[0]["temperature"] == 0
    assert constructed[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert constructed[0]["disabled_params"] == {"parallel_tool_calls": None}
    assert constructed[1]["base_url"] == "https://api.deepseek.com"


def test_deepseek_structured_output_uses_a_tool_call(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-from-env")
    model = build_chat_model(
        _profile("deepseek", "deepseek-v4-flash"),
        _provider("deepseek", credential_ref="DEEPSEEK_API_KEY"),
    )
    bound = model.with_structured_output(Output)
    request = bound.steps[0].kwargs
    assert request["tool_choice"] == {"type": "function", "function": {"name": "Output"}}
    assert "response_format" not in request
    assert "parallel_tool_calls" not in request


def test_anthropic_receives_config_and_environment(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-from-env")
    constructed: list[dict] = []

    class _Chat:
        def __init__(self, **kwargs):
            constructed.append(kwargs)

    with patch("langchain_anthropic.ChatAnthropic", _Chat):
        build_chat_model(
            _profile("anthropic", "claude-3-5-haiku-latest"),
            _provider(
                "anthropic",
                endpoint="https://api.anthropic.com",
                credential_ref="ANTHROPIC_API_KEY",
            ),
        )

    assert constructed[0]["model_name"] == "claude-3-5-haiku-latest"
    assert _secret(constructed[0]["api_key"]) == "anthropic-from-env"
    assert constructed[0]["base_url"] == "https://api.anthropic.com"
    assert constructed[0]["temperature"] == 0
    assert constructed[0]["max_tokens"] == 8192
    assert constructed[0]["thinking"] == {"type": "disabled"}


def _secret(value: SecretStr | str) -> str:
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def test_credential_ref_rejects_a_secret():
    with pytest.raises(ValidationError):
        ProviderConfig(name="openai", protocol="openai_compatible", credential_ref="sk-live")
