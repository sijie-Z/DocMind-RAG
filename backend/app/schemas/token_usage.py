"""Token usage Pydantic schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TokenUsageCreate(BaseModel):
    user_id: int
    organization_id: int | None = None
    model: str
    source: str = Field(..., pattern="^(rag_chat|agent|tool_call)$")
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None


class TokenUsageResponse(BaseModel):
    id: int
    user_id: int
    organization_id: int | None = None
    model: str
    source: str
    input_tokens: int
    output_tokens: int
    cost_usd: float | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TokenUsageSummary(BaseModel):
    """Summary of token usage for dashboards."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_requests: int = 0
    by_model: list[dict] = Field(default_factory=list, description="Usage breakdown by model")
    by_user: list[dict] = Field(default_factory=list, description="Usage breakdown by user")
    by_day: list[dict] = Field(default_factory=list, description="Daily usage trend")
