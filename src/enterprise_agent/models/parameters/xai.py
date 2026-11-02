from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class XAIParameters(BaseModel):
    """Form fields for xAI chat requests."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = None
    thinking_enable: bool = False
    reasoning_effort: Literal["low", "medium", "high"] | None = None
