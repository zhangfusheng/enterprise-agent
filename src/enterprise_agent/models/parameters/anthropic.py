from typing import Literal

from pydantic import BaseModel, ConfigDict


class AnthropicParameters(BaseModel):
    """Form fields for Anthropic chat requests."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    thinking_enable: bool = False
    thinking_budget: int | None = None
    thinking_mode: Literal["adaptive", "enabled", "disabled"] | None = None
    thinking_display: Literal["summarized", "omitted"] | None = None
    reasoning_effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
