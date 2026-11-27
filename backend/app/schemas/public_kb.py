"""公开知识库发布请求 schema（批次十三）。

只做防滥用粗拦（类型/长度/数量上限）；真正的归属/状态校验（KB 属于本组织、
文档属于本组织且与 KB 一致、``INDEXED``、启用）在 ``public_kb_service`` 统一
执行，内部调用同样绕不过去。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
MAX_DOCUMENTS_PER_BATCH = 200
MAX_QUERY_CHARS = 200
MAX_TOP_K = 10


class PublicationCreate(BaseModel):
    """创建发布：标题/描述/有效期 + 可选首批白名单文档。"""

    knowledge_base_id: str = Field(..., min_length=1, max_length=36, description="发布的知识库 id")
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_CHARS, description="公开页标题")
    description: str | None = Field(
        default=None, max_length=MAX_DESCRIPTION_CHARS, description="公开页描述（可空）"
    )
    expires_in_days: int | None = Field(
        default=None, ge=1, le=90, description="有效期（天）；空 = 不过期"
    )
    document_ids: list[str] | None = Field(
        default=None,
        max_length=MAX_DOCUMENTS_PER_BATCH,
        description="首批白名单文档 id（必须是本组织、同 KB、已索引且启用）",
    )


class PublicationRotateRequest(BaseModel):
    """轮换 token；可选更新有效期（缺省 = 保持当前过期时间）。"""

    expires_in_days: int | None = Field(
        default=None, ge=1, le=90, description="新的有效期（天）；空 = 保持当前设置"
    )


class PublicationDocumentsUpdate(BaseModel):
    """批量设置白名单：replace=整体替换，add=追加，remove=移除。"""

    mode: Literal["replace", "add", "remove"] = "replace"
    document_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_DOCUMENTS_PER_BATCH,
        description="文档 id 列表（上限 200 条）",
    )


class PublicKbSearchRequest(BaseModel):
    """公开检索请求（无登录；token 即全部权限）。"""

    query: str = Field(..., description="检索词（服务层限制 1–200 字符）")
    top_k: int = Field(default=5, description="返回条数（服务层限制 1–10）")
