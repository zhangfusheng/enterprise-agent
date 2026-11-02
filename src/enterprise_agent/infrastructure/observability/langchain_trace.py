"""LangChain callback adapter for the enterprise trace tree."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

from enterprise_agent.contracts.traces import RunType
from enterprise_agent.infrastructure.observability.tracing import (
    TraceSpan,
    finish_span,
    preview_messages,
    start_span,
)

logger = logging.getLogger(__name__)


class LangChainTraceHandler(BaseCallbackHandler):
    """Persist LangChain/Deep Agents callback runs under an enterprise span."""

    def __init__(self, root: TraceSpan) -> None:
        self._root = root
        self._spans: dict[str, TraceSpan] = {}

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._start(
            run_id,
            parent_run_id,
            _name(serialized, kwargs.get("name"), metadata, "chain"),
            RunType.CHAIN,
            inputs,
            tags,
            metadata,
        )

    def on_chain_end(self, outputs: object, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        self._finish(run_id, outputs=outputs)

    def on_chain_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        self._finish(run_id, error=error)

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        prompt = messages[0] if messages else []
        self._start(
            run_id,
            parent_run_id,
            _name(serialized, kwargs.get("name"), metadata, "chat_model"),
            RunType.LLM,
            {"messages": preview_messages(prompt)},
            tags,
            metadata,
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._start(
            run_id,
            parent_run_id,
            _name(serialized, kwargs.get("name"), metadata, "llm"),
            RunType.LLM,
            {"prompts": prompts},
            tags,
            metadata,
        )

    def on_llm_end(self, response: object, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        self._finish(run_id, outputs=_llm_outputs(response))

    def on_llm_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        self._finish(run_id, error=error)

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: Any,
        parent_run_id: Any = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self._start(
            run_id,
            parent_run_id,
            _name(serialized, kwargs.get("name"), metadata, "tool"),
            RunType.TOOL,
            inputs or {"input": input_str},
            tags,
            metadata,
        )

    def on_tool_end(self, output: object, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        payload, error = _tool_output(output)
        self._finish(run_id, outputs=payload, error=error)

    def on_tool_error(self, error: BaseException, *, run_id: Any, **kwargs: Any) -> None:
        del kwargs
        self._finish(run_id, error=error)

    def _start(
        self,
        run_id: Any,
        parent_run_id: Any,
        name: str,
        run_type: RunType,
        inputs: object,
        tags: Sequence[str] | None,
        metadata: Mapping[str, Any] | None,
    ) -> None:
        key = str(run_id)
        if key in self._spans:
            return
        try:
            parent = self._spans.get(str(parent_run_id), self._root)
            span = start_span(
                name,
                run_type,
                parent=parent,
                inputs=inputs if isinstance(inputs, Mapping) else {"value": inputs},
                extra={"metadata": dict(metadata or {})},
                tags=tags,
            )
            if span is not None:
                self._spans[key] = span
        except Exception:
            logger.exception("langchain trace start callback failed")

    def _finish(
        self,
        run_id: Any,
        *,
        outputs: object | None = None,
        error: BaseException | str | None = None,
    ) -> None:
        try:
            span = self._spans.pop(str(run_id), None)
            if span is not None:
                if isinstance(error, BaseException) and getattr(
                    error, "expected_control_flow", False
                ):
                    error = None
                finish_span(span, outputs=outputs, error=error)
        except Exception:
            logger.exception("langchain trace finish callback failed")


def _name(
    serialized: Mapping[str, Any] | None,
    callback_name: object,
    metadata: Mapping[str, Any] | None,
    fallback: str,
) -> str:
    for value in (
        callback_name,
        serialized.get("name") if isinstance(serialized, Mapping) else None,
        metadata.get("langgraph_node") if isinstance(metadata, Mapping) else None,
        fallback,
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _llm_outputs(response: object) -> dict[str, Any]:
    messages: list[Any] = []
    for group in getattr(response, "generations", ()) or ():
        if not isinstance(group, Sequence) or isinstance(group, str | bytes):
            continue
        for generation in group:
            message = getattr(generation, "message", None)
            if message is not None:
                messages.append(message)
    output: dict[str, Any] = {"messages": preview_messages(messages)}
    usage = getattr(response, "llm_output", None)
    if isinstance(usage, Mapping) and usage:
        output["llm_output"] = dict(usage)
    return output


def _tool_output(output: object) -> tuple[dict[str, Any], str | None]:
    payload: object = output
    if isinstance(output, str):
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            payload = output
    if isinstance(payload, Mapping):
        result = dict(payload)
        if result.get("ok") is False:
            return result, str(result.get("error") or "tool failed")
        status = str(result.get("status", "")).upper()
        if status in {"FAILED", "UNKNOWN", "ERROR"}:
            return result, str(result.get("error") or "tool failed")
        return result, None
    return {"output": payload}, None


__all__ = ["LangChainTraceHandler"]
