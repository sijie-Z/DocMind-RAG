"""核验请求/响应 schema（合同核验场景 Batch 1/2）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ReviewMetaUpsert(BaseModel):
    """注册/更新文档核验身份；content_sha256 由服务端从存储对象计算。"""

    doc_role: Literal["policy", "template", "contract"] = Field(..., description="文档角色")
    family_key: str | None = Field(None, max_length=128, description="版本族键（合同必填）")
    version_label: str | None = Field(None, max_length=64, description="版本标签（合同必填）")
    version_seq: int | None = Field(None, ge=1, le=2**31 - 1, description="版本序（合同必填）")
    supersedes_document_id: str | None = Field(
        None, max_length=36, description="被本版本替代的同组织文档 id"
    )
    effective_from: datetime | None = Field(None, description="生效起点（可选）")
    effective_to: datetime | None = Field(None, description="生效终点（可选）")


class ReviewCaseCreate(BaseModel):
    """建案：一份制度/模板 + 合同基准版 + 修订版。"""

    title: str = Field(..., min_length=1, max_length=255, description="案件标题")
    policy_document_id: str = Field(
        ..., min_length=1, max_length=36, description="制度/模板文档 id"
    )
    base_document_id: str = Field(..., min_length=1, max_length=36, description="合同基准版 id")
    revised_document_id: str = Field(..., min_length=1, max_length=36, description="合同修订版 id")
    checklist_version: str = Field("v1", max_length=16, description="核验清单版本")
    assignee_id: int | None = Field(None, ge=1, description="指定审核人（可选）")


class ReviewEvidenceSelect(BaseModel):
    """人工选择/改选某核验项某一侧的证据（chunk 必须属于该侧文档）。"""

    side: Literal["policy", "base", "revised"] = Field(..., description="证据侧")
    chunk_id: str = Field(..., min_length=1, max_length=36, description="文档分块 id")
    role: Literal["main", "condition", "exception", "cross_ref"] = Field(
        "main", description="证据角色"
    )
    selected: bool = Field(True, description="选中=true；取消选择=false")


class ReviewDecisionUpsert(BaseModel):
    """逐项人工决策：只允许三态之一，pending/insufficient 等不能手工选择。"""

    status: Literal["verified", "needs_human", "not_applicable"] = Field(
        ..., description="决策状态"
    )
    note: str | None = Field(None, max_length=2000, description="决策说明（可选）")
