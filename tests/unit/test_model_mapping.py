from enterprise_agent.models.mapping import (
    map_anthropic,
    map_deepseek,
    map_doubao,
    map_gemini,
    map_moonshot,
    map_ollama,
    map_openai_chat,
    map_openai_responses,
    map_xai,
)
from enterprise_agent.models.parameters.anthropic import AnthropicParameters
from enterprise_agent.models.parameters.deepseek import DeepSeekParameters
from enterprise_agent.models.parameters.doubao import DoubaoParameters
from enterprise_agent.models.parameters.gemini import GeminiParameters
from enterprise_agent.models.parameters.moonshot import MoonshotParameters
from enterprise_agent.models.parameters.ollama import OllamaParameters
from enterprise_agent.models.parameters.openai_chat import OpenAIChatParameters
from enterprise_agent.models.parameters.openai_response import OpenAIResponseParameters
from enterprise_agent.models.parameters.xai import XAIParameters


def test_anthropic_defaults_and_expands_budget():
    defaults = map_anthropic(AnthropicParameters())
    assert defaults["max_tokens"] == 8192
    assert defaults["thinking"] == {"type": "disabled"}
    assert "output_config" not in defaults

    half = map_anthropic(AnthropicParameters(max_tokens=100, thinking_enable=True))
    assert half["max_tokens"] == 100
    assert half["thinking"] == {"type": "enabled", "budget_tokens": 50}

    expanded = map_anthropic(
        AnthropicParameters(max_tokens=100, thinking_enable=True, thinking_budget=100)
    )
    assert expanded["max_tokens"] == 1124
    assert expanded["thinking"]["budget_tokens"] == 100

    adaptive = map_anthropic(
        AnthropicParameters(
            thinking_enable=True,
            thinking_mode="adaptive",
            thinking_display="summarized",
            reasoning_effort="high",
        )
    )
    assert adaptive["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert adaptive["output_config"] == {"effort": "high"}

    hidden = map_anthropic(
        AnthropicParameters(thinking_display="omitted", thinking_mode="disabled")
    )
    assert hidden["thinking"] == {"type": "disabled"}


def test_doubao_thinking_and_answer_limit():
    disabled = map_doubao(DoubaoParameters(max_tokens=128, temperature=0))
    assert disabled["temperature"] == 0
    assert disabled["extra_body"] == {
        "thinking": {"type": "disabled"},
        "max_tokens": 128,
    }
    assert "max_tokens" not in disabled

    enabled = map_doubao(
        DoubaoParameters(thinking_enable=True, reasoning_effort="high"),
        tools=True,
    )
    assert enabled["reasoning_effort"] == "high"
    assert enabled["extra_body"]["thinking"] == {"type": "enabled"}
    assert "parallel_tool_calls" not in enabled

    automatic = map_doubao(
        DoubaoParameters(thinking_enable=True, thinking_mode="auto", parallel_tool_calls=False),
        tools=True,
    )
    assert automatic["extra_body"]["thinking"] == {"type": "auto"}
    assert automatic["parallel_tool_calls"] is False


def test_openai_chat_sends_reasoning_effort_only_when_both_fields_are_set():
    assert "reasoning_effort" not in map_openai_chat(OpenAIChatParameters(thinking_enable=True))
    mapped = map_openai_chat(
        OpenAIChatParameters(thinking_enable=False, reasoning_effort="low"),
        tools=True,
    )
    assert mapped["reasoning_effort"] == "low"
    assert "parallel_tool_calls" not in mapped
    disabled = map_openai_chat(
        OpenAIChatParameters(parallel_tool_calls=False),
        tools=True,
    )
    assert disabled["parallel_tool_calls"] is False
    assert "parallel_tool_calls" not in map_openai_chat(
        OpenAIChatParameters(parallel_tool_calls=False)
    )


def test_openai_responses_drop_audio_fields():
    mapped = map_openai_responses(
        OpenAIResponseParameters(max_tokens=20, temperature=0.5, reasoning_effort="minimal")
    )
    assert mapped == {
        "max_output_tokens": 20,
        "temperature": 0.5,
        "reasoning": {"effort": "minimal"},
    }
    assert "modalities" not in mapped
    assert "audio" not in mapped


def test_gemini_thinking_config():
    enabled = map_gemini(GeminiParameters(max_tokens=10, thinking_enable=True))
    assert enabled["max_output_tokens"] == 10
    assert enabled["thinking_config"] == {"include_thoughts": True, "thinking_budget": 1024}

    budgeted = map_gemini(GeminiParameters(thinking_enable=True, thinking_budget=8))
    assert budgeted["thinking_config"]["thinking_budget"] == 8

    disabled = map_gemini(GeminiParameters(thinking_enable=False))
    assert disabled["thinking_config"] == {"include_thoughts": False, "thinking_budget": 0}


def test_deepseek_thinking_type():
    enabled = map_deepseek(DeepSeekParameters(thinking_enable=True, reasoning_effort="high"))
    assert enabled["reasoning_effort"] == "high"
    assert enabled["extra_body"] == {"thinking": {"type": "enabled"}}
    disabled = map_deepseek(DeepSeekParameters())
    assert disabled["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in disabled


def test_moonshot_kimi_k3_uses_completion_tokens():
    kimi = map_moonshot(
        MoonshotParameters(max_tokens=30, reasoning_effort="max", thinking_enable=True),
        model_name="kimi-k3",
    )
    assert kimi["max_completion_tokens"] == 30
    assert kimi["reasoning_effort"] == "max"
    assert "extra_body" not in kimi
    assert "max_tokens" not in kimi

    other = map_moonshot(
        MoonshotParameters(max_tokens=30, thinking_enable=True, reasoning_effort="low"),
        model_name="moonshot-v1",
    )
    assert other["max_tokens"] == 30
    assert other["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "reasoning_effort" not in other
    assert "max_completion_tokens" not in other


def test_xai_requires_both_thinking_fields():
    assert "reasoning_effort" not in map_xai(XAIParameters(thinking_enable=True))
    mapped = map_xai(XAIParameters(thinking_enable=False, reasoning_effort="medium", max_tokens=5))
    assert mapped["reasoning_effort"] == "medium"
    assert mapped["max_tokens"] == 5


def test_ollama_options():
    mapped = map_ollama(OllamaParameters(max_tokens=12, temperature=0.3, thinking_enable=True))
    assert mapped == {"think": True, "options": {"num_predict": 12, "temperature": 0.3}}
