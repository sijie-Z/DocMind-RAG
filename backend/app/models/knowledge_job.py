"""
知识库处理任务模型
"""
import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class KnowledgeJobStatus(enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


class KnowledgeProcessingJob(Base):
    __tablename__ = "knowledge_processing_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True)
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True)
    trigger_type: Mapped[str] = mapped_column(String(32), default="upload")  # upload/reprocess
    status: Mapped[KnowledgeJobStatus] = mapped_column(Enum(KnowledgeJobStatus), default=KnowledgeJobStatus.QUEUED)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())

    document: Mapped[Optional["Document"]] = relationship("Document", lazy="selectin")
