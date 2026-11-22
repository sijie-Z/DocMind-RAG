"""把可选的知识库过滤收口为 DB 预筛的 document_id 集合。

设计纪律（与 ``metadata_filter`` 同构）：
- 只做收窄，绝不放宽：先确认知识库属于当前组织，再在该组织内取库内文档；
- 空命中必须返回空集合，由调用方转成早退空结果，**绝不**退回全组织检索；
- DB 异常 fail-closed（检索不可用），不把过滤故障变成越权召回；
- 不碰 ACL：私有文档的 blocked_ids 始终由 document_acl 单独计算并合并。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase

# 与 metadata_filter 的有界扫描一致：超限时拒绝，不返回「部分匹配」放大范围。
MAX_KB_FILTER_SCAN = 10_000


async def resolve_knowledge_base_document_ids(
    db: AsyncSession, organization_id: int, knowledge_base_id: str | None
) -> list[str] | None:
    """解析知识库内的文档 id；未给出 kb_id 时返回 None（旧行为逐字不变）。

    跨组织 / 不存在的知识库返回空列表；调用方以 ``_NO_MATCH`` 早退空结果，
    不会退回组织级全量检索。
    """
    if not knowledge_base_id:
        return None
    try:
        kb = (
            await db.execute(
                select(KnowledgeBase.id).where(
                    KnowledgeBase.id == knowledge_base_id,
                    KnowledgeBase.organization_id == organization_id,
                )
            )
        ).scalar_one_or_none()
        if kb is None:
            return []
        rows = (
            (
                await db.execute(
                    select(Document.id)
                    .where(
                        Document.organization_id == organization_id,
                        Document.knowledge_base_id == knowledge_base_id,
                    )
                    .limit(MAX_KB_FILTER_SCAN + 1)
                )
            )
            .scalars()
            .all()
        )
    except Exception as exc:  # noqa: BLE001 — fail closed，过滤故障不得变成越权检索
        from app.rag.errors import RetrievalUnavailableError

        raise RetrievalUnavailableError("知识库过滤查询失败，检索已拒绝") from exc

    if len(rows) > MAX_KB_FILTER_SCAN:
        from app.rag.errors import RetrievalUnavailableError

        raise RetrievalUnavailableError(
            f"知识库内文档超过 {MAX_KB_FILTER_SCAN} 篇，检索已拒绝；请缩小范围"
        )
    return [str(doc_id) for doc_id in rows]
