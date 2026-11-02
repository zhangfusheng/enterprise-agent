from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DoubaoParameters(BaseModel):
    """Form fields for Volcengine Ark Doubao chat requests."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    thinking_enable: bool = False
    thinking_mode: Literal["enabled", "disabled", "auto"] | None = None
    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"] | None = (
        None
    )
    parallel_tool_calls: bool = True
