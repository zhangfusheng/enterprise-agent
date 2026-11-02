"""LangSmith-shaped trace runs.

A trace is a tree of runs. The root run's id is the trace id. Child runs point
at their parent and share that trace id. ``dotted_order`` sorts a parent before
its children.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator

from enterprise_agent.contracts.runs import RunStatus

TraceStatus = Literal["success", "error", "running", "interrupted", "incomplete"]


def utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes for timestamps originally written in UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class RunType(StrEnum):
    CHAIN = "chain"
    LLM = "llm"
    TOOL = "tool"
    RETRIEVER = "retriever"
    EMBEDDING = "embedding"
    PROMPT = "prompt"
    PARSER = "parser"


class TraceRunView(BaseModel):
    id: str
    trace_id: str
    parent_run_id: str | None = None
    name: str
    run_type: RunType
    start_time: datetime
    end_time: datetime | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    dotted_order: str
    children: list["TraceRunView"] = Field(default_factory=list)
    depth: int = 0
    has_error: bool = False
    incomplete: bool = False

    @field_validator("start_time", "end_time")
    @classmethod
    def normalize_time(cls, value: datetime | None) -> datetime | None:
        return utc(value) if value is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> TraceStatus:
        if self.error:
            return "error"
        if self.outputs.get("interrupted") is True:
            return "interrupted"
        if self.end_time is None:
            return "incomplete" if self.incomplete else "running"
        return "success"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def timing_source(self) -> Literal["measured", "reconstructed"]:
        metadata = self.extra.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("timing_source") == "reconstructed":
            return "reconstructed"
        # Historical message replay spans predate the explicit provenance flag.
        if (self.run_type == RunType.LLM and "requested_tool_calls" in self.inputs) or (
            self.run_type == RunType.TOOL and "tool_call_id" in self.inputs
        ):
            return "reconstructed"
        return "measured"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None or self.timing_source == "reconstructed":
            return None
        return max(0.0, (self.end_time - self.start_time).total_seconds() * 1000)


class TraceSummary(BaseModel):
    run_id: str
    trace_id: str
    root_name: str
    status: TraceStatus
    run_status: RunStatus | None = None
    start_time: datetime
    end_time: datetime | None = None
    duration_ms: float | None = None
    span_count: int
    error_count: int
    has_error: bool
    tags: list[str] = Field(default_factory=list)
    route: str | None = None


class TraceListResponse(BaseModel):
    items: list[TraceSummary] = Field(default_factory=list)
    next_cursor: str | None = None


class TraceView(BaseModel):
    run_id: str
    run_status: RunStatus | None = None
    runs: list[TraceRunView] = Field(default_factory=list)
    roots: list[TraceRunView] = Field(default_factory=list)
