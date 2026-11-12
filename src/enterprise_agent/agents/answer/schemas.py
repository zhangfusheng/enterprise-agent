"""Contracts for the tool-free Answer Agent."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    inputs: list[dict[str, Any]] = Field(default_factory=list)
