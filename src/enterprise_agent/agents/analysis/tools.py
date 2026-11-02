"""Analysis tools the model can call.

The model supplies the report text and the calculation request. Tenant, run,
and operation identity are injected here. Execution stays on ToolGateway when
one is bound, so scope, budget, and cancellation do not move into the harness.
"""

import json
from collections.abc import Callable, Sequence
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.runtime.budgets import BudgetExceeded
from enterprise_agent.runtime.cancellation import CancellationRequested
from enterprise_agent.tools.calculate import calculate
from enterprise_agent.tools.contracts import ToolCall, ToolStatus
from enterprise_agent.tools.gateway import ToolDenied, ToolGateway
from enterprise_agent.tools.save_report import ReportStore

_RUN_CONTROL = (CancellationRequested, BudgetExceeded, ToolDenied)
_CALCULATE = "calculate"
_SAVE = "save_report"
_VALUES_REJECTED = "数值不在证据中"
_STORE_UNAVAILABLE = "报告存储不可用"
_FAILED = ToolStatus.FAILED.value
_SUCCESS = ToolStatus.SUCCESS.value

type Operation = Literal["sum", "mean", "difference", "growth_rate"]


class RunControl:
    """Remembers a runtime control error if the tool loop turns it into a message."""

    def __init__(self) -> None:
        self.error: BaseException | None = None

    def capture(self, exc: BaseException) -> None:
        if self.error is None:
            self.error = exc


class _CalculateArgs(BaseModel):
    operation: Operation = Field(description="Arithmetic operation")
    values: list[float] = Field(
        min_length=1,
        description="One numeric series copied from the evidence values field",
    )


class _SaveArgs(BaseModel):
    markdown: str = Field(min_length=1, description="Markdown report text")


def bind_analysis_tools(
    *,
    evidence: list[dict[str, Any]],
    reports: ReportStore | None,
    gateway: ToolGateway | None,
    context: ExecutionContext,
    control: RunControl,
) -> list[StructuredTool]:
    series = [tuple(item["values"]) for item in evidence if "values" in item]

    def calculate_tool(operation: Operation, values: list[float]) -> str:
        return _calculate(operation, values, series, gateway, context, control)

    def save_report(markdown: str) -> str:
        return _save(markdown, reports, gateway, context, control)

    return [
        StructuredTool.from_function(
            name=_CALCULATE,
            description="Run deterministic arithmetic on one evidence values series.",
            args_schema=_CalculateArgs,
            func=calculate_tool,
        ),
        StructuredTool.from_function(
            name=_SAVE,
            description="Save the Markdown report. Pass only the report text.",
            args_schema=_SaveArgs,
            func=save_report,
        ),
    ]


def _calculate(
    operation: Operation,
    values: list[float],
    series: Sequence[tuple[Any, ...]],
    gateway: ToolGateway | None,
    context: ExecutionContext,
    control: RunControl,
) -> str:
    if not any(_same_series(values, accepted) for accepted in series):
        return _fail(_VALUES_REJECTED)
    numbers = [float(item) for item in values]

    def direct() -> float:
        return calculate(operation, numbers)

    return _execute(
        gateway,
        context,
        control,
        ToolCall(name=_CALCULATE, arguments={"operation": operation, "values": numbers}),
        direct,
        _calculation_value,
        "calculate failed",
    )


def _save(
    markdown: str,
    reports: ReportStore | None,
    gateway: ToolGateway | None,
    context: ExecutionContext,
    control: RunControl,
) -> str:
    operation_id = f"{context.run_id}:analysis-report"
    call = ToolCall(
        name=_SAVE,
        arguments={
            "tenant_id": context.tenant_id,
            "run_id": context.run_id,
            "markdown": markdown,
            "operation_id": operation_id,
        },
        operation_id=operation_id,
    )
    if gateway is not None:
        return _execute(gateway, context, control, call, None, _artifact_id, "save failed")
    if reports is None:
        return _fail(_STORE_UNAVAILABLE)
    store = reports

    def direct() -> Any:
        return store.save(context.tenant_id, context.run_id, markdown, operation_id)

    return _execute(None, context, control, call, direct, _artifact_id, "save failed")


def _execute(
    gateway: ToolGateway | None,
    context: ExecutionContext,
    control: RunControl,
    call: ToolCall,
    direct: Callable[[], Any] | None,
    accept: Callable[[Any], str],
    fallback: str,
) -> str:
    try:
        if gateway is None:
            if direct is None:
                return _fail(fallback)
            value = direct()
        else:
            result = gateway.call(context, call)
            if result.status != ToolStatus.SUCCESS:
                return _fail(result.error or fallback, status=result.status.value)
            value = result.value
    except _RUN_CONTROL as exc:
        control.capture(exc)
        raise
    except Exception as exc:
        return _fail(type(exc).__name__)
    return accept(value)


def _calculation_value(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return _fail("calculate failed")
    return _ok(value=float(value))


def _artifact_id(value: Any) -> str:
    if isinstance(value, dict):
        artifact_id = value.get("artifact_id")
    else:
        artifact_id = getattr(value, "artifact_id", None)
    if not isinstance(artifact_id, str) or not artifact_id:
        return _fail("missing artifact id")
    return _ok(artifact_id=artifact_id)


def _same_series(values: Sequence[float], accepted: Sequence[Any]) -> bool:
    if len(values) != len(accepted):
        return False
    return all(float(item) == expected for item, expected in zip(values, accepted, strict=True))


def _fail(error: str, *, status: str = _FAILED) -> str:
    return _payload(ok=False, status=status, error=error)


def _ok(**fields: Any) -> str:
    return _payload(ok=True, status=_SUCCESS, **fields)


def _payload(**fields: Any) -> str:
    return json.dumps(fields, ensure_ascii=False)
