from pydantic import BaseModel, Field


class ResearchEvidence(BaseModel):
    title: str
    url: str
    snippet: str = ""
    content: str | None = None


class ResearchRequest(BaseModel):
    query: str
    limit: int = Field(default=5, ge=1, le=20)
    acceptance_criteria: list[str] = Field(default_factory=list)
    historical_evidence: list[ResearchEvidence] = Field(default_factory=list)
    excluded_urls: list[str] = Field(default_factory=list)

