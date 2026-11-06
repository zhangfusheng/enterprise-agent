"""Bounded, deterministic claim submission for research-backed analysis."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from enterprise_agent.agents.analysis.schemas import AnalysisClaimDraft
from enterprise_agent.agents.research.evidence import EvidenceIndex, EvidencePackValidator
from enterprise_agent.agents.research.schemas import (
    EvidenceClaim,
    EvidenceValidationIssue,
    EvidenceValidationReport,
    SupportStatus,
)

_TOOL_NAME = "submit_claims"
_MAX_ATTEMPTS = 2
_EMPTY_CLAIMS = "至少提交一条分析声明"
_TOO_MANY_ATTEMPTS = "引用校验修复次数已耗尽"


class _SubmitClaimsArgs(BaseModel):
    claims: list[AnalysisClaimDraft] = Field(
        default_factory=list,
        description="Claims with verbatim citations from the supplied research evidence",
    )


@dataclass
class ClaimSubmissionControl:
    """Run-local state shared by the claim tool and AnalysisAgent."""

    attempts: int = 0
    report: EvidenceValidationReport | None = None
    validated_claims: list[EvidenceClaim] = field(default_factory=list)
    allow_empty_claims: bool = False

    @property
    def accepted(self) -> bool:
        return self.report is not None and self.report.valid and bool(self.validated_claims)


def bind_claim_submission_tool(
    *,
    index: EvidenceIndex,
    control: ClaimSubmissionControl,
) -> StructuredTool:
    """Expose one bounded, side-effect-free claim submission tool."""

    validator = EvidencePackValidator(index)

    def submit_claims(claims: list[AnalysisClaimDraft]) -> str:
        if control.attempts >= _MAX_ATTEMPTS:
            return _failure(_TOO_MANY_ATTEMPTS)
        control.attempts += 1
        if not claims:
            if control.allow_empty_claims:
                claims = [
                    AnalysisClaimDraft(
                        claim_id="offline-unclear-claim",
                        text="离线 fixture 文档已提供给分析流程。",
                        support_status=SupportStatus.UNCLEAR,
                        confidence=0.0,
                    )
                ]
            else:
                return _failure(_EMPTY_CLAIMS)
        parsed = [
            EvidenceClaim(
                claim_id=claim.claim_id,
                text=claim.text,
                support_status=claim.support_status,
                confidence=claim.confidence,
                citations=claim.citations,
            )
            for claim in claims
        ]
        report = validator.validate_claims(parsed)
        control.report = report
        if not report.valid:
            return _validation_failure(report)
        control.validated_claims = list(report.validated_claims)
        return _success(report)

    return StructuredTool.from_function(
        name=_TOOL_NAME,
        description=(
            "Submit structured analysis claims. Every supported or contradicted claim "
            "must cite an exact quote from the supplied research evidence. "
            "The server recomputes source counters and accepts at most two attempts."
        ),
        args_schema=_SubmitClaimsArgs,
        func=submit_claims,
    )


def claim_error_messages(control: ClaimSubmissionControl) -> list[str]:
    """Return compact errors suitable for the public AgentResult."""

    report = control.report
    if report is None:
        return ["缺少经过校验的分析声明"]
    return [issue.message for issue in report.issues]


def _success(report: EvidenceValidationReport) -> str:
    return json.dumps(
        {
            "ok": True,
            "status": "SUCCESS",
            "verification_summary": report.verification_summary,
        },
        ensure_ascii=False,
    )


def _validation_failure(report: EvidenceValidationReport) -> str:
    return json.dumps(
        {
            "ok": False,
            "status": "FAILED",
            "verification_summary": report.verification_summary,
            "issues": [issue.model_dump(mode="json") for issue in report.issues],
        },
        ensure_ascii=False,
    )


def _failure(message: str) -> str:
    return json.dumps(
        {
            "ok": False,
            "status": "FAILED",
            "issues": [
                EvidenceValidationIssue(code="claim_submission", message=message).model_dump(
                    mode="json"
                )
            ],
        },
        ensure_ascii=False,
    )


__all__ = ["ClaimSubmissionControl", "bind_claim_submission_tool", "claim_error_messages"]
