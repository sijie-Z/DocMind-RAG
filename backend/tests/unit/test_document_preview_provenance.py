"""文档预览原文口径（PR4 第一片）：原文优先、legacy 显式、截断显式。

真实 SQLite 分块行 + fake ES；不连接真实 DB/ES/模型。

关键变异点：
- 预览重新读取 ``chunk_text``（增强文本）→ 原文用例红；
- 缺 ``original_text`` 不降级标记 → legacy 用例红；
- 截断不报告/顺序错乱 → 截断与分页用例红；
- DB 有行仍回退 ES → DB 权威用例红。
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.document_preview as preview_mod
from app.core.database import Base
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.services.document_preview import load_document_preview_text

BACKGROUND = "【文档背景：模型生成的摘要】\n\n"


@pytest.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [Document.__table__, DocumentChunk.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_document(factory, document_id: str) -> None:
    async with factory() as session:
        session.add(
            Document(
                id=document_id,
                filename="contract.txt",
                file_path=f"1/{document_id}.txt",
                file_size=1,
                file_type=DocumentType.TXT,
                status=DocumentStatus.INDEXED,
                organization_id=1,
                uploaded_by=1,
            )
        )
        await session.commit()


def _chunk(
    document_id: str,
    index: int,
    chunk_text: str,
    original_text: str | None,
    meta_data: dict | None = None,
):
    if meta_data is None:
        # 与 doc_processor 真实写入一致：原文行带 parser_original 标记，
        # 旧行带 legacy_unverified（或历史行为下的缺失标记）。
        meta_data = {
            "text_provenance": (
                "parser_original" if (original_text or "").strip() else "legacy_unverified"
            )
        }
    return DocumentChunk(
        id=str(uuid.uuid4()),
        document_id=document_id,
        chunk_index=index,
        chunk_text=chunk_text,
        original_text=original_text,
        chunk_length=len(chunk_text),
        meta_data=meta_data,
    )


async def _seed_chunks(factory, document_id: str, rows) -> None:
    await _seed_document(factory, document_id)
    async with factory() as session:
        session.add_all([_chunk(document_id, i, chunk, original) for i, chunk, original in rows])
        await session.commit()


async def _load(factory, document_id: str):
    async with factory() as session:
        return await load_document_preview_text(session, document_id)


@pytest.mark.asyncio
async def test_original_text_wins_over_enhanced_chunk_text(session_factory):
    """原文存在的分块必须返回 O，生成的背景摘要不得混进这些分块。"""
    doc_id = "doc-original"
    await _seed_chunks(
        session_factory,
        doc_id,
        [
            (0, BACKGROUND + "第一条 付款期限为 30 天。", "第一条 付款期限为 30 天。"),
            (1, BACKGROUND + "第二条 验收期限为 7 天。", "第二条 验收期限为 7 天。"),
            # 旧分块：没有原文，只能回退增强文本，并如实标记 legacy。
            (2, BACKGROUND + "第三条 仅旧索引文本。", None),
        ],
    )

    preview = await _load(session_factory, doc_id)

    assert preview.content.count(BACKGROUND) == 1
    assert "第一条 付款期限为 30 天。" in preview.content
    assert "第二条 验收期限为 7 天。" in preview.content
    assert preview.text_provenance == "mixed"
    assert (preview.original_chunk_count, preview.legacy_chunk_count) == (2, 1)
    assert (preview.total_chunks, preview.returned_chunks) == (3, 3)
    assert preview.truncated is False


@pytest.mark.asyncio
async def test_all_original_is_parser_original(session_factory):
    doc_id = "doc-all-original"
    await _seed_chunks(
        session_factory,
        doc_id,
        [
            (0, BACKGROUND + "甲原文", "甲原文"),
            (1, BACKGROUND + "乙原文", "乙原文"),
        ],
    )

    preview = await _load(session_factory, doc_id)

    assert preview.content == "甲原文\n乙原文"
    assert preview.text_provenance == "parser_original"
    assert preview.original_chunk_count == 2
    assert preview.legacy_chunk_count == 0


@pytest.mark.asyncio
async def test_legacy_rows_are_unverified_even_when_text_nonempty(session_factory):
    doc_id = "doc-legacy"
    await _seed_chunks(
        session_factory,
        doc_id,
        [(0, "旧版块一", None), (1, "旧版块二", "   ")],
    )

    preview = await _load(session_factory, doc_id)

    assert preview.content == "旧版块一\n旧版块二"
    assert preview.text_provenance == "legacy_unverified"
    assert preview.legacy_chunk_count == 2
    assert preview.original_chunk_count == 0


@pytest.mark.asyncio
async def test_pagination_keeps_chunk_order(session_factory, monkeypatch):
    """跨页读取仍按 chunk_index 顺序拼接（分页不能乱序/漏块）。"""
    monkeypatch.setattr(preview_mod, "PREVIEW_PAGE_SIZE", 2)
    doc_id = "doc-paged"
    rows = [(i, f"块{i}", f"块{i}") for i in range(5)]
    await _seed_chunks(session_factory, doc_id, rows)

    preview = await _load(session_factory, doc_id)

    assert preview.content == "\n".join(f"块{i}" for i in range(5))
    assert (preview.returned_chunks, preview.total_chunks) == (5, 5)
    assert preview.truncated is False


@pytest.mark.asyncio
async def test_char_budget_truncation_is_explicit(session_factory, monkeypatch):
    """字符预算截断必须 truncated=True，且计数如实反映实际返回的块。"""
    monkeypatch.setattr(preview_mod, "PREVIEW_MAX_CHARS", 10)
    doc_id = "doc-budget"
    await _seed_chunks(
        session_factory,
        doc_id,
        [(i, f"aaaa{i}", f"aaaa{i}") for i in range(5)],
    )

    preview = await _load(session_factory, doc_id)

    assert preview.content == "aaaa0\naaaa1"
    assert preview.truncated is True
    assert preview.returned_chunks == 2
    assert preview.total_chunks == 5
    assert preview.text_provenance == "parser_original"


@pytest.mark.asyncio
async def test_db_last_chunk_budget_cut_is_still_truncated(session_factory, monkeypatch):
    """DB 路径最后一个块被预算截断时也必须报告 truncated。"""
    monkeypatch.setattr(preview_mod, "PREVIEW_MAX_CHARS", 4)
    doc_id = "doc-db-cut"
    await _seed_chunks(session_factory, doc_id, [(0, "0123456789", "0123456789")])

    preview = await _load(session_factory, doc_id)

    assert preview.content == "0123"
    assert preview.truncated is True
    assert (preview.total_chunks, preview.returned_chunks) == (1, 1)


@pytest.mark.asyncio
async def test_chunk_budget_truncation_is_explicit(session_factory, monkeypatch):
    monkeypatch.setattr(preview_mod, "PREVIEW_MAX_CHUNKS", 2)
    doc_id = "doc-chunks"
    await _seed_chunks(
        session_factory,
        doc_id,
        [(i, f"块{i}", f"块{i}") for i in range(4)],
    )

    preview = await _load(session_factory, doc_id)

    assert preview.content == "块0\n块1"
    assert preview.truncated is True
    assert preview.returned_chunks == 2
    assert preview.total_chunks == 4


@pytest.mark.asyncio
async def test_db_rows_never_fall_back_to_es(session_factory, monkeypatch):
    """DB 已有分块行时，即使内容为空也不得被 ES 旧数据顶替。"""
    doc_id = "doc-db-authority"
    await _seed_chunks(session_factory, doc_id, [(0, "", None)])

    es_search = AsyncMock(return_value={"hits": {"total": {"value": 1}, "hits": []}})
    monkeypatch.setattr("app.core.elasticsearch.ElasticsearchTools.search_documents", es_search)

    preview = await _load(session_factory, doc_id)

    es_search.assert_not_called()
    assert preview.content == ""
    assert preview.text_provenance == "none"
    assert preview.total_chunks == 1


@pytest.mark.asyncio
async def test_original_text_without_marker_is_legacy(session_factory):
    """有 original_text 但缺写入方标记（如未来回填）不得被认作原文。"""
    doc_id = "doc-no-marker"
    await _seed_document(session_factory, doc_id)
    async with session_factory() as session:
        session.add(
            _chunk(
                doc_id,
                0,
                BACKGROUND + "甲增强文本",
                "甲原文",
                meta_data={},
            )
        )
        await session.commit()

    preview = await _load(session_factory, doc_id)

    assert preview.content == BACKGROUND + "甲增强文本"
    assert preview.text_provenance == "legacy_unverified"
    assert preview.original_chunk_count == 0


@pytest.mark.asyncio
async def test_es_fallback_uses_original_and_reports_ordering(session_factory, monkeypatch):
    doc_id = "doc-es-only"
    hits = [
        {
            "_source": {
                "chunk_index": 1,
                "chunk_text": BACKGROUND + "乙旧文",
                "original_text": "乙原文",
                "text_provenance": "parser_original",
            }
        },
        {
            "_source": {
                "chunk_index": 0,
                "chunk_text": "甲旧文",
                # 有 original_text 但无 marker：按不可信处理，回退检索文本。
                "original_text": "甲原文",
            }
        },
    ]
    es_search = AsyncMock(return_value={"hits": {"total": {"value": 2}, "hits": hits}})
    monkeypatch.setattr("app.core.elasticsearch.ElasticsearchTools.search_documents", es_search)

    preview = await _load(session_factory, doc_id)

    es_search.assert_awaited_once()
    query = es_search.await_args.args[0]
    # 回退查询必须显式取原文/标记字段并使用有界 size（删字段或丢 size 会静默退化）。
    assert query["size"] == preview_mod.ES_PREVIEW_SIZE
    assert {
        "chunk_text",
        "content",
        "original_text",
        "text_provenance",
        "chunk_index",
        "metadata",
    } <= set(query["_source"])
    assert preview.content == "甲旧文\n乙原文"
    assert preview.text_provenance == "mixed"
    assert (preview.original_chunk_count, preview.legacy_chunk_count) == (1, 1)
    assert (preview.total_chunks, preview.returned_chunks) == (2, 2)
    assert preview.truncated is False


@pytest.mark.asyncio
async def test_es_fallback_reports_page_truncation(session_factory, monkeypatch):
    doc_id = "doc-es-truncated"
    hits = [
        {
            "_source": {
                "chunk_index": 0,
                "chunk_text": "甲旧文",
            }
        }
    ]
    es_search = AsyncMock(return_value={"hits": {"total": {"value": 5}, "hits": hits}})
    monkeypatch.setattr("app.core.elasticsearch.ElasticsearchTools.search_documents", es_search)

    preview = await _load(session_factory, doc_id)

    assert preview.truncated is True
    assert (preview.total_chunks, preview.returned_chunks) == (5, 1)
    assert preview.text_provenance == "legacy_unverified"


@pytest.mark.asyncio
async def test_es_last_hit_budget_cut_is_still_truncated(session_factory, monkeypatch):
    """ES 回退里最后一个 hit 被预算截断时也必须报告 truncated。"""
    monkeypatch.setattr(preview_mod, "PREVIEW_MAX_CHARS", 4)
    hits = [{"_source": {"chunk_index": 0, "chunk_text": "0123456789"}}]
    es_search = AsyncMock(return_value={"hits": {"total": {"value": 1}, "hits": hits}})
    monkeypatch.setattr("app.core.elasticsearch.ElasticsearchTools.search_documents", es_search)

    preview = await _load(session_factory, "doc-es-cut")

    assert preview.content == "0123"
    assert preview.truncated is True
    assert (preview.total_chunks, preview.returned_chunks) == (1, 1)


@pytest.mark.asyncio
async def test_empty_document_reports_none(session_factory, monkeypatch):
    es_search = AsyncMock(return_value={"hits": {"total": {"value": 0}, "hits": []}})
    monkeypatch.setattr("app.core.elasticsearch.ElasticsearchTools.search_documents", es_search)

    preview = await _load(session_factory, "doc-missing")

    assert preview.content == ""
    assert preview.text_provenance == "none"
    assert preview.truncated is False
