from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OpenAIChatParameters(BaseModel):
    """Form fields for the OpenAI Chat Completions API."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = None
    thinking_enable: bool = False
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    parallel_tool_calls: bool = True
    voice: str | None = None
