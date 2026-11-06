from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from enterprise_agent.agents.research.schemas import EvidenceCitation, SupportStatus


class ResearchEvidenceBundle(BaseModel):
    """The structured, server-produced input handed from Research to Analysis."""

    model_config = ConfigDict(extra="forbid")

    query: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)
    plan: dict[str, Any] | None = None
    source_assessments: list[dict[str, Any]] = Field(default_factory=list)
    evidence_index: dict[str, Any] = Field(default_factory=dict)
    unresolved_questions: list[str] = Field(default_factory=list)


class AnalysisClaimDraft(BaseModel):
    """Model-proposed claim; verification counters are server-owned."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    support_status: SupportStatus
    confidence: float = Field(ge=0.0, le=1.0)
    citations: list[EvidenceCitation] = Field(default_factory=list)


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    research_evidence: ResearchEvidenceBundle | None = None


class AnalysisOutput(BaseModel):
    conclusion: str
    evidence_count: int
    report_markdown: str | None = None
    calculation_operation: str | None = None
    calculation: float | None = None
    evidence_pack: dict[str, Any] | None = None
    citation_validation: dict[str, Any] | None = None

