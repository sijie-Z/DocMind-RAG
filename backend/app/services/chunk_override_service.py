"""Chunk 浏览与人工修正服务（批次四 · 高溯源风险面）。

不变量（判别测试锚点）：

- 本模块**从不写** ``DocumentChunk.chunk_text`` / ``original_text`` /
  ``meta_data``；合同证据的 ``quote_sha256`` 属于另一张不可变快照表，
  本模块更不会触碰。人工文本只存在 ``DocumentChunkOverride.override_text``。
- 人工文本进入检索时显式标 ``manual_override``；strict 原文 replay 只认
  ``parser_original``，因此人工文本不能伪装 parser 原文。
- 所有读写入口都先经 ``get_document_for_user``（组织 + 私有文档 ACL），
  viewer / 组织 / 文档 id 全部服务端派生，不接受客户端声明。
- 分块级禁用与人工文本都直接刷 ES ``_source`` 字段，检索过滤随即生效；
  缓存按 org 清理。向量不重算，因此改动不影响语义向量臂的旧向量。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_document_for_user, is_org_admin
from app.core.time_utils import utcnow
from app.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.models.document import (
    Document,
    DocumentChunk,
    DocumentChunkOverride,
    DocumentVisibility,
)
from app.models.user import User
from app.rag.source_provenance import (
    LEGACY_UNVERIFIED,
    MANUAL_OVERRIDE,
    PARSER_ORIGINAL,
)
from app.services.document_preview import _is_original

logger = logging.getLogger(__name__)

MAX_OVERRIDE_CHARS = 100_000
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20


@dataclass(frozen=True)
class ChunkBrowserItem:
    chunk_id: str
    chunk_index: int
    document_id: str
    document_title: str
    indexed_text: str
    parser_original_available: bool
    parser_original_text: str | None
    text_provenance: str
    page_number: int | None
    start_pos: int | None
    end_pos: int | None
    section_title: str | None
    clause_no: list[int]
    section_no: list[str]
    is_enabled: bool
    override_state: str
    effective_text_provenance: str
    override_text: str | None
    override_id: str | None
    override_updated_at: datetime | None


def _override_state(override: DocumentChunkOverride | None) -> str:
    """单一投影口径：draft / active / disabled / none。

    browse_chunks 的过滤和写入响应共用此口径，避免「列表说 draft、详情说 none」
    这类前端 reset/徽标漂移。disabled 优先于 active：禁用块即便有生效修正，
    也必须显出 disabled，防止 UI 暗示它还在参与检索。
    """
    if override is None:
        return "none"
    if not bool(override.is_enabled):
        return "disabled"
    if (
        bool(override.apply_to_retrieval)
        and isinstance(override.override_text, str)
        and override.override_text.strip()
    ):
        return "active"
    if isinstance(override.override_text, str) and override.override_text.strip():
        return "draft"
    return "none"


def _meta_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clause_numbers(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        if isinstance(item, int) and not isinstance(item, bool) and item not in result:
            result.append(item)
    return result


def _section_numbers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({str(item) for item in value if isinstance(item, str) and item})


def _locator_fields(
    meta: dict[str, Any], original_text: str | None, indexed_text: str
) -> tuple[list[int], list[str]]:
    """结构化定位字段优先取 meta，缺失时按索引侧同口径从 parser 原文派生。

    真实索引路径只在 ES `_source` 顶层写 clause_no / section_no，DB 的
    ``meta_data`` 并不保存它们。浏览器若只读 meta 会永远显示空定位；这里
    复用 ``app.services.clause_refs.extract_clause_fields``（与 indexer 同一
    来源）从 parser 原文派生，人工文本不参与，不能污染定义位判定。
    """
    if isinstance(meta.get("clause_no"), list) or isinstance(meta.get("section_no"), list):
        return (
            _clause_numbers(meta.get("clause_no")),
            _section_numbers(meta.get("section_no")),
        )
    from app.services.clause_refs import extract_clause_fields

    fields = extract_clause_fields(original_text or indexed_text)
    return (
        _clause_numbers(fields.get("clause_no")),
        _section_numbers(fields.get("section_no")),
    )


def chunk_to_item(
    chunk: DocumentChunk,
    document: Document,
    override: DocumentChunkOverride | None,
) -> ChunkBrowserItem:
    """把 DB 行投影成浏览项；原文标记严格沿用 document_preview 的口径。"""
    meta = _meta_dict(chunk.meta_data)
    original_available = _is_original(chunk.original_text, meta)
    clauses, sections = _locator_fields(meta, chunk.original_text, chunk.chunk_text or "")
    active_override = (
        override
        if override is not None
        and bool(override.apply_to_retrieval)
        and isinstance(override.override_text, str)
        and override.override_text.strip()
        else None
    )
    return ChunkBrowserItem(
        chunk_id=chunk.id,
        chunk_index=chunk.chunk_index,
        document_id=document.id,
        document_title=document.title or document.filename,
        indexed_text=chunk.chunk_text or "",
        parser_original_available=original_available,
        parser_original_text=chunk.original_text if original_available else None,
        text_provenance=PARSER_ORIGINAL if original_available else LEGACY_UNVERIFIED,
        page_number=chunk.page_number,
        start_pos=chunk.start_pos,
        end_pos=chunk.end_pos,
        section_title=meta.get("section_title") or chunk.section_title,
        clause_no=clauses,
        section_no=sections,
        is_enabled=bool(override.is_enabled) if override is not None else True,
        # 与 browse_chunks 的 override_state 过滤（active/draft/disabled/none）
        # 用同一套判定：UI reset 按钮也必须按这里的结果显隐。
        override_state=_override_state(override),
        effective_text_provenance=MANUAL_OVERRIDE
        if active_override is not None
        else (PARSER_ORIGINAL if original_available else LEGACY_UNVERIFIED),
        override_text=override.override_text if override is not None else None,
        override_id=override.id if override is not None else None,
        override_updated_at=override.updated_at if override is not None else None,
    )


def item_to_dict(item: ChunkBrowserItem) -> dict[str, Any]:
    return {
        "chunk_id": item.chunk_id,
        "chunk_index": item.chunk_index,
        "document_id": item.document_id,
        "document_title": item.document_title,
        "indexed_text": item.indexed_text,
        "parser_original_available": item.parser_original_available,
        "parser_original_text": item.parser_original_text,
        "text_provenance": item.text_provenance,
        "page_number": item.page_number,
        "start_pos": item.start_pos,
        "end_pos": item.end_pos,
        "section_title": item.section_title,
        "clause_no": item.clause_no,
        "section_no": item.section_no,
        "is_enabled": item.is_enabled,
        "override_state": item.override_state,
        "effective_text_provenance": item.effective_text_provenance,
        "override_text": item.override_text,
        "override_id": item.override_id,
        "override_updated_at": (
            item.override_updated_at.isoformat() if item.override_updated_at else None
        ),
    }


async def load_chunk_overrides(
    db: AsyncSession, chunk_ids: list[str]
) -> dict[str, DocumentChunkOverride]:
    if not chunk_ids:
        return {}
    rows = (
        (
            await db.execute(
                select(DocumentChunkOverride).where(
                    DocumentChunkOverride.document_chunk_id.in_(chunk_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.document_chunk_id: row for row in rows}


async def browse_chunks(
    db: AsyncSession,
    current_user: User,
    document_id: str,
    *,
    page: int,
    page_size: int,
    enabled: bool | None = None,
    override_state: str | None = None,
    query: str | None = None,
) -> dict[str, Any]:
    """分页浏览**单篇已授权文档**的 chunk；viewer 上下文完全服务端派生。"""
    doc = await get_document_for_user(db, current_user, document_id)
    conditions = [DocumentChunk.document_id == doc.id]
    if enabled is not None:
        enabled_expr = case(
            (DocumentChunkOverride.is_enabled.is_(None), True),
            else_=DocumentChunkOverride.is_enabled,
        )
        conditions.append(enabled_expr == enabled)
    # 与 _override_state() 同口径：disabled 优先，draft/active 只在启用块里判定。
    not_disabled = case(
        (DocumentChunkOverride.is_enabled.is_(None), True),
        else_=DocumentChunkOverride.is_enabled,
    )
    has_text = func.length(func.coalesce(DocumentChunkOverride.override_text, "")) > 0
    if override_state == "active":
        conditions.append(not_disabled)
        conditions.append(DocumentChunkOverride.apply_to_retrieval.is_(True))
        conditions.append(has_text)
    elif override_state == "draft":
        conditions.append(not_disabled)
        conditions.append(DocumentChunkOverride.apply_to_retrieval.is_(False))
        conditions.append(has_text)
    elif override_state == "disabled":
        conditions.append(DocumentChunkOverride.is_enabled.is_(False))
    needle = (query or "").strip()
    if needle:
        conditions.append(
            func.lower(DocumentChunk.chunk_text).contains(needle.lower())
            | func.lower(func.coalesce(DocumentChunk.original_text, "")).contains(needle.lower())
        )
    join = DocumentChunkOverride.document_chunk_id == DocumentChunk.id
    total = int(
        (
            await db.execute(
                select(func.count(DocumentChunk.id))
                .outerjoin(DocumentChunkOverride, join)
                .where(*conditions)
            )
        ).scalar()
        or 0
    )
    rows = (
        await db.execute(
            select(DocumentChunk, DocumentChunkOverride)
            .outerjoin(DocumentChunkOverride, join)
            .where(*conditions)
            .order_by(DocumentChunk.chunk_index.asc(), DocumentChunk.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [item_to_dict(chunk_to_item(chunk, doc, override)) for chunk, override in rows]
    return {
        "success": True,
        "data": {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": (total + page_size - 1) // page_size,
            "document": {
                "id": doc.id,
                "title": doc.title or doc.filename,
                "organization_id": doc.organization_id,
            },
        },
    }


def _actor_can_write(doc: Document, user: User, is_admin: bool) -> bool:
    """私有文档的 chunk 写入仅限上传者/组织管理员（读权限不等于写权限）。"""
    private = doc.visibility == DocumentVisibility.PRIVATE or (
        isinstance(doc.visibility, str) and doc.visibility == DocumentVisibility.PRIVATE.value
    )
    return bool(is_admin or not private or doc.uploaded_by == user.id)


async def load_chunk_for_update(
    db: AsyncSession, current_user: User, document_id: str, chunk_id: str
) -> tuple[Document, DocumentChunk, DocumentChunkOverride | None]:
    """单块写入前的统一授权 + 归属校验；只返回该文档自己的分块。"""
    doc = await get_document_for_user(db, current_user, document_id)
    if not _actor_can_write(
        doc, current_user, await is_org_admin(db, current_user, doc.organization_id)
    ):
        raise AuthorizationError("仅文档所有者或组织管理员可修正私有文档分块")
    chunk = await db.get(DocumentChunk, chunk_id)
    if chunk is None or chunk.document_id != doc.id:
        raise NotFoundError("文档分块不存在")
    override = (
        await db.execute(
            select(DocumentChunkOverride).where(DocumentChunkOverride.document_chunk_id == chunk.id)
        )
    ).scalar_one_or_none()
    return doc, chunk, override


def validate_override_text(text: str | None) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("修正文本不能为空白；如需清空请使用撤销接口")
    if len(text) > MAX_OVERRIDE_CHARS:
        raise ValidationError(f"修正文本不能超过 {MAX_OVERRIDE_CHARS} 字符")
    return text


async def _sync_es(
    db: AsyncSession,
    doc: Document,
    chunk: DocumentChunk,
    *,
    enabled: bool,
    retrieval_text: str | None,
    apply_to_retrieval: bool,
) -> None:
    """把分块级状态同步到 ES；失败则整笔失败，避免 DB/ES 状态漂移。"""
    from app.core.elasticsearch import ElasticsearchTools

    fields: dict[str, Any] = {"is_chunk_enabled": bool(enabled)}
    if apply_to_retrieval and retrieval_text is not None:
        # 检索文本换成人工文本，但**不碰** original_text / text_provenance：
        # 后者仍然只证明 parser 原文的真实性。另设独立字段标记检索文本来源，
        # strict replay 继续用 parser 原文，绝不会把人工文本当原文。
        fields.update(
            {
                "content": retrieval_text,
                "chunk_text": retrieval_text,
                "retrieval_text_provenance": MANUAL_OVERRIDE,
            }
        )
    else:
        # 关闭应用或撤销时恢复 parser 检索文本，并清掉人工来源标记。
        fields.update(
            {
                "content": chunk.chunk_text,
                "chunk_text": chunk.chunk_text,
                "retrieval_text_provenance": None,
            }
        )
    es_id = f"{doc.id}_{chunk.id}"
    try:
        await ElasticsearchTools.update_document(es_id, fields)
    except Exception as es_err:
        raise RuntimeError(f"同步检索索引失败，请稍后重试: {es_err}") from es_err
    try:
        from app.rag.cache import clear_all_for_org

        await clear_all_for_org(doc.organization_id)
    except Exception as cache_err:
        logger.warning("分块变更后清理检索缓存失败（org=%s）: %s", doc.organization_id, cache_err)


def _audit_detail(action: str, doc: Document, chunk: DocumentChunk, fields: dict[str, Any]) -> str:
    """审计只写 id / 布尔 / 哈希与字符数，绝不写正文（含私有文档原文）。"""
    digest = hashlib.sha256((fields.get("text") or "").encode("utf-8")).hexdigest()
    safe = {k: v for k, v in fields.items() if k != "text"}
    safe["text_sha256"] = digest
    safe["text_chars"] = len(fields.get("text") or "")
    return (
        f"action={action} document_id={doc.id} chunk_id={chunk.id} "
        f"organization_id={doc.organization_id} {safe}"
    )


async def _write_audit(
    db: AsyncSession,
    current_user: User,
    action: str,
    doc: Document,
    chunk: DocumentChunk,
    fields: dict[str, Any],
) -> None:
    from app.services.audit_service import audit_service

    await audit_service.log_activity(
        user_id=current_user.id,
        action=action,
        target_type="document_chunk",
        target_id=chunk.id,
        detail=_audit_detail(action, doc, chunk, fields),
        db=db,
    )


async def upsert_chunk_override(
    db: AsyncSession,
    current_user: User,
    document_id: str,
    chunk_id: str,
    *,
    text: str | None,
    apply_to_retrieval: bool,
) -> dict[str, Any]:
    """写入人工修正草稿/应用态；immutable parser 原文与 quote 哈希不动。"""
    doc, chunk, override = await load_chunk_for_update(db, current_user, document_id, chunk_id)
    if text is None and override is None:
        raise ValidationError("尚无可修正内容")
    clean_text = validate_override_text(text)
    if apply_to_retrieval and not clean_text:
        raise ValidationError("应用人工修正必须提供非空修正文本")
    if override is None:
        override = DocumentChunkOverride(
            id=str(uuid.uuid4()),
            document_chunk_id=chunk.id,
            document_id=doc.id,
            organization_id=doc.organization_id,
            created_by=current_user.id,
            is_enabled=True,
        )
        db.add(override)
    # 保存修正绝不改变启停：只有显式 PATCH /chunks/enabled 才能改 is_enabled。
    # 此前无条件置 True 会把已禁用 chunk 重新放回检索——这是一条静默越权路径。
    effective_enabled = bool(override.is_enabled)
    override.override_text = clean_text
    override.apply_to_retrieval = bool(apply_to_retrieval)
    override.updated_by = current_user.id
    # 显式赋值避免 onupdate 在 flush 后把该列标记为 expired ——
    # 随后同事务内投影响应会触发异步 lazy load（MissingGreenlet）。
    override.updated_at = utcnow()
    await db.flush()
    await _sync_es(
        db,
        doc,
        chunk,
        enabled=effective_enabled,
        retrieval_text=clean_text,
        apply_to_retrieval=bool(apply_to_retrieval),
    )
    await db.commit()
    await _write_audit(
        db,
        current_user,
        "upsert_chunk_override",
        doc,
        chunk,
        {
            "text": clean_text or "",
            "apply_to_retrieval": bool(apply_to_retrieval),
            "is_enabled": effective_enabled,
        },
    )
    return item_to_dict(chunk_to_item(chunk, doc, override))


async def set_chunk_enabled(
    db: AsyncSession,
    current_user: User,
    document_id: str,
    chunk_id: str,
    *,
    enabled: bool,
) -> dict[str, Any]:
    """分块级启停：同步刷 ES 字段，立即影响检索（旧索引缺字段按启用处理）。"""
    doc, chunk, override = await load_chunk_for_update(db, current_user, document_id, chunk_id)
    if override is None:
        override = DocumentChunkOverride(
            id=str(uuid.uuid4()),
            document_chunk_id=chunk.id,
            document_id=doc.id,
            organization_id=doc.organization_id,
            created_by=current_user.id,
            updated_by=current_user.id,
            is_enabled=bool(enabled),
        )
        db.add(override)
    else:
        override.is_enabled = bool(enabled)
        override.updated_by = current_user.id
        override.updated_at = utcnow()
    await db.flush()
    await _sync_es(
        db,
        doc,
        chunk,
        enabled=bool(enabled),
        retrieval_text=override.override_text,
        apply_to_retrieval=bool(override.apply_to_retrieval),
    )
    await db.commit()
    await _write_audit(
        db,
        current_user,
        "set_chunk_enabled",
        doc,
        chunk,
        {"enabled": bool(enabled)},
    )
    return item_to_dict(chunk_to_item(chunk, doc, override))


async def reset_chunk_override(
    db: AsyncSession,
    current_user: User,
    document_id: str,
    chunk_id: str,
) -> dict[str, Any]:
    """撤销人工修正：保留启停审计行，但恢复 parser 检索文本与原文标记。"""
    doc, chunk, override = await load_chunk_for_update(db, current_user, document_id, chunk_id)
    if override is None:
        raise NotFoundError("该分块没有人工修正")
    override.override_text = None
    override.apply_to_retrieval = False
    override.updated_by = current_user.id
    override.updated_at = utcnow()
    await db.flush()
    await _sync_es(
        db,
        doc,
        chunk,
        enabled=bool(override.is_enabled),
        retrieval_text=None,
        apply_to_retrieval=False,
    )
    await db.commit()
    await _write_audit(db, current_user, "reset_chunk_override", doc, chunk, {})
    return item_to_dict(chunk_to_item(chunk, doc, override))
