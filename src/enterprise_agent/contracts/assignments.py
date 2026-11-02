from pydantic import BaseModel, Field


class Assignment(BaseModel):
    assignment_id: str
    task_id: str
    agent_role: str
    input_refs: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_capabilities: list[str] = Field(default_factory=list)

