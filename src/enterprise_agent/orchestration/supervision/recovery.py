"""Decide whether a worker result can continue, should retry, or must fail."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from enterprise_agent.contracts.results import AgentResult, ResultStatus


class RecoveryAction(StrEnum):
    PROCEED = "PROCEED"
    RETRY = "RETRY"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str


_TERMINAL_RESEARCH_STOPS = frozenset({"fetch_budget_exhausted"})


def decide_recovery(result: AgentResult) -> RecoveryDecision:
    """Classify a result without treating every PARTIAL as a retryable error.

    A partial result with a usable deliverable may unblock downstream tasks. Its
    original PARTIAL status remains visible in the final aggregate result.
    """

    if result.status == ResultStatus.SUCCESS:
        return RecoveryDecision(RecoveryAction.PROCEED, "result_succeeded")
    if result.status == ResultStatus.PARTIAL:
        if _has_usable_output(result):
            return RecoveryDecision(RecoveryAction.PROCEED, "partial_result_is_usable")
        return RecoveryDecision(RecoveryAction.RETRY, "partial_result_has_no_usable_output")
    if result.status == ResultStatus.FAILED:
        if _is_terminal_research_failure(result):
            return RecoveryDecision(
                RecoveryAction.FAIL,
                "research_fetch_budget_exhausted_without_evidence",
            )
        return RecoveryDecision(RecoveryAction.RETRY, "failed_result_is_retryable")
    # Preserve the supervisor's historical behavior for statuses that are not
    # explicitly understood: treat them as retryable rather than silently
    # converting a potentially recoverable worker result into a terminal
    # failure.
    return RecoveryDecision(RecoveryAction.RETRY, "unknown_result_status")


def _has_usable_output(result: AgentResult) -> bool:
    return bool(
        result.summary.strip()
        and (result.sources or result.artifact_refs or result.structured_data)
        and not result.unmet_requirements
    )


def _is_terminal_research_failure(result: AgentResult) -> bool:
    """Avoid retrying an exhausted research loop with no verified evidence.

    ``structured_data`` contains the run-local research counters, while the
    top-level ``sources`` list is the deliverable consumed by downstream
    tasks.  A fetch budget stop with neither is deterministic under the same
    policy, so replanning the same research task only repeats the failure.
    """
    research = result.structured_data.get("research")
    if not isinstance(research, Mapping):
        return False
    return (
        research.get("stop_reason") in _TERMINAL_RESEARCH_STOPS
        and research.get("verified_source_count") == 0
        and not result.sources
        and not result.artifact_refs
    )
