from pydantic import BaseModel, ConfigDict, Field


class DashScopeParameters(BaseModel):
    """Form fields for DashScope chat requests."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = Field(default=None, ge=0, lt=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    top_k: int | None = Field(default=None, ge=1, le=100)
    thinking_enable: bool = False
    thinking_budget: int | None = None
    parallel_tool_calls: bool = True
    voice: str | None = None
