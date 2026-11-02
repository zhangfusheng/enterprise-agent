"""In-process trace tree, persisted by the active sink.

The shape follows LangSmith runs: a root run id is the trace id, children set
``parent_run_id``, and ``run_type`` is chain, llm, or tool. Spans are written
when they open and updated when they close, so a finished flow can be read back
as a tree. User text is truncated and secrets are redacted before it is stored.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from enterprise_agent.contracts.traces import RunType, TraceRunView, TraceView
from enterprise_agent.infrastructure.observability.logging import redact

logger = logging.getLogger(__name__)

_TEXT_LIMIT = 2000
_ERROR_LIMIT = 500
_COLLECTION_LIMIT = 20
_DEPTH_LIMIT = 6
_RUN_TYPES = frozenset(item.value for item in RunType)
_MESSAGE_PREVIEW = 8


class TraceSink(Protocol):
    def record(self, span: TraceSpan) -> None: ...


@dataclass
class TraceSpan:
    id: str
    trace_id: str
    parent_run_id: str | None
    name: str
    run_type: str
    start_time: datetime
    dotted_order: str
    end_time: datetime | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    run_id: str | None = None
    tenant_id: str | None = None


@dataclass(frozen=True)
class TraceScope:
    sink: TraceSink
    run_id: str | None
    tenant_id: str | None


_SCOPE: ContextVar[TraceScope | None] = ContextVar("ea_trace_scope", default=None)
_STACK: ContextVar[tuple[TraceSpan, ...]] = ContextVar("ea_trace_stack", default=())


@contextmanager
def tracing_scope(
    sink: TraceSink | None,
    *,
    run_id: str | None = None,
    tenant_id: str | None = None,
) -> Iterator[TraceScope | None]:
    """Bind a sink for the current context. An outer scope wins."""
    current = _SCOPE.get()
    if current is not None:
        yield current
        return
    if sink is None:
        yield None
        return
    scope = TraceScope(sink, run_id, tenant_id)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


@contextmanager
def trace(
    name: str,
    run_type: RunType | str = RunType.CHAIN,
    *,
    inputs: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    start_time: datetime | None = None,
) -> Iterator[TraceSpan]:
    """Open a child of the current run. Without a scope the body still runs."""
    scope = _SCOPE.get()
    if scope is None:
        yield TraceSpan(
            id="",
            trace_id="",
            parent_run_id=None,
            name=name or "run",
            run_type=_run_type(run_type),
            start_time=start_time or datetime.now(UTC),
            dotted_order="",
        )
        return
    parent = _current()
    span = _start_span(scope, parent, name, run_type, inputs, extra, tags, start_time)
    token = _push(span)
    _safe_record(scope, span)
    try:
        yield span
    except BaseException as exc:
        if _is_graph_bubble(exc):
            if not span.outputs:
                span.outputs = {"interrupted": True}
        elif span.error is None:
            span.error = _error_text(exc)
        raise
    finally:
        _pop(token)
        span.end_time = datetime.now(UTC)
        _safe_record(scope, span)


def start_span(
    name: str,
    run_type: RunType | str = RunType.CHAIN,
    *,
    parent: TraceSpan | None = None,
    inputs: Mapping[str, Any] | None = None,
    extra: Mapping[str, Any] | None = None,
    tags: Sequence[str] | None = None,
    start_time: datetime | None = None,
) -> TraceSpan | None:
    """Start a span whose lifecycle is managed by an external callback.

    ``trace`` is the preferred API for synchronous scopes. Callback systems
    report start and end in separate callbacks, so they need an explicit
    parent and a public lifecycle pair instead of holding a context manager.
    """
    scope = _SCOPE.get()
    if scope is None:
        return None
    span = _start_span(
        scope,
        parent if parent is not None else _current(),
        name,
        run_type,
        inputs,
        extra,
        tags,
        start_time,
    )
    _safe_record(scope, span)
    return span


def finish_span(
    span: TraceSpan,
    *,
    outputs: object | None = None,
    error: BaseException | str | None = None,
) -> None:
    """Finish a span created by :func:`start_span`."""
    scope = _SCOPE.get()
    if scope is None or not span.id or span.end_time is not None:
        return
    if outputs is not None:
        span.outputs = dict(outputs) if isinstance(outputs, Mapping) else {"value": outputs}
    if error is not None:
        if isinstance(error, str):
            span.error = _clip(redact(error), _ERROR_LIMIT)
        elif _is_graph_bubble(error):
            span.outputs.setdefault("interrupted", True)
        else:
            span.error = _error_text(error)
    span.end_time = datetime.now(UTC)
    _safe_record(scope, span)


def record_messages(messages: Sequence[BaseMessage], *, start_time: datetime | None = None) -> None:
    """Record model turns and tool calls under the current chain.

    Timestamps are completion order. The provider does not stamp each message,
    so each child starts one microsecond after the previous one.
    """
    if _SCOPE.get() is None:
        return
    try:
        _record_messages(messages, start_time or datetime.now(UTC))
    except Exception:
        logger.exception("message trace was not recorded")


def preview_messages(messages: Sequence[object]) -> list[dict[str, object]]:
    preview: list[dict[str, object]] = []
    for message in list(messages)[:_MESSAGE_PREVIEW]:
        preview.append(
            {
                "type": str(getattr(message, "type", type(message).__name__)),
                "content": getattr(message, "content", ""),
            }
        )
    return preview


def build_trace_view(run_id: str, spans: Sequence[TraceSpan]) -> TraceView:
    ordered = sorted(spans, key=lambda item: item.dotted_order)
    views = [_view(span) for span in ordered if span.id]
    children: dict[str, list[TraceRunView]] = {view.id: [] for view in views}
    roots: list[TraceRunView] = []
    known = set(children)
    for view in views:
        parent = view.parent_run_id
        if parent is not None and parent in known:
            children[parent].append(view)
        else:
            roots.append(view)
    by_id = {view.id: view for view in views}
    for view in views:
        visited = {view.id}
        ancestor = by_id.get(view.parent_run_id or "")
        while ancestor is not None and ancestor.id not in visited:
            view.depth += 1
            visited.add(ancestor.id)
            ancestor = by_id.get(ancestor.parent_run_id or "")
        view.has_error = bool(view.error)
    for view in sorted(views, key=lambda item: item.depth, reverse=True):
        if view.has_error and view.parent_run_id in by_id:
            by_id[view.parent_run_id].has_error = True
    return TraceView(
        run_id=run_id,
        runs=views,
        roots=[_nest(view, children) for view in roots],
    )


def _record_messages(messages: Sequence[BaseMessage], clock: datetime) -> None:
    calls: dict[str, Mapping[str, Any]] = {}
    for message in messages:
        clock = clock + timedelta(microseconds=1)
        if isinstance(message, AIMessage):
            requested = _tool_calls(message)
            for call in requested:
                call_id = call.get("id")
                if isinstance(call_id, str) and call_id:
                    calls[call_id] = call
            with trace(
                _model_name(message),
                RunType.LLM,
                inputs={"requested_tool_calls": requested},
                extra=_usage_extra(message),
                start_time=clock,
            ) as span:
                span.outputs = {"content": message.content, "tool_calls": requested}
            continue
        if not isinstance(message, ToolMessage):
            continue
        call_id = str(message.tool_call_id or "")
        matched = calls.get(call_id)
        tool_name = matched.get("name") if matched is not None else None
        name = message.name or (str(tool_name) if tool_name else "tool")
        payload = _tool_payload(message.content)
        with trace(
            name,
            RunType.TOOL,
            inputs=_tool_inputs(call_id, matched),
            extra={"metadata": {"timing_source": "reconstructed"}},
            start_time=clock,
        ) as span:
            span.outputs = payload
            failure = _tool_error(message, payload)
            if failure is not None:
                span.error = failure


def _start_span(
    scope: TraceScope,
    parent: TraceSpan | None,
    name: str,
    run_type: RunType | str,
    inputs: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None,
    tags: Sequence[str] | None,
    start_time: datetime | None,
) -> TraceSpan:
    started = start_time or datetime.now(UTC)
    span_id = str(uuid4())
    trace_id = parent.trace_id if parent is not None else span_id
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    current = f"{stamp}{span_id}"
    dotted = f"{parent.dotted_order}.{current}" if parent is not None else current
    return TraceSpan(
        id=span_id,
        trace_id=trace_id,
        parent_run_id=None if parent is None else parent.id,
        name=(name or "run").strip() or "run",
        run_type=_run_type(run_type),
        start_time=started,
        dotted_order=dotted,
        inputs=dict(inputs or {}),
        extra=_extra(scope, extra),
        tags=[str(item) for item in (tags or ()) if str(item).strip()][:_COLLECTION_LIMIT],
        run_id=scope.run_id,
        tenant_id=scope.tenant_id,
    )


def _extra(scope: TraceScope, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    merged = dict(extra or {})
    metadata = merged.get("metadata")
    stored = dict(metadata) if isinstance(metadata, Mapping) else {}
    if scope.run_id:
        stored.setdefault("run_id", scope.run_id)
    if scope.tenant_id:
        stored.setdefault("tenant_id", scope.tenant_id)
    merged["metadata"] = stored
    return merged


def _freeze(span: TraceSpan) -> None:
    span.inputs = _object(span.inputs)
    span.outputs = _object(span.outputs)
    span.extra = _object(span.extra)
    span.tags = [str(item) for item in span.tags][:_COLLECTION_LIMIT]
    if span.error is not None:
        span.error = _clip(redact(span.error), _ERROR_LIMIT)


def _safe_record(scope: TraceScope, span: TraceSpan) -> None:
    try:
        _freeze(span)
        scope.sink.record(span)
    except Exception:
        logger.exception("trace span %s was not saved", span.id)


def _current() -> TraceSpan | None:
    stack = _STACK.get()
    if not stack:
        return None
    return stack[-1]


def _push(span: TraceSpan) -> Token[tuple[TraceSpan, ...]]:
    return _STACK.set((*_STACK.get(), span))


def _pop(token: Token[tuple[TraceSpan, ...]]) -> None:
    _STACK.reset(token)


def _view(span: TraceSpan) -> TraceRunView:
    return TraceRunView(
        id=span.id,
        trace_id=span.trace_id,
        parent_run_id=span.parent_run_id,
        name=_clip(redact(span.name)),
        run_type=RunType(span.run_type),
        start_time=span.start_time,
        end_time=span.end_time,
        inputs=_object(span.inputs),
        outputs=_object(span.outputs),
        error=_clip(redact(span.error), _ERROR_LIMIT) if span.error else None,
        extra=_object(span.extra),
        tags=[_clip(redact(tag)) for tag in span.tags],
        dotted_order=span.dotted_order,
    )


def _nest(view: TraceRunView, children: Mapping[str, Sequence[TraceRunView]]) -> TraceRunView:
    return view.model_copy(
        update={"children": [_nest(child, children) for child in children[view.id]]}
    )


def _run_type(value: RunType | str) -> str:
    text = str(value)
    if text in _RUN_TYPES:
        return text
    return RunType.CHAIN.value


def _is_graph_bubble(exc: BaseException) -> bool:
    return any(item.__name__ == "GraphBubbleUp" for item in type(exc).__mro__)


def _error_text(exc: BaseException) -> str:
    return _clip(redact(f"{type(exc).__name__}: {exc}"), _ERROR_LIMIT)


def _tool_calls(message: AIMessage) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []
    for call in message.tool_calls or []:
        recorded.append(
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "args": call.get("args"),
            }
        )
    return recorded


def _tool_inputs(call_id: str, call: Mapping[str, Any] | None) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    if call_id:
        inputs["tool_call_id"] = call_id
    if call is not None and "args" in call:
        inputs["arguments"] = call.get("args")
    return inputs


def _tool_payload(content: object) -> dict[str, Any]:
    if isinstance(content, str):
        parsed = _json_object(content)
        if parsed is not None:
            return parsed
        return {"content": content}
    if isinstance(content, Mapping):
        return dict(content)
    return {"content": content}


def _json_object(content: str) -> dict[str, Any] | None:
    import json

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return {"value": parsed}


def _tool_error(message: ToolMessage, payload: Mapping[str, Any]) -> str | None:
    if getattr(message, "status", None) == "error":
        return str(payload.get("error") or payload.get("content") or "tool failed")
    if payload.get("ok") is False:
        return str(payload.get("error") or "tool failed")
    return None


def _model_name(message: AIMessage) -> str:
    metadata = message.response_metadata if isinstance(message.response_metadata, Mapping) else {}
    model = metadata.get("model_name") or metadata.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return "chat_model"


def _usage_extra(message: AIMessage) -> dict[str, Any]:
    usage = getattr(message, "usage_metadata", None)
    metadata: dict[str, Any] = {"timing_source": "reconstructed"}
    if usage:
        metadata["usage"] = usage
    return {"metadata": metadata}


def _object(value: object) -> dict[str, Any]:
    stored = jsonable(value)
    if isinstance(stored, dict):
        return stored
    return {"value": stored}


def jsonable(value: object, depth: int = 0) -> Any:
    if depth > _DEPTH_LIMIT:
        return "..."
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if value != value or value in {float("inf"), float("-inf")}:
            return None
        return value
    if isinstance(value, str):
        return _clip(redact(value))
    if isinstance(value, bytes):
        return _clip(redact(value.decode("utf-8", "replace")))
    if isinstance(value, datetime):
        return value.isoformat()
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return jsonable(dump(mode="json"), depth + 1)
    if isinstance(value, Mapping):
        items = list(value.items())[:_COLLECTION_LIMIT]
        return {
            str(key): "[REDACTED]" if _secret_key(str(key)) else jsonable(item, depth + 1)
            for key, item in items
        }
    if isinstance(value, Sequence):
        return [jsonable(item, depth + 1) for item in list(value)[:_COLLECTION_LIMIT]]
    return _clip(redact(str(value)))


def _clip(value: str, limit: int = _TEXT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _secret_key(key: str) -> bool:
    return key.lower().replace("-", "_") in {
        "api_key",
        "apikey",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "client_secret",
        "authorization",
        "cookie",
        "set_cookie",
    }
