from typing import Literal

from pydantic import BaseModel, ConfigDict


class OpenAIResponseParameters(BaseModel):
    """Form fields for the OpenAI Responses API."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = None
    thinking_enable: bool = False
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
