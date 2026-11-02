"""Per-run model metrics.

Token counts, latency, and cost come from the call that actually ran.
Structured-output failures and server fallbacks are counted separately.
Prices are optional profile parameters, ``input_usd_per_million`` and
``output_usd_per_million``. A priced call without those values has unknown cost.
An offline model is free.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass
from typing import Literal

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import ValidationError

CallOutcome = Literal["success", "structured_output_failure", "error"]

_INPUT_PRICE = "input_usd_per_million"
_OUTPUT_PRICE = "output_usd_per_million"


@dataclass(frozen=True, slots=True)
class CallObservation:
    latency_ms: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    structured_output_failure: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class ModelCall:
    stage: str
    role: str
    model_version: str | None
    prompt_version: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float | None
    retries: int
    outcome: CallOutcome


class RunMetrics:
    def __init__(self) -> None:
        self.calls: list[ModelCall] = []
        self.fallbacks: list[str] = []
        self.retries = 0

    def add_call(self, call: ModelCall) -> None:
        self.calls.append(call)

    def add_fallback(self, stage: str) -> None:
        self.fallbacks.append(stage)

    def add_retry(self) -> None:
        self.retries += 1

    def snapshot(self) -> dict[str, object]:
        model_calls = len(self.calls)
        structured = sum(1 for call in self.calls if call.outcome == "structured_output_failure")
        costs = [call.cost_usd for call in self.calls]
        cost: float | None = None if any(item is None for item in costs) else float(sum(item or 0 for item in costs))
        return {
            "model_calls": model_calls,
            "structured_output_failures": structured,
            "fallbacks": len(self.fallbacks),
            "retries": self.retries,
            "input_tokens": sum(call.input_tokens for call in self.calls),
            "output_tokens": sum(call.output_tokens for call in self.calls),
            "latency_ms": sum(call.latency_ms for call in self.calls),
            "cost_usd": cost,
            "structured_output_failure_rate": _rate(structured, model_calls),
            "fallback_rate": _rate(len(self.fallbacks), model_calls),
            "calls": [asdict(call) for call in self.calls],
        }


class UsageCapture(BaseCallbackHandler):
    """Read token usage from a chat-model callback without changing the response."""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0

    def on_llm_end(self, response: object, **kwargs: object) -> None:
        del kwargs
        generations = getattr(response, "generations", None) or []
        for group in generations:
            if not isinstance(group, list):
                continue
            for generation in group:
                message = getattr(generation, "message", None)
                if message is None:
                    continue
                incoming, outgoing = usage_tokens(message)
                self.input_tokens += incoming
                self.output_tokens += outgoing
        if self.input_tokens or self.output_tokens:
            return
        llm_output = getattr(response, "llm_output", None)
        if not isinstance(llm_output, Mapping):
            return
        token_usage = llm_output.get("token_usage")
        if not isinstance(token_usage, Mapping):
            return
        self.input_tokens = _token(token_usage.get("prompt_tokens") or token_usage.get("input_tokens"))
        self.output_tokens = _token(token_usage.get("completion_tokens") or token_usage.get("output_tokens"))


_METRICS: ContextVar[RunMetrics | None] = ContextVar("run_metrics", default=None)


def begin_run_metrics() -> Token[RunMetrics | None]:
    return _METRICS.set(RunMetrics())


def reset_run_metrics(token: Token[RunMetrics | None]) -> None:
    _METRICS.reset(token)


def metrics_snapshot() -> dict[str, object]:
    metrics = _METRICS.get()
    if metrics is None:
        return RunMetrics().snapshot()
    return metrics.snapshot()


def record_model_call(
    *,
    stage: str,
    role: str,
    model_version: str | None,
    prompt_version: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    cost_usd: float | None,
    outcome: CallOutcome,
) -> None:
    metrics = _METRICS.get()
    if metrics is None:
        return
    metrics.add_call(
        ModelCall(
            stage=stage,
            role=role,
            model_version=model_version,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost_usd,
            retries=0,
            outcome=outcome,
        )
    )


def record_fallback(stage: str) -> None:
    metrics = _METRICS.get()
    if metrics is None:
        return
    metrics.add_fallback(stage)


def record_retry() -> None:
    metrics = _METRICS.get()
    if metrics is None:
        return
    metrics.add_retry()


def call_outcome(exc: Exception, *, produced: bool) -> CallOutcome:
    """Classify a failed model step.

    A schema or parser failure is a structured-output failure. A candidate that
    the server rejects still counts as a successful model call. Anything else
    that never produced a candidate is a transport or runtime error.
    """
    if is_structured_output_failure(exc):
        return "structured_output_failure"
    if produced:
        return "success"
    return "error"


def is_structured_output_failure(exc: Exception) -> bool:
    if isinstance(exc, (ValidationError, TypeError)):
        return True
    return type(exc).__name__ in {"OutputParserException", "StructuredOutputError"}


def quote_cost(
    parameters: Mapping[str, object],
    input_tokens: int,
    output_tokens: int,
    *,
    offline: bool,
) -> float | None:
    incoming = _number(parameters.get(_INPUT_PRICE))
    outgoing = _number(parameters.get(_OUTPUT_PRICE))
    if incoming is None or outgoing is None:
        return 0.0 if offline else None
    return (input_tokens * incoming + output_tokens * outgoing) / 1_000_000


def usage_tokens(message: object) -> tuple[int, int]:
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, Mapping):
        return _token(usage.get("input_tokens")), _token(usage.get("output_tokens"))
    if usage is None:
        return 0, 0
    return _token(getattr(usage, "input_tokens", None)), _token(getattr(usage, "output_tokens", None))


def usage_from_messages(messages: Sequence[object]) -> tuple[int, int]:
    incoming = 0
    outgoing = 0
    for message in messages:
        inp, out = usage_tokens(message)
        incoming += inp
        outgoing += out
    return incoming, outgoing


def elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _token(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _rate(part: int, whole: int) -> float:
    if whole == 0:
        return 0.0
    return part / whole
