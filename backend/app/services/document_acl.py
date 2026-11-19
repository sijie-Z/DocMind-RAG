"""文档级 ACL 的查询收口：从 DB 推导「当前用户不可见的文档 ID」。

设计取舍：不在 ES 索引 permitted_user_ids（避免重索引与 mapping 漂移）。
私有文档的授权关系在 DB，每次检索开始时读一次，把不可见文档 ID 作为
``must_not terms document_id`` 交给各检索臂；授权撤销后立即生效。

无 viewer（内部系统检索）时返回空列表：保持既有组织级行为，不改变
后台任务/对账脚本的语义。所有面向用户的路径都必须传 viewer_user_id。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_blocked_document_ids(
    db: AsyncSession,
    organization_id: int,
    viewer_user_id: int | None,
) -> list[str]:
    """返回 organization_id 下 viewer 无权查看的私有文档 ID。

    - viewer_user_id 为 None（内部系统检索）→ []（组织级行为不变）
    - 组织可见（visibility=organization 或旧库无该字段）→ 不屏蔽
    - 私有：上传者本人或 document_acl 中有授权 → 不屏蔽；其余屏蔽
    - DB 异常（如表未迁移）→ fail closed：把该组织的私有文档全部屏蔽，
      宁可少召回，不把私有文档泄漏给无权限者。
    """
    if viewer_user_id is None:
        return []

    try:
        from app.models.document import Document, DocumentAcl, DocumentVisibility

        private_docs = (
            await db.execute(
                select(Document.id, Document.uploaded_by).where(
                    Document.organization_id == organization_id,
                    Document.visibility == DocumentVisibility.PRIVATE,
                )
            )
        ).all()
        if not private_docs:
            return []

        doc_ids = [row.id for row in private_docs]
        granted = set(
            (
                await db.execute(
                    select(DocumentAcl.document_id).where(
                        DocumentAcl.document_id.in_(doc_ids),
                        DocumentAcl.subject_type == "user",
                        DocumentAcl.subject_id == viewer_user_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        return [
            row.id
            for row in private_docs
            if row.uploaded_by != viewer_user_id and row.id not in granted
        ]
    except Exception as exc:  # noqa: BLE001 — fail closed，不把 ACL 故障变成越权
        # 只记异常类型：异常对象可能夹带查询串/用户数据，不能明文入日志
        logger.warning(
            "文档 ACL 查询失败，按 fail-closed 屏蔽私有文档（org=%s, user=%s, error=%s）",
            organization_id,
            viewer_user_id,
            type(exc).__name__,
        )
        try:
            from app.models.document import Document

            return list(
                (
                    await db.execute(
                        select(Document.id).where(
                            Document.organization_id == organization_id,
                            Document.visibility == "private",
                        )
                    )
                )
                .scalars()
                .all()
            )
        except Exception:
            # 二次失败（DB 断连/迁移缺列）：不能放行私有文档。抛检索不可用，
            # 让上层以「服务不可用」诚实失败，而非静默返回组织级全量结果。
            from app.rag.errors import RetrievalUnavailableError

            raise RetrievalUnavailableError("文档 ACL 查询失败，检索已拒绝") from exc


def with_acl_filter(filters: list[dict[str, Any]], blocked_ids: list[str]) -> list[dict[str, Any]]:
    """把不可见文档追加为 ``must_not terms``（空列表时原样返回）。"""
    if not blocked_ids:
        return filters
    return [*filters, {"bool": {"must_not": [{"terms": {"document_id": blocked_ids}}]}}]
