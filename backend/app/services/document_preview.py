"""文档正文预览：原文优先、旧分块显式降级、截断显式化。

PR4 的第一片（预览）：``/documents/{id}/content`` 之前直接拼接检索文本
``chunk_text``（可能带【文档背景：…】增强摘要）并静默截断在 1000 个 ES hit，
既会把生成摘要当成原文展示，也可能在长文档上悄悄丢内容。

本模块的口径：

- ``DocumentChunk.original_text`` 非空且写入方带 ``parser_original`` 标记才视为
  原文（与 retrieval 的 ``original_text_of`` 同口径；列定义见
  ``app/models/document.py``。缺标记/标记不符一律按 legacy 处理，防止未来
  回填/修复把检索增强文本伪装成原文）。预览优先使用它；
- 旧分块没有原文时回退 ``chunk_text``，但按块标记 ``legacy_unverified`` ——
  预览可以展示，但调用方必须知道这部分可能是生成文本，不能当原文证据；
- 返回 ``text_provenance``（对**实际返回的内容**而言）、原文/旧分块计数、
  总块数与 ``truncated``。任何未完整返回都显式标记，不再静默截断；
- DB 是分块行（含 original_text）的权威存储，优先读取；仅在 DB 没有任何分块行
  时才回退 ES。ES 回退同样只取显式字段并如实报告截断。

本模块不重建、不写库、不调用模型。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import DocumentChunk
from app.rag.source_provenance import (
    LEGACY_UNVERIFIED,
    PARSER_ORIGINAL,
    original_text_of,
    provenance_of,
)

# 预览不是导出：用有界预算防止超大文档把 API/浏览器拖垮，但超出时必须显式
# reported（truncated=True），不得像旧实现那样静默只取前 1000 个 hit。
PREVIEW_PAGE_SIZE = 500
PREVIEW_MAX_CHARS = 1_000_000
PREVIEW_MAX_CHUNKS = 5_000
# ES 回退只取一页；命中总数大于本页即 truncated=True。
ES_PREVIEW_SIZE = 1_000

TEXT_PROVENANCE_NONE = "none"
TEXT_PROVENANCE_MIXED = "mixed"


@dataclass(frozen=True)
class DocumentPreviewText:
    """一次预览读取的结果；所有计数都针对 ``content`` 实际覆盖的分块。"""

    content: str
    text_provenance: str
    original_chunk_count: int
    legacy_chunk_count: int
    total_chunks: int
    returned_chunks: int
    truncated: bool


async def load_document_preview_text(db: AsyncSession, document_id: str) -> DocumentPreviewText:
    """读取文档预览正文；先 DB（权威分块行），DB 无行时才回退 ES。"""
    total = (
        await db.execute(
            select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document_id)
        )
    ).scalar()
    total_chunks = int(total or 0)
    if total_chunks:
        return await _load_from_db(db, document_id, total_chunks)
    return await _load_from_es(document_id)


async def _load_from_db(
    db: AsyncSession, document_id: str, total_chunks: int
) -> DocumentPreviewText:
    parts: list[str] = []
    used_chars = 0
    returned = 0
    original_count = 0
    truncated = False
    offset = 0

    while offset < total_chunks:
        rows = (
            await db.execute(
                select(
                    DocumentChunk.original_text,
                    DocumentChunk.chunk_text,
                    DocumentChunk.meta_data,
                )
                .where(DocumentChunk.document_id == document_id)
                .order_by(DocumentChunk.chunk_index.asc(), DocumentChunk.id.asc())
                .offset(offset)
                .limit(PREVIEW_PAGE_SIZE)
            )
        ).all()
        if not rows:
            # COUNT 与分页之间文档被重建/删除：按已读内容如实标记不完整。
            truncated = truncated or offset < total_chunks
            break

        for original_text, chunk_text, meta_data in rows:
            if returned >= PREVIEW_MAX_CHUNKS or used_chars >= PREVIEW_MAX_CHARS:
                truncated = True
                break
            text = _preview_chunk_text(original_text, chunk_text, meta_data)
            is_original = _is_original(original_text, meta_data)
            if is_original:
                original_count += 1
            remaining = PREVIEW_MAX_CHARS - used_chars
            if len(text) > remaining:
                # 单块就超出预算：截到预算上限并把「不完整」如实透出。
                text = text[:remaining]
                truncated = True
            if text:
                parts.append(text)
                used_chars += len(text)
            returned += 1

        if truncated:
            break
        offset += PREVIEW_PAGE_SIZE

    # 非快照隔离或重建与 COUNT 交错时，返回的块数可能与 COUNT 不一致
    # （多读/少读都说明这份预览不是全量），一律显式标记截断。
    if returned != total_chunks:
        truncated = True

    content = "\n".join(part for part in parts if part)
    return DocumentPreviewText(
        content=content,
        text_provenance=(
            TEXT_PROVENANCE_NONE if not content else _provenance_label(returned, original_count)
        ),
        original_chunk_count=original_count,
        legacy_chunk_count=returned - original_count,
        total_chunks=total_chunks,
        returned_chunks=returned,
        truncated=truncated,
    )


async def _load_from_es(document_id: str) -> DocumentPreviewText:
    """DB 无分块行时的回退：只取显式字段，命中总数超出本页即 truncated。"""
    from app.core.elasticsearch import ElasticsearchTools

    res = await ElasticsearchTools.search_documents(
        {
            "query": {"term": {"document_id": document_id}},
            "size": ES_PREVIEW_SIZE,
            "_source": [
                "chunk_text",
                "content",
                "original_text",
                "text_provenance",
                "chunk_index",
                "metadata",
            ],
        }
    )
    hits = res.get("hits", {}).get("hits", []) or []
    total_chunks = _es_total(res, fallback=len(hits))

    ordered = sorted(hits, key=_es_chunk_index)
    parts: list[str] = []
    used_chars = 0
    returned = 0
    original_count = 0
    cut_by_budget = False
    for hit in ordered:
        source = hit.get("_source") or {}
        if returned >= PREVIEW_MAX_CHUNKS or used_chars >= PREVIEW_MAX_CHARS:
            cut_by_budget = True
            break
        original = original_text_of(source)
        is_original = original is not None
        text = (
            original if is_original else (source.get("chunk_text") or source.get("content") or "")
        )
        if is_original:
            original_count += 1
        remaining = PREVIEW_MAX_CHARS - used_chars
        if len(text) > remaining:
            # 单块超出剩余预算：截断必须显式（即便它恰好是最后一个 hit）。
            text = text[:remaining]
            cut_by_budget = True
        if text:
            parts.append(text)
            used_chars += len(text)
        returned += 1

    truncated = total_chunks > len(hits) or returned < len(ordered) or cut_by_budget
    content = "\n".join(part for part in parts if part)
    return DocumentPreviewText(
        content=content,
        text_provenance=(
            TEXT_PROVENANCE_NONE if not content else _provenance_label(returned, original_count)
        ),
        original_chunk_count=original_count,
        legacy_chunk_count=returned - original_count,
        total_chunks=total_chunks,
        returned_chunks=returned,
        truncated=truncated,
    )


def _preview_chunk_text(
    original_text: str | None, chunk_text: str | None, meta_data: object | None = None
) -> str:
    if _is_original(original_text, meta_data):
        return original_text or ""
    return chunk_text or ""


def _is_original(original_text: str | None, meta_data: object | None = None) -> bool:
    if not isinstance(original_text, str) or not original_text.strip():
        return False
    # 与 retrieval 的 original_text_of 同口径：正文与写入方标记必须同时成立。
    if not isinstance(meta_data, dict):
        return False
    return provenance_of(meta_data) == PARSER_ORIGINAL


def _provenance_label(returned: int, original_count: int) -> str:
    if returned == 0:
        return TEXT_PROVENANCE_NONE
    if original_count == returned:
        return PARSER_ORIGINAL
    if original_count == 0:
        return LEGACY_UNVERIFIED
    return TEXT_PROVENANCE_MIXED


def _es_total(res: dict, *, fallback: int) -> int:
    total = (res.get("hits") or {}).get("total")
    if isinstance(total, dict):
        value = total.get("value")
        if isinstance(value, int):
            return value
    if isinstance(total, int):  # ES < 7 形态
        return total
    return fallback


def _es_chunk_index(hit: dict) -> int:
    source = hit.get("_source") or {}
    top_level = source.get("chunk_index")
    if isinstance(top_level, int):
        return top_level
    metadata = source.get("metadata") or {}
    value = metadata.get("chunk_index")
    return value if isinstance(value, int) else 0


__all__ = [
    "DocumentPreviewText",
    "PARSER_ORIGINAL",
    "LEGACY_UNVERIFIED",
    "TEXT_PROVENANCE_NONE",
    "TEXT_PROVENANCE_MIXED",
    "load_document_preview_text",
]
