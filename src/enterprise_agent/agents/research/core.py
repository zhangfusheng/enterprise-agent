"""Research planning and deterministic execution core."""

from __future__ import annotations

from typing import Any

from enterprise_agent.agents.research.execution import ResearchExecutor
from enterprise_agent.agents.research.planning import (
    PlanValidationError,
    ResearchPlanner,
    ResearchPlanningError,
)
from enterprise_agent.agents.research.ranking import (
    RANKING_VERSION,
    deduplicate_sources,
    evidence_is_eligible,
    normalize_source_url,
    rank_sources,
)
from enterprise_agent.agents.research.schemas import (
    FreshnessStatus,
    ResearchEvidence,
    ResearchRequest,
)
from enterprise_agent.agents.research.tools import ResearchLoopPolicy, RunControl
from enterprise_agent.contracts.context import ExecutionContext
from enterprise_agent.contracts.results import AgentResult, ResultStatus
from enterprise_agent.runtime.cancellation import CancellationRequested
from enterprise_agent.tools.gateway import ToolDenied, ToolGateway
from enterprise_agent.tools.web.fetch import WebFetcher
from enterprise_agent.tools.web.provider import SearchProvider

_SEARCH_FAILED = "搜索工具失败"
_NO_VERIFIED_RESULTS = "没有通过来源质量门禁的可引用来源"
_INSUFFICIENT_VERIFIED_RESULTS = "通过质量门禁的来源数量不足"


class ResearchCore:
    """Generate one ResearchPlan and execute only that plan."""

    role = "research"

    def __init__(
        self,
        provider: SearchProvider,
        fetcher: WebFetcher,
        *,
        planner: ResearchPlanner,
        allow_fetch: bool = True,
        skip_fetch_prefixes: tuple[str, ...] = (),
        loop_policy: ResearchLoopPolicy | None = None,
    ) -> None:
        self.provider = provider
        self.fetcher = fetcher
        self.planner = planner
        self.allow_fetch = allow_fetch
        self.skip_fetch_prefixes = tuple(skip_fetch_prefixes)
        self.loop_policy = loop_policy or ResearchLoopPolicy()

    def run(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        *,
        gateway: ToolGateway | None = None,
    ) -> AgentResult:
        try:
            plan = self.planner.create_plan(
                request,
                context,
                before_completion=(
                    (lambda: gateway.budget.consume("model_calls"))
                    if gateway is not None
                    else None
                ),
            )
        except (CancellationRequested, ToolDenied):
            raise
        except (ResearchPlanningError, PlanValidationError) as exc:
            return self._planning_failure(request, context, str(exc))

        executor = ResearchExecutor(
            self.provider,
            self.fetcher,
            allow_fetch=self.allow_fetch,
            skip_fetch_prefixes=self.skip_fetch_prefixes,
            loop_policy=self.loop_policy,
        )
        try:
            execution = executor.run(plan, request, context, gateway=gateway)
        except (CancellationRequested, ToolDenied):
            raise
        evidence = execution.evidence
        errors = execution.errors
        if not evidence and not request.historical_evidence:
            return self._failure(request, context, plan, errors or [_SEARCH_FAILED], execution.control)
        return self._collected(request, context, plan, evidence, errors, execution.control)

    def _planning_failure(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        error: str,
    ) -> AgentResult:
        return AgentResult(
            status=ResultStatus.FAILED,
            summary="研究计划生成失败。",
            structured_data={
                "query": request.query,
                "evidence": [],
                "research": {
                    "plan_status": "failed",
                    "plan_error": error,
                    "search_call_count": 0,
                    "fetch_call_count": 0,
                    "tool_call_count": 0,
                },
            },
            errors=[error],
            agent_id=context.agent_id,
            parent_agent_id=context.parent_agent_id,
        )

    def _failure(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        plan: Any,
        errors: list[str],
        control: RunControl,
    ) -> AgentResult:
        summary = "没有通过来源质量门禁。" if control.policy.require_fetch else _SEARCH_FAILED
        return AgentResult(
            status=ResultStatus.FAILED,
            summary=summary,
            structured_data={
                "query": request.query,
                "evidence": [],
                "plan": plan.model_dump(mode="json"),
                "research": research_metadata(request, plan, control, []),
            },
            errors=errors,
            agent_id=context.agent_id,
            parent_agent_id=context.parent_agent_id,
        )

    def _collected(
        self,
        request: ResearchRequest,
        context: ExecutionContext,
        plan: Any,
        evidence: list[dict[str, Any]],
        errors: list[str],
        control: RunControl,
    ) -> AgentResult:
        effective_as_of = request.as_of or control.effective_as_of
        ranked = rank_sources(
            merge_evidence(request.historical_evidence, evidence),
            preferred_domains=plan.preferred_domains,
            required_source_types=plan.required_source_types,
            freshness_days=plan.freshness_days,
            as_of=effective_as_of,
            min_content_length=control.policy.min_content_length,
        )
        verified_ranked = [item for item in ranked if control.is_verified(item.source)]
        merged = [
            item.source
            for item in verified_ranked
            if evidence_is_eligible(
                item.assessment,
                freshness_required=request.freshness_days is not None,
            )
        ]
        fresh_count = sum(
            item.assessment.freshness_status.value == "fresh" for item in verified_ranked
        )
        required_count = (
            fresh_count if request.freshness_days is not None else len(merged)
        )
        collected_errors = list(errors)
        if not merged and control.policy.require_fetch:
            if _NO_VERIFIED_RESULTS not in collected_errors:
                collected_errors.append(_NO_VERIFIED_RESULTS)
        elif control.policy.require_fetch and required_count < control.policy.min_usable_sources:
            collected_errors.append(
                f"{_INSUFFICIENT_VERIFIED_RESULTS}: "
                f"{required_count}/{control.policy.min_usable_sources}"
            )
        merged_assessments = [item.assessment for item in verified_ranked]
        return AgentResult(
            status=outcome(merged, collected_errors),
            summary=(
                "没有通过来源质量门禁。"
                if control.policy.require_fetch and not merged
                else (
                    f"收集到 {len(merged)} 条已验证来源。"
                    if control.policy.require_fetch
                    else f"收集到 {len(merged)} 条来源。"
                )
            ),
            structured_data={
                "query": request.query,
                "evidence": merged,
                "plan": plan.model_dump(mode="json"),
                "research": research_metadata(
                    request,
                    plan,
                    control,
                    merged,
                    assessments=merged_assessments,
                ),
            },
            sources=merged,
            unmet_requirements=(
                []
                if (
                    merged
                    and (
                        not control.policy.require_fetch
                        or required_count >= control.policy.min_usable_sources
                    )
                )
                else [_NO_VERIFIED_RESULTS]
            ),
            errors=collected_errors,
            agent_id=context.agent_id,
            parent_agent_id=context.parent_agent_id,
        )


def outcome(evidence: list[Any], errors: list[str]) -> ResultStatus:
    if not evidence:
        return ResultStatus.FAILED
    if errors:
        return ResultStatus.PARTIAL
    return ResultStatus.SUCCESS


def merge_evidence(
    historical: list[ResearchEvidence],
    current: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates = [
        *(evidence.model_dump(mode="json") for evidence in historical),
        *(dict(item) for item in current),
    ]
    candidates = [
        item
        for item in candidates
        if isinstance(item.get("url"), str) and item["url"].strip()
    ]
    return list(
        deduplicate_sources(candidates, min_content_length=1).sources
    )


def research_metadata(
    request: ResearchRequest,
    plan: Any,
    control: RunControl,
    evidence: list[dict[str, Any]],
    *,
    assessments: list[Any] | None = None,
) -> dict[str, Any]:
    metadata = control.snapshot()
    historical_urls = {url_key(item.url) for item in request.historical_evidence}
    normalized_assessments = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        for item in (assessments if assessments is not None else control.assessments())
    ]
    metadata.update(
        {
            "plan_status": "compiled",
            "plan_id": plan.plan_id,
            "plan_version": plan.version,
            "planned_query_count": len(plan.queries),
            "acceptance_criteria_count": len(request.acceptance_criteria),
            "historical_evidence_count": len(request.historical_evidence),
            "new_evidence_count": sum(
                1
                for item in evidence
                if url_key(str(item.get("url", ""))) not in historical_urls
            ),
            "ranking_version": RANKING_VERSION,
            "source_assessments": normalized_assessments,
            "duplicate_sources": control.duplicate_sources(),
            "freshness_counts": _assessment_freshness_counts(normalized_assessments),
        }
    )
    return metadata


def url_key(value: str) -> str:
    return normalize_source_url(value)


def _assessment_freshness_counts(assessments: list[dict[str, Any]]) -> dict[str, int]:
    return {
        status.value: sum(item.get("freshness_status") == status.value for item in assessments)
        for status in FreshnessStatus
    }


__all__ = [
    "ResearchCore",
    "merge_evidence",
    "outcome",
    "research_metadata",
    "url_key",
]
