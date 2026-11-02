from pydantic import BaseModel, ConfigDict, Field


class GeminiParameters(BaseModel):
    """Form fields for Gemini chat requests."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = None
    thinking_enable: bool = False
    thinking_budget: int | None = None
