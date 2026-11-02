from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MoonshotParameters(BaseModel):
    """Form fields for Moonshot chat requests."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = Field(default=None, ge=0, le=1)
    top_p: float | None = None
    thinking_enable: bool = False
    reasoning_effort: Literal["low", "high", "max"] | None = None
