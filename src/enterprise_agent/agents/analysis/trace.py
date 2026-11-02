"""Read the analysis deliverable from the harness tool trace.

The report is the markdown argument of a successful save_report call.
Numbers come from successful calculate results. The closing model text is only
the summary.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

_SAVE = "save_report"
_CALCULATE = "calculate"
_STATUSES = frozenset({"SUCCESS", "FAILED", "UNKNOWN"})
_DUPLICATE_SAVE = "重复保存报告"
_SAVE_FAILED = "报告保存失败"
_SAVE_UNKNOWN = "报告保存结果未知"
_MISSING_RESULT = "missing tool result"


@dataclass(frozen=True)
class CalculationRecord:
    operation: str | None
    value: float | None
    status: str
    error: str | None = None


@dataclass(frozen=True)
class SaveRecord:
    status: str
    markdown: str | None
    artifact_id: str | None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return (
            self.status == "SUCCESS"
            and isinstance(self.artifact_id, str)
            and bool(self.artifact_id)
            and isinstance(self.markdown, str)
            and bool(self.markdown.strip())
        )


@dataclass(frozen=True)
class AnalysisTrace:
    calculations: tuple[CalculationRecord, ...]
    saves: tuple[SaveRecord, ...]
    summary: str
    errors: tuple[str, ...]

    @property
    def unknown_save(self) -> bool:
        return any(item.status == "UNKNOWN" for item in self.saves)

    @property
    def successful_save(self) -> SaveRecord | None:
        for item in self.saves:
            if item.succeeded:
                return item
        return None

    def value_for(self, operation: str) -> float | None:
        for item in self.calculations:
            if item.status == "SUCCESS" and item.operation == operation and item.value is not None:
                return item.value
        return None


def read_trace(messages: Sequence[BaseMessage]) -> AnalysisTrace:
    calculations = tuple(
        _calculation(call, _result(messages, call)) for call in _calls(messages, _CALCULATE)
    )
    saves = tuple(_save(call, _result(messages, call)) for call in _calls(messages, _SAVE))
    return AnalysisTrace(
        calculations=calculations,
        saves=saves,
        summary=_summary(messages),
        errors=tuple(_errors(saves)),
    )


def _calls(messages: Sequence[BaseMessage], name: str) -> list[Mapping[str, Any]]:
    found: list[Mapping[str, Any]] = []
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in message.tool_calls or []:
            if call.get("name") == name:
                found.append(call)
    return found


def _result(messages: Sequence[BaseMessage], call: Mapping[str, Any]) -> dict[str, Any]:
    call_id = str(call.get("id") or "")
    name = call.get("name")
    fallback: ToolMessage | None = None
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if call_id and str(message.tool_call_id or "") == call_id:
            return _json_object(message.content)
        if not call_id and message.name == name and fallback is None:
            fallback = message
    if fallback is not None:
        return _json_object(fallback.content)
    return {"status": "FAILED", "error": _MISSING_RESULT}


def _calculation(call: Mapping[str, Any], payload: Mapping[str, Any]) -> CalculationRecord:
    raw_args = call.get("args")
    args = raw_args if isinstance(raw_args, Mapping) else {}
    operation = args.get("operation")
    status = _status(payload)
    value = payload.get("value")
    if status != "SUCCESS" or isinstance(value, bool) or not isinstance(value, int | float):
        value = None
        if status == "SUCCESS":
            status = "FAILED"
    error = payload.get("error")
    return CalculationRecord(
        operation=operation if isinstance(operation, str) else None,
        value=float(value) if value is not None else None,
        status=status,
        error=error if isinstance(error, str) else None,
    )


def _save(call: Mapping[str, Any], payload: Mapping[str, Any]) -> SaveRecord:
    raw_args = call.get("args")
    args = raw_args if isinstance(raw_args, Mapping) else {}
    markdown = args.get("markdown")
    artifact_id = payload.get("artifact_id")
    error = payload.get("error")
    return SaveRecord(
        status=_status(payload),
        markdown=markdown if isinstance(markdown, str) else None,
        artifact_id=artifact_id if isinstance(artifact_id, str) and artifact_id else None,
        error=error if isinstance(error, str) else None,
    )


def _errors(saves: tuple[SaveRecord, ...]) -> list[str]:
    errors: list[str] = []
    if len(saves) > 1:
        errors.append(_DUPLICATE_SAVE)
    for item in saves:
        if item.status == "UNKNOWN":
            errors.append(item.error or _SAVE_UNKNOWN)
            continue
        if not item.succeeded:
            errors.append(item.error or _SAVE_FAILED)
    return errors


def _status(payload: Mapping[str, Any]) -> str:
    status = payload.get("status")
    if isinstance(status, str) and status in _STATUSES:
        return status
    return "FAILED"


def _summary(messages: Sequence[BaseMessage]) -> str:
    for message in reversed(messages):
        if not isinstance(message, AIMessage):
            continue
        text = _text(message).strip()
        if text:
            return text
    return ""


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, Mapping) and part.get("type") == "text":
            parts.append(str(part.get("text", "")))
    return "\n".join(parts)


def _json_object(content: Any) -> dict[str, Any]:
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {"status": "FAILED", "error": content or "tool failed"}
    elif isinstance(content, Mapping):
        parsed = dict(content)
    else:
        return {"status": "FAILED", "error": "tool result is not json"}
    if isinstance(parsed, dict):
        return parsed
    return {"status": "FAILED", "error": "tool result is not json"}
