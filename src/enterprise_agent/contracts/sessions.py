from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SessionMessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class SessionMessage(BaseModel):
    message_id: str
    role: SessionMessageRole
    content: str
    created_at: datetime
    run_id: str


class SessionSummary(BaseModel):
    session_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    latest_run_id: str | None = None


class SessionView(SessionSummary):
    messages: list[SessionMessage] = Field(default_factory=list)
