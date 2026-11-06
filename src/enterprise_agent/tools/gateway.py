from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import timedelta

from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.infrastructure.repositories.operations import OperationRepository
from enterprise_agent.runtime.budgets import BudgetLedger
from enterprise_agent.runtime.cancellation import CancellationToken
from enterprise_agent.runtime.ownership import raise_if_lease_lost
from enterprise_agent.tools.contracts import ToolCall, ToolResult, ToolStatus
from enterprise_agent.tools.idempotency import arguments_hash, stable_operation_id
from enterprise_agent.tools.registry import ToolRegistry


class ToolDenied(PermissionError):
    pass


@dataclass
class ToolGateway:
    registry: ToolRegistry
    budget: BudgetLedger
    cancellation: CancellationToken
    idempotency_store: dict[str, ToolResult]
    operations: OperationRepository | None = None

    def call(self, context: ExecutionContext, request: ToolCall) -> ToolResult:
        raise_if_lease_lost()
        self.cancellation.raise_if_cancelled()
        self.budget.check_runtime()
        registered = self.registry.get(request.name)
        if request.name not in context.allowed_tools:
            raise ToolDenied(f"tool {request.name} is not in the execution scope")
        if request.name in {"web_search", "web_fetch"}:
            if context.metadata.get("external_network_allowed") is not True:
                raise ToolDenied(
                    f"external network access is not enabled for tool {request.name}"
                )
            if (
                request.name == "web_fetch"
                and context.metadata.get("network_mode") in {"offline_fixture", "disabled"}
            ):
                raise ToolDenied("web_fetch is unavailable outside external network mode")
        if not registered.spec.required_scopes.issubset(context.metadata.get("scopes", set())):
            raise ToolDenied(f"missing scopes for tool {request.name}")
        task_id = _task_id(context)
        digest = arguments_hash(request.arguments)
        operation_id = request.operation_id or stable_operation_id(
            context.run_id, request.name, request.arguments, task_id
        )
        cached = self.idempotency_store.get(operation_id)
        if cached is not None:
            return cached
        replay = self._replay(operation_id, registered.spec.has_side_effect, registered.spec.timeout_seconds)
        if replay is not None:
            self.idempotency_store[operation_id] = replay
            return replay
        if self.operations is not None and not self.operations.try_start(
            operation_id=operation_id,
            tenant_id=context.tenant_id,
            run_id=context.run_id,
            task_id=task_id,
            tool_name=request.name,
            arguments_hash=digest,
            has_side_effect=registered.spec.has_side_effect,
        ):
            replay = self._replay(operation_id, registered.spec.has_side_effect, registered.spec.timeout_seconds)
            if replay is not None:
                self.idempotency_store[operation_id] = replay
                return replay
        self.budget.consume("tool_calls")
        arguments = dict(request.arguments)
        if registered.spec.has_side_effect:
            arguments.setdefault("operation_id", operation_id)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(registered.function, **arguments)
                value = future.result(timeout=registered.spec.timeout_seconds)
            encoded = str(value).encode("utf-8")
            if len(encoded) > registered.spec.max_result_bytes:
                result = ToolResult(status=ToolStatus.FAILED, error="tool result exceeds size limit", operation_id=operation_id)
            else:
                result = ToolResult(status=ToolStatus.SUCCESS, value=value, operation_id=operation_id)
        except FutureTimeoutError:
            # A timed-out side effect may have landed. Do not run it again.
            status = ToolStatus.UNKNOWN if registered.spec.has_side_effect else ToolStatus.FAILED
            result = ToolResult(status=status, error="tool timed out", operation_id=operation_id)
        except Exception as exc:  # boundary converts tool failures to a safe result
            safe_error = getattr(exc, "code", type(exc).__name__)
            result = ToolResult(status=ToolStatus.FAILED, error=str(safe_error), operation_id=operation_id)
        self._remember(operation_id, result)
        return result

    def _replay(self, operation_id: str, has_side_effect: bool, timeout_seconds: float) -> ToolResult | None:
        if self.operations is None:
            return None
        return self.operations.lookup(
            operation_id,
            has_side_effect=has_side_effect,
            stale_after=timedelta(seconds=timeout_seconds),
        )

    def _remember(self, operation_id: str, result: ToolResult) -> None:
        self.idempotency_store[operation_id] = result
        if self.operations is not None:
            self.operations.complete(operation_id, result)


def _task_id(context: ExecutionContext) -> str | None:
    task_id = context.metadata.get("task_id")
    return task_id if isinstance(task_id, str) and task_id else None
