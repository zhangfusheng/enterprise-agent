from pydantic import BaseModel, ConfigDict, Field


class OllamaParameters(BaseModel):
    """Form fields for Ollama chat requests."""

    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    thinking_enable: bool = False
