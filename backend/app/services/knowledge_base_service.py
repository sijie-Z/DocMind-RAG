"""组织内知识库实体（多库）的 CRUD 服务。

所有读写都以 ``organization_id`` 为第一过滤条件；跨组织 id 一律按「不存在」
处理（404），不回显存在性。删除知识库不删除任何文档：所属文档仅解除关联
（``knowledge_base_id=NULL``），回到旧的「组织级默认库」语义。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import AppError, NotFoundError, ValidationError
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.models.public_kb import DocumentPublication, KbPublication

MAX_PAGE_SIZE = 100
MAX_NAME_LENGTH = 200


def _validate_name(name: str) -> str:
    value = name.strip() if isinstance(name, str) else ""
    if not value or len(value) > MAX_NAME_LENGTH:
        raise ValidationError(f"知识库名称必须为 1–{MAX_NAME_LENGTH} 个字符")
    return value


def _validate_org_id(organization_id: int) -> int:
    if (
        isinstance(organization_id, bool)
        or not isinstance(organization_id, int)
        or organization_id < 1
    ):
        raise ValidationError("organization_id 必须是正整数")
    return organization_id


class KnowledgeBaseService:
    """知识库实体服务（无进程内状态，全部查询按租户收敛）。"""

    async def create(
        self,
        db: AsyncSession,
        organization_id: int,
        *,
        name: str,
        description: str | None = None,
        created_by: int | None = None,
    ) -> KnowledgeBase:
        org_id = _validate_org_id(organization_id)
        kb = KnowledgeBase(
            id=str(uuid.uuid4()),
            organization_id=org_id,
            name=_validate_name(name),
            description=description,
            created_by=created_by,
        )
        db.add(kb)
        try:
            await db.commit()
            await db.refresh(kb)
        except AppError:
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            raise AppError(f"创建知识库失败: {type(exc).__name__}") from exc
        return kb

    async def list(
        self,
        db: AsyncSession,
        organization_id: int,
        *,
        page: int = 1,
        page_size: int = 10,
        search: str | None = None,
    ) -> tuple[list[KnowledgeBase], int]:
        org_id = _validate_org_id(organization_id)
        conditions = [KnowledgeBase.organization_id == org_id]
        if search and search.strip():
            conditions.append(KnowledgeBase.name.contains(search.strip()))

        total = (
            await db.execute(select(func.count(KnowledgeBase.id)).where(*conditions))
        ).scalar() or 0
        safe_page = max(1, page)
        safe_size = max(1, min(page_size, MAX_PAGE_SIZE))
        rows = (
            (
                await db.execute(
                    select(KnowledgeBase)
                    .where(*conditions)
                    .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
                    .offset((safe_page - 1) * safe_size)
                    .limit(safe_size)
                )
            )
            .scalars()
            .all()
        )
        return list(rows), int(total)

    async def get(
        self, db: AsyncSession, organization_id: int, knowledge_base_id: str
    ) -> KnowledgeBase:
        org_id = _validate_org_id(organization_id)
        if not knowledge_base_id:
            raise NotFoundError("知识库不存在")
        kb = (
            await db.execute(
                select(KnowledgeBase).where(
                    KnowledgeBase.id == knowledge_base_id,
                    KnowledgeBase.organization_id == org_id,
                )
            )
        ).scalar_one_or_none()
        if kb is None:
            # 跨组织与不存在同形：不回显存在性。
            raise NotFoundError("知识库不存在")
        return kb

    async def update(
        self,
        db: AsyncSession,
        organization_id: int,
        knowledge_base_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> KnowledgeBase:
        # 先按 org 取行，再改字段：任何跨组织更新都在 get 处 404，
        # 不会以 id 直接 UPDATE 命中别的租户。
        kb = await self.get(db, organization_id, knowledge_base_id)
        if name is not None:
            kb.name = _validate_name(name)
        if description is not None:
            kb.description = description
        kb.updated_at = datetime.utcnow()
        try:
            await db.commit()
            await db.refresh(kb)
        except AppError:
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            raise AppError(f"更新知识库失败: {type(exc).__name__}") from exc
        return kb

    async def delete(self, db: AsyncSession, organization_id: int, knowledge_base_id: str) -> None:
        # 同样先做 org 边界校验；后续解除关联仅命中本组织文档。
        kb = await self.get(db, organization_id, knowledge_base_id)
        try:
            # 批次十三：知识库删除后其公开发布不再有任何意义（文档会被解除
            # 关联，白名单检索必然为空）。显式清理发布与白名单行，避免悬空
            # 公开页继续暴露旧标题/描述；先清白名单再清发布头（FK 顺序）。
            publication_ids = select(KbPublication.id).where(
                KbPublication.organization_id == organization_id,
                KbPublication.knowledge_base_id == knowledge_base_id,
            )
            await db.execute(
                delete(DocumentPublication).where(
                    DocumentPublication.organization_id == organization_id,
                    DocumentPublication.publication_id.in_(publication_ids),
                )
            )
            await db.execute(
                delete(KbPublication).where(
                    KbPublication.organization_id == organization_id,
                    KbPublication.knowledge_base_id == knowledge_base_id,
                )
            )
            await db.execute(
                Document.__table__.update()
                .where(
                    Document.organization_id == organization_id,
                    Document.knowledge_base_id == knowledge_base_id,
                )
                .values(knowledge_base_id=None)
            )
            await db.delete(kb)
            await db.commit()
        except AppError:
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            raise AppError(f"删除知识库失败: {type(exc).__name__}") from exc


knowledge_base_service = KnowledgeBaseService()
