"""文档核验身份请求/响应 schema（合同核验场景 Batch 1）。"""

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
