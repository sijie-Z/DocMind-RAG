"""
Token usage tracking model — per-user LLM cost accounting.
"""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class TokenUsageRecord(Base):
    """Token usage record for cost tracking."""

    __tablename__ = "token_usage_records"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, comment="用户ID"
    )
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), index=True, comment="组织ID"
    )
    model: Mapped[str] = mapped_column(String(100), comment="模型名称")
    source: Mapped[str] = mapped_column(String(50), comment="来源: rag_chat, agent, tool_call")
    input_tokens: Mapped[int] = mapped_column(Integer, comment="输入Token数")
    output_tokens: Mapped[int] = mapped_column(Integer, comment="输出Token数")
    cost_usd: Mapped[float | None] = mapped_column(Float, comment="估算成本(USD)")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True, comment="创建时间"
    )
