from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.orchestration.supervision.recovery import (
    RecoveryAction,
    decide_recovery,
)


def test_usable_partial_result_proceeds_without_retry():
    result = AgentResult(
        status=ResultStatus.PARTIAL,
        summary="收集到部分来源",
        sources=[{"url": "https://example.test/source"}],
        errors=["one page failed"],
    )

    decision = decide_recovery(result)

    assert decision.action == RecoveryAction.PROCEED
    assert decision.reason == "partial_result_is_usable"


def test_partial_result_without_deliverable_retries():
    result = AgentResult(
        status=ResultStatus.PARTIAL,
        summary="没有得到可用搜索结果",
        unmet_requirements=["没有来源"],
        errors=["search timeout"],
    )

    assert decide_recovery(result).action == RecoveryAction.RETRY


def test_success_proceeds_and_unknown_retries():
    assert (
        decide_recovery(AgentResult(status=ResultStatus.SUCCESS)).action
        == RecoveryAction.PROCEED
    )
    assert (
        decide_recovery(AgentResult(status=ResultStatus.UNKNOWN)).action
        == RecoveryAction.RETRY
    )


def test_fetch_budget_failure_without_evidence_is_terminal():
    result = AgentResult(
        status=ResultStatus.FAILED,
        summary="研究未完成",
        structured_data={
            "research": {
                "stop_reason": "fetch_budget_exhausted",
                "verified_source_count": 0,
            }
        },
        errors=["抓取次数达到 research 局部预算"],
    )

    decision = decide_recovery(result)

    assert decision.action == RecoveryAction.FAIL
    assert decision.reason == "research_fetch_budget_exhausted_without_evidence"
