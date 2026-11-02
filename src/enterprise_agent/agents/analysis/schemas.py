from pydantic import BaseModel, Field


class AnalysisRequest(BaseModel):
    objective: str
    evidence: list[dict] = Field(default_factory=list)


class AnalysisOutput(BaseModel):
    conclusion: str
    evidence_count: int
    report_markdown: str | None = None
    calculation_operation: str | None = None
    calculation: float | None = None

