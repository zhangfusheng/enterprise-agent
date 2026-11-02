import json
from collections.abc import Iterable
from typing import Any

from enterprise_agent.contracts.results import AgentResult, ResultStatus

_EMPTY = "没有可交付结果。"


def merge_results(results: list[AgentResult]) -> AgentResult:
    """Join task results into one candidate. Acceptance decides the run status."""
    if not results:
        return AgentResult(status=ResultStatus.FAILED, summary=_EMPTY)
    return AgentResult(
        status=_status(results),
        summary="\n\n".join(result.summary for result in results if result.summary),
        sources=_unique(item for result in results for item in result.sources),
        artifact_refs=_unique(item for result in results for item in result.artifact_refs),
        unmet_requirements=_unique(item for result in results for item in result.unmet_requirements),
        errors=_unique(item for result in results for item in result.errors),
    )


def _status(results: list[AgentResult]) -> ResultStatus:
    statuses = {result.status for result in results}
    if ResultStatus.FAILED in statuses and statuses != {ResultStatus.FAILED}:
        return ResultStatus.PARTIAL
    if ResultStatus.FAILED in statuses:
        return ResultStatus.FAILED
    if ResultStatus.PARTIAL in statuses or ResultStatus.UNKNOWN in statuses:
        return ResultStatus.PARTIAL
    return ResultStatus.SUCCESS


def _unique(items: Iterable[Any]) -> list[Any]:
    seen: set[str] = set()
    unique = []
    for item in items:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
