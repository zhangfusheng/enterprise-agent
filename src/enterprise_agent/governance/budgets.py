from pydantic import BaseModel, Field


class BudgetLimits(BaseModel):
    model_calls: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    agents: int = Field(ge=0)
    steps: int = Field(ge=0)
    runtime_seconds: int = Field(ge=0)

