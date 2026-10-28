"""文档核验身份、readiness 与原文证据读取（合同核验场景 Batch 1）。

口径（与 ``audit_contract_review_gap_20261027.md`` 对齐）：

- 原文证据只来自 ``DocumentChunk.original_text``，绝不回退 ``chunk_text``；
- legacy（缺 parser 原文）文档在 evidence 读取时显式 409 ``needs_rebuild``；
- readiness 区分硬阻断（未索引/解析失败/无分块/legacy/缺核验身份）与提示
  （页码覆盖、条款候选覆盖），不把「能预览」冒充「能核验」；
- 内容哈希在注册时从 MinIO 对象流式计算并固化，不使用可空的 ``Document.md5_hash``。

本模块不依赖 ES/模型；证据与 readiness 都从 DB 分块行读取。
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.minio_client import minio_client, normalize_object_name
from app.exceptions import ConflictError, ServiceUnavailableError, ValidationError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.review import DOC_ROLES, ReviewDocumentMeta
from app.rag.source_provenance import (
    PARSER_ORIGINAL,
    SourceProvenanceUnavailableError,
    provenance_of,
)
from app.services.clause_refs import extract_clause_fields

READINESS_REVIEWABLE = "reviewable"
READINESS_BLOCKED = "blocked"

# 与文档预览一致的有界扫描：条款候选只用于 readiness 提示，不承担证据输出。
_CHUNK_PAGE_SIZE = 500
MAX_EVIDENCE_LIMIT = 20
# 证据扫描的硬上限：过滤可能命中很晚，不能让单请求无界扫完整库。
MAX_EVIDENCE_SCAN_CHUNKS = 5_000


@dataclass(frozen=True)
class ReviewMetaInput:
    doc_role: str
    family_key: str | None = None
    version_label: str | None = None
    version_seq: int | None = None
    supersedes_document_id: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None


@dataclass(frozen=True)
class ReviewReadiness:
    document_id: str
    status: str
    blocking_reasons: list[str]
    warnings: list[str]
    total_chunks: int
    original_text_chunks: int
    page_number_chunks: int
    clause_candidate_chunks: int
    legacy_chunk_count: int
    meta: dict[str, Any] | None


@dataclass(frozen=True)
class EvidenceResult:
    document_id: str
    document_sha256: str | None
    meta: dict[str, Any] | None
    items: list[dict[str, Any]]
    total_original_chunks: int
    legacy_chunk_count: int
    returned: int
    scanned_chunks: int
    truncated: bool
    scan_truncated: bool = False


async def get_review_meta(session: AsyncSession, document_id: str) -> ReviewDocumentMeta | None:
    return await session.get(ReviewDocumentMeta, document_id)


def meta_to_dict(meta: ReviewDocumentMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {
        "doc_role": meta.doc_role,
        "family_key": meta.family_key,
        "version_label": meta.version_label,
        "version_seq": meta.version_seq,
        "supersedes_document_id": meta.supersedes_document_id,
        "effective_from": meta.effective_from.isoformat() if meta.effective_from else None,
        "effective_to": meta.effective_to.isoformat() if meta.effective_to else None,
        "content_sha256": meta.content_sha256,
        "registered_by": meta.registered_by,
    }


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


async def _sha256_storage_object(file_path: str) -> str:
    """从 MinIO 对象流式计算 sha256；失败必须阻止注册，不得用 md5 顶替。"""

    def _hash() -> str:
        response = minio_client.get_object(normalize_object_name(file_path))
        digest = hashlib.sha256()
        try:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        finally:
            response.close()
            response.release_conn()
        return digest.hexdigest()

    try:
        return await asyncio.to_thread(_hash)
    except Exception as exc:  # noqa: BLE001 - 存储层异常统一 fail closed
        raise ServiceUnavailableError(
            "无法读取文档存储对象计算内容哈希，核验身份未注册",
            detail={"reason": type(exc).__name__},
        ) from exc


async def upsert_review_meta(
    session: AsyncSession,
    document: Document,
    data: ReviewMetaInput,
    user_id: int,
) -> ReviewDocumentMeta:
    """注册/更新文档核验身份；合同必须带 family/version，哈希服务端计算。"""
    if data.doc_role not in DOC_ROLES:
        raise ValidationError(f"未知文档角色: {data.doc_role!r}")
    family_key = _normalize_text(data.family_key)
    version_label = _normalize_text(data.version_label)
    supersedes = _normalize_text(data.supersedes_document_id)

    if data.doc_role == "contract":
        missing = [
            name
            for name, value in (
                ("family_key", family_key),
                ("version_label", version_label),
                ("version_seq", data.version_seq),
            )
            if value is None
        ]
        if missing:
            raise ValidationError(f"合同核验身份缺少: {', '.join(missing)}")
    if data.version_seq is not None and version_label is None:
        raise ValidationError("version_seq 必须与 version_label 同时提供")
    effective_from = _as_utc(data.effective_from)
    effective_to = _as_utc(data.effective_to)
    if effective_from and effective_to and effective_from > effective_to:
        raise ValidationError("effective_from 不能晚于 effective_to")

    if supersedes:
        if supersedes == document.id:
            raise ValidationError("supersedes_document_id 不能指向自身")
        previous = await session.get(Document, supersedes)
        if previous is None or previous.organization_id != document.organization_id:
            raise ValidationError("supersedes_document_id 必须是同组织的已存在文档")

    # 只有同时给出 family/version 才构成可比较的版本身份；制度/模板允许
    # 不带版本（可多份未版本化），不会被这里误判为重复。
    if family_key is not None and version_label is not None:
        duplicate_stmt = (
            select(ReviewDocumentMeta.document_id)
            .where(
                ReviewDocumentMeta.organization_id == document.organization_id,
                ReviewDocumentMeta.doc_role == data.doc_role,
                ReviewDocumentMeta.document_id != document.id,
                ReviewDocumentMeta.family_key == family_key,
                ReviewDocumentMeta.version_label == version_label,
            )
            .limit(1)
        )
        duplicate = (await session.execute(duplicate_stmt)).scalar_one_or_none()
        if duplicate is not None:
            raise ConflictError(
                "同组织/角色/版本族/版本已注册到其他文档",
                error_code="REVIEW_META_DUPLICATE",
            )
    # 同族版本序也必须唯一（审计 §1.3：同族版本序冲突 409）。
    if family_key is not None and data.version_seq is not None:
        seq_stmt = (
            select(ReviewDocumentMeta.document_id)
            .where(
                ReviewDocumentMeta.organization_id == document.organization_id,
                ReviewDocumentMeta.doc_role == data.doc_role,
                ReviewDocumentMeta.document_id != document.id,
                ReviewDocumentMeta.family_key == family_key,
                ReviewDocumentMeta.version_seq == data.version_seq,
            )
            .limit(1)
        )
        seq_duplicate = (await session.execute(seq_stmt)).scalar_one_or_none()
        if seq_duplicate is not None:
            raise ConflictError(
                "同版本族已存在相同 version_seq 的文档",
                error_code="REVIEW_META_VERSION_SEQ_CONFLICT",
            )

    content_sha256 = await _sha256_storage_object(document.file_path)
    meta = await session.get(ReviewDocumentMeta, document.id)
    if meta is None:
        meta = ReviewDocumentMeta(
            document_id=document.id,
            organization_id=document.organization_id,
            registered_by=user_id,
            content_sha256=content_sha256,
            doc_role=data.doc_role,
        )
        session.add(meta)
    meta.doc_role = data.doc_role
    meta.family_key = family_key
    meta.version_label = version_label
    meta.version_seq = data.version_seq
    meta.supersedes_document_id = supersedes
    meta.effective_from = effective_from
    meta.effective_to = effective_to
    meta.content_sha256 = content_sha256
    meta.registered_by = user_id
    try:
        await session.commit()
    except IntegrityError as exc:
        # 并发 PUT 绕过只读重复检查：DB 唯一约束是最后防线，映射为 409。
        await session.rollback()
        raise ConflictError(
            "同组织/角色/版本族/版本已注册到其他文档",
            error_code="REVIEW_META_DUPLICATE",
        ) from exc
    await session.refresh(meta)
    return meta


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _chunk_original_text(chunk: DocumentChunk) -> str | None:
    """只有正文非空且写入方标记 parser_original 才算可证据原文。

    与 retrieval/preview 同口径：缺标记/标记不符一律 legacy，防止未来回填
    把增强文本伪装成原文。
    """
    meta = chunk.meta_data
    if not isinstance(meta, dict) or provenance_of(meta) != PARSER_ORIGINAL:
        return None
    text = chunk.original_text
    if isinstance(text, str) and text.strip():
        return text
    return None


async def _iter_chunks(session: AsyncSession, document_id: str, *, max_chunks: int | None = None):
    """按 chunk_index 分页遍历分块；可选上限用于证据扫描的边界。"""
    offset = 0
    scanned = 0
    while True:
        rows = (
            await session.execute(
                select(
                    DocumentChunk.id,
                    DocumentChunk.chunk_index,
                    DocumentChunk.original_text,
                    DocumentChunk.start_pos,
                    DocumentChunk.end_pos,
                    DocumentChunk.page_number,
                    DocumentChunk.section_title,
                    DocumentChunk.meta_data,
                )
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index.asc(), DocumentChunk.id.asc())
                .offset(offset)
                .limit(_CHUNK_PAGE_SIZE)
            )
        ).all()
        if not rows:
            return
        for chunk in rows:
            if max_chunks is not None and scanned >= max_chunks:
                return
            scanned += 1
            yield chunk
        offset += _CHUNK_PAGE_SIZE


async def _scan_review_chunks(
    session: AsyncSession, document_id: str
) -> tuple[int, int, int, int, int]:
    """返回 (SQL 总数, 实际扫描数, parser 原文块数, 页码块数, 条款候选块数)。"""
    total = int(
        (
            await session.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document_id)
            )
        ).scalar()
        or 0
    )
    scanned = 0
    original = 0
    page_chunks = 0
    clause_candidates = 0
    async for chunk in _iter_chunks(session, document_id):
        scanned += 1
        if chunk.page_number is not None:
            page_chunks += 1
        text = _chunk_original_text(chunk)
        if text is None:
            continue
        original += 1
        fields = extract_clause_fields(text)
        if fields["clause_no"] or fields["section_no"]:
            clause_candidates += 1
    return total, scanned, original, page_chunks, clause_candidates


async def compute_readiness(session: AsyncSession, document: Document) -> ReviewReadiness:
    (
        total_chunks,
        scanned,
        original_chunks,
        page_chunks,
        clause_candidates,
    ) = await _scan_review_chunks(session, document.id)
    legacy_chunk_count = scanned - original_chunks
    meta = await get_review_meta(session, document.id)

    blocking: list[str] = []
    warnings: list[str] = []
    if document.status == DocumentStatus.FAILED:
        blocking.append("parse_failed")
    elif document.status != DocumentStatus.INDEXED:
        blocking.append("not_indexed")
    if total_chunks == 0:
        blocking.append("no_chunks")
    elif original_chunks == 0:
        blocking.append("no_original_text")
    elif legacy_chunk_count > 0:
        blocking.append("legacy_unverified")
    if meta is None:
        blocking.append("missing_version_meta")
    else:
        if meta.organization_id != document.organization_id:
            blocking.append("wrong_org")
        if meta.doc_role == "contract" and (
            not meta.family_key or not meta.version_label or meta.version_seq is None
        ):
            blocking.append("missing_version_meta")

    if total_chunks and page_chunks == 0:
        warnings.append("no_page_info")
    elif total_chunks and page_chunks < total_chunks:
        warnings.append("partial_page_info")
    if original_chunks and clause_candidates == 0:
        warnings.append("no_clause_candidates")
    if total_chunks > MAX_EVIDENCE_SCAN_CHUNKS:
        # evidence 有硬扫描上限；超过后 readiness 仍可能 reviewable，但证据
        # 接口只能覆盖前 5000 块，必须提前提示，避免“可核验”被误解为全量可达。
        warnings.append("evidence_scan_limited")
    if scanned != total_chunks:
        # 并发重建/删除期间的快照不一致：不得把不完整扫描当成完整覆盖。
        warnings.append("scan_incomplete")
        if legacy_chunk_count > 0:
            blocking.append("legacy_unverified")

    return ReviewReadiness(
        document_id=document.id,
        status=READINESS_BLOCKED if blocking else READINESS_REVIEWABLE,
        blocking_reasons=blocking,
        warnings=warnings,
        total_chunks=total_chunks,
        original_text_chunks=original_chunks,
        page_number_chunks=page_chunks,
        clause_candidate_chunks=clause_candidates,
        legacy_chunk_count=legacy_chunk_count,
        meta=meta_to_dict(meta),
    )


def readiness_to_dict(readiness: ReviewReadiness) -> dict[str, Any]:
    return {
        "document_id": readiness.document_id,
        "status": readiness.status,
        "blocking_reasons": readiness.blocking_reasons,
        "warnings": readiness.warnings,
        "total_chunks": readiness.total_chunks,
        "original_text_chunks": readiness.original_text_chunks,
        "page_number_chunks": readiness.page_number_chunks,
        "clause_candidate_chunks": readiness.clause_candidate_chunks,
        "legacy_chunk_count": readiness.legacy_chunk_count,
        "meta": readiness.meta,
    }


async def query_evidence(
    session: AsyncSession,
    document: Document,
    *,
    clause_no: int | None = None,
    section_no: str | None = None,
    query: str | None = None,
    limit: int = 5,
    offset: int = 0,
) -> EvidenceResult:
    """按条款/章节/关键字读取 parser 原文证据；legacy 回退被显式拒绝。"""
    limit = max(1, min(limit, MAX_EVIDENCE_LIMIT))
    total_chunks = int(
        (
            await session.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id)
            )
        ).scalar()
        or 0
    )
    if total_chunks == 0:
        raise ConflictError(
            "文档没有可核验的分块；请先完成解析/索引",
            error_code="NO_REVIEWABLE_CHUNKS",
            detail={"document_id": document.id},
        )

    meta = await get_review_meta(session, document.id)
    meta_dict = meta_to_dict(meta)
    needle = (query or "").strip().lower()
    items: list[dict[str, Any]] = []
    matched = 0
    truncated = False
    scanned = 0
    original_chunks = 0
    async for chunk in _iter_chunks(session, document.id, max_chunks=MAX_EVIDENCE_SCAN_CHUNKS):
        scanned += 1
        text = _chunk_original_text(chunk)
        if text is None:
            continue
        original_chunks += 1
        fields = extract_clause_fields(text)
        if clause_no is not None and clause_no not in fields["clause_no"]:
            continue
        if section_no and section_no not in fields["section_no"]:
            continue
        if needle and needle not in text.lower():
            continue
        matched += 1
        if matched <= offset:
            continue
        if len(items) >= limit:
            truncated = True
            break
        items.append(
            {
                "chunk_id": chunk.id,
                "chunk_index": chunk.chunk_index,
                "quote": text,
                "quote_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "char_start": chunk.start_pos,
                "char_end": chunk.end_pos,
                "page_number": chunk.page_number,
                "section_title": chunk.section_title,
                "clause_no": fields["clause_no"],
                "section_no": fields["section_no"],
                "text_provenance": "parser_original",
                "document_id": document.id,
                "document_sha256": meta.content_sha256 if meta else None,
                "version_label": meta.version_label if meta else None,
                "version_seq": meta.version_seq if meta else None,
                "doc_role": meta.doc_role if meta else None,
            }
        )

    scan_truncated = scanned >= MAX_EVIDENCE_SCAN_CHUNKS and scanned < total_chunks
    if original_chunks == 0 and not scan_truncated:
        raise SourceProvenanceUnavailableError([document.id], unverified_count=scanned)

    return EvidenceResult(
        document_id=document.id,
        document_sha256=meta.content_sha256 if meta else None,
        meta=meta_dict,
        items=items,
        total_original_chunks=original_chunks,
        legacy_chunk_count=scanned - original_chunks,
        returned=len(items),
        scanned_chunks=scanned,
        truncated=truncated,
        scan_truncated=scan_truncated,
    )


def evidence_to_dict(result: EvidenceResult) -> dict[str, Any]:
    return {
        "document_id": result.document_id,
        "document_sha256": result.document_sha256,
        "meta": result.meta,
        "items": result.items,
        "total_original_chunks": result.total_original_chunks,
        "legacy_chunk_count": result.legacy_chunk_count,
        "returned": result.returned,
        "scanned_chunks": result.scanned_chunks,
        "truncated": result.truncated,
        "scan_truncated": result.scan_truncated,
    }
