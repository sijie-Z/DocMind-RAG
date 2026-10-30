"""文档核验身份 / readiness / evidence（Batch 1）真实 SQLite 单测。"""

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.exceptions import ConflictError, ValidationError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.review import ReviewDocumentMeta
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services import review_document_service as svc

ORG = 1
DOC = "doc-review-1"


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_doc(factory, doc_id=DOC, *, status=DocumentStatus.INDEXED):
    async with factory() as db:
        db.add(
            Document(
                id=doc_id,
                filename="contract.pdf",
                file_path=f"1/{doc_id}.pdf",
                file_size=100,
                file_type=DocumentType.PDF,
                status=status,
                organization_id=ORG,
                uploaded_by=1,
            )
        )
        await db.commit()


def _chunk(doc_id, idx, *, original, chunk_text=None, page=1, meta_data=None):
    if meta_data is None:
        meta_data = {"text_provenance": "parser_original" if original else "legacy_unverified"}
    return DocumentChunk(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        chunk_index=idx,
        chunk_text=chunk_text or original or "",
        original_text=original,
        chunk_length=len(chunk_text or original or ""),
        page_number=page,
        meta_data=meta_data,
    )


async def _seed_chunks(factory, rows, doc_id=DOC):
    async with factory() as db:
        db.add_all([_chunk(doc_id, i, **row) for i, row in enumerate(rows)])
        await db.commit()


async def _doc(factory, doc_id=DOC):
    async with factory() as db:
        return await db.get(Document, doc_id)


async def _register_meta(factory, monkeypatch, **overrides):
    monkeypatch.setattr(svc, "_sha256_storage_object", lambda _path: _async_hash("a" * 64))
    payload = {
        "doc_role": "contract",
        "family_key": "supplier-x",
        "version_label": "v1",
        "version_seq": 1,
    }
    payload.update(overrides)
    async with factory() as db:
        doc = await db.get(Document, DOC)
        return await svc.upsert_review_meta(db, doc, svc.ReviewMetaInput(**payload), user_id=7)


async def _async_hash(value: str) -> str:
    return value


@pytest.mark.asyncio
async def test_contract_meta_requires_version_and_hashes_storage(factory, monkeypatch):
    await _seed_doc(factory)
    with pytest.raises(ValidationError):
        await _register_meta(factory, monkeypatch, family_key=None)
    with pytest.raises(ValidationError):
        await _register_meta(factory, monkeypatch, version_seq=None)

    meta = await _register_meta(factory, monkeypatch)
    assert meta.doc_role == "contract"
    assert meta.family_key == "supplier-x"
    assert meta.version_seq == 1
    assert meta.content_sha256 == "a" * 64
    assert meta.registered_by == 7


@pytest.mark.asyncio
async def test_duplicate_identity_is_rejected(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_doc(factory, doc_id="doc-review-2")
    await _register_meta(factory, monkeypatch)

    monkeypatch.setattr(svc, "_sha256_storage_object", lambda _p: _async_hash("b" * 64))
    async with factory() as db:
        other = await db.get(Document, "doc-review-2")
        with pytest.raises(ConflictError):
            await svc.upsert_review_meta(
                db,
                other,
                svc.ReviewMetaInput(
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="v1",
                    version_seq=1,
                ),
                user_id=7,
            )


@pytest.mark.asyncio
async def test_readiness_reviewable_with_full_original_and_version(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [
            {"original": "第一条 付款期限为30天。", "page": 1},
            {"original": "第二条 验收期限为7天。", "page": 2},
        ],
    )
    await _register_meta(factory, monkeypatch)

    async with factory() as db:
        readiness = await svc.compute_readiness(db, await db.get(Document, DOC))

    assert readiness.status == "reviewable"
    assert readiness.blocking_reasons == []
    assert readiness.total_chunks == 2
    assert readiness.original_text_chunks == 2
    assert readiness.page_number_chunks == 2
    assert readiness.clause_candidate_chunks == 2
    assert readiness.meta["version_label"] == "v1"


@pytest.mark.asyncio
async def test_readiness_blocks_legacy_and_missing_meta(factory):
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [
            {"original": "第一条 原文", "page": 1},
            {"original": None, "chunk_text": "【文档背景：摘要】旧文", "page": 1},
        ],
    )

    async with factory() as db:
        readiness = await svc.compute_readiness(db, await db.get(Document, DOC))

    assert readiness.status == "blocked"
    assert "legacy_unverified" in readiness.blocking_reasons
    assert "missing_version_meta" in readiness.blocking_reasons
    assert readiness.legacy_chunk_count == 1


@pytest.mark.asyncio
async def test_policy_without_version_can_have_multiple_documents(factory, monkeypatch):
    """制度/模板允许不带版本；两份未版本化制度不得被唯一性误伤。"""
    await _seed_doc(factory)
    await _seed_doc(factory, doc_id="doc-review-policy-2")
    monkeypatch.setattr(svc, "_sha256_storage_object", lambda _p: _async_hash("e" * 64))

    async with factory() as db:
        await svc.upsert_review_meta(
            db, await db.get(Document, DOC), svc.ReviewMetaInput(doc_role="policy"), user_id=7
        )
        await svc.upsert_review_meta(
            db,
            await db.get(Document, "doc-review-policy-2"),
            svc.ReviewMetaInput(doc_role="policy"),
            user_id=7,
        )
    async with factory() as db:
        count = (
            await db.execute(
                select(func.count(ReviewDocumentMeta.document_id)).where(
                    ReviewDocumentMeta.doc_role == "policy"
                )
            )
        ).scalar()
    assert count == 2


@pytest.mark.asyncio
async def test_same_family_version_seq_conflict(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_doc(factory, doc_id="doc-review-seq")
    await _register_meta(factory, monkeypatch)
    monkeypatch.setattr(svc, "_sha256_storage_object", lambda _p: _async_hash("f" * 64))

    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await svc.upsert_review_meta(
                db,
                await db.get(Document, "doc-review-seq"),
                svc.ReviewMetaInput(
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="v2",
                    version_seq=1,
                ),
                user_id=7,
            )
    assert excinfo.value.error_code == "REVIEW_META_VERSION_SEQ_CONFLICT"


@pytest.mark.asyncio
async def test_original_without_marker_is_legacy(factory, monkeypatch):
    """有 original_text 但缺写入方标记：readiness 不得 reviewable，evidence 不得返回。"""
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [{"original": "疑似回填文本", "page": 1, "meta_data": {}}],
    )
    await _register_meta(factory, monkeypatch)

    async with factory() as db:
        readiness = await svc.compute_readiness(db, await db.get(Document, DOC))
        with pytest.raises(SourceProvenanceUnavailableError):
            await svc.query_evidence(db, await db.get(Document, DOC))

    assert readiness.status == "blocked"
    assert "no_original_text" in readiness.blocking_reasons


@pytest.mark.asyncio
async def test_mixed_naive_aware_effective_dates_do_not_500(factory, monkeypatch):
    await _seed_doc(factory)
    meta = await _register_meta(
        factory,
        monkeypatch,
        effective_from=datetime(2026, 1, 1),
        effective_to=datetime(2026, 6, 1, tzinfo=UTC),
    )
    # SQLite 会丢弃 tzinfo；关键是不因 naive/aware 混用抛 500，且值已落库。
    assert meta.effective_from is not None
    assert meta.effective_to is not None


@pytest.mark.asyncio
async def test_readiness_warns_on_missing_page_and_clauses(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_chunks(factory, [{"original": "没有条号的正文", "page": None}])
    await _register_meta(factory, monkeypatch)

    async with factory() as db:
        readiness = await svc.compute_readiness(db, await db.get(Document, DOC))

    assert readiness.status == "reviewable"
    assert "no_page_info" in readiness.warnings
    assert "no_clause_candidates" in readiness.warnings


@pytest.mark.asyncio
async def test_evidence_returns_original_only(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [
            {
                "original": "第一条 付款期限为30天。",
                "chunk_text": "【文档背景：摘要】第一条 付款期限为30天。",
                "page": 1,
            },
            {
                "original": "第二条 验收期限为7天。",
                "chunk_text": "【文档背景：摘要】第二条 验收期限为7天。",
                "page": 2,
            },
            {"original": None, "chunk_text": "【文档背景】第三条 旧版", "page": 3},
        ],
    )
    await _register_meta(factory, monkeypatch)

    async with factory() as db:
        result = await svc.query_evidence(db, await db.get(Document, DOC), clause_no=1, limit=5)

    assert result.returned == 1
    item = result.items[0]
    assert item["quote"] == "第一条 付款期限为30天。"
    assert "【文档背景" not in item["quote"]
    assert item["text_provenance"] == "parser_original"
    assert item["clause_no"] == [1]
    assert item["document_sha256"] == "a" * 64
    assert result.legacy_chunk_count == 1

    async with factory() as db:
        unfiltered = await svc.query_evidence(db, await db.get(Document, DOC), limit=10)
    assert unfiltered.returned == 2
    assert all("【文档背景" not in i["quote"] for i in unfiltered.items)
    assert all(i["text_provenance"] == "parser_original" for i in unfiltered.items)


@pytest.mark.asyncio
async def test_evidence_legacy_only_is_structured_refusal(factory):
    await _seed_doc(factory)
    await _seed_chunks(factory, [{"original": None, "chunk_text": "旧文", "page": 1}])

    async with factory() as db:
        with pytest.raises(SourceProvenanceUnavailableError) as excinfo:
            await svc.query_evidence(db, await db.get(Document, DOC))

    assert excinfo.value.as_dict()["status"] == "needs_rebuild"
    assert excinfo.value.as_dict()["document_ids"] == [DOC]


@pytest.mark.asyncio
async def test_evidence_query_filter_and_truncation(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [
            {"original": "第一条 付款期限为30天。", "page": 1},
            {"original": "第二条 验收期限为7天。", "page": 2},
        ],
    )
    await _register_meta(factory, monkeypatch)

    async with factory() as db:
        hit = await svc.query_evidence(db, await db.get(Document, DOC), query="验收")
        first = await svc.query_evidence(db, await db.get(Document, DOC), limit=1)

    assert [i["chunk_index"] for i in hit.items] == [1]
    assert first.returned == 1
    assert first.truncated is True


# ── 迁移 020 round-trip ─────────────────────────────────────────────────────

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "019_deleted_chat_sessions"
NEW_REV = "020_review_document_meta"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def test_020_migration_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "review-meta-020.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    script = ScriptDirectory.from_config(cfg)

    assert len(NEW_REV) <= 32
    heads = set(script.get_heads())
    assert len(heads) == 1, f"迁移必须单头，实际 {heads}"
    chain = [rev.revision for rev in script.iterate_revisions(next(iter(heads)), "base")]
    assert NEW_REV in chain
    assert chain.index(PREV_REV) == chain.index(NEW_REV) + 1

    command.upgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "review_document_meta" not in tables

    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info('review_document_meta')")}
        indexes = {row[1]: row for row in conn.execute("PRAGMA index_list('review_document_meta')")}
        fks = {
            (row[2], row[3])
            for row in conn.execute("PRAGMA foreign_key_list('review_document_meta')")
        }
    assert set(columns) == {
        "document_id",
        "organization_id",
        "doc_role",
        "family_key",
        "version_label",
        "version_seq",
        "supersedes_document_id",
        "effective_from",
        "effective_to",
        "content_sha256",
        "registered_by",
        "created_at",
        "updated_at",
    }
    assert columns["document_id"][5] == 1
    assert columns["content_sha256"][3] == 1
    assert "ix_review_document_meta_organization_id" in indexes
    assert "ix_review_document_meta_family" in indexes
    unique_indexes = [name for name, row in indexes.items() if row[2] == 1]
    assert unique_indexes, "唯一约束/唯一索引必须存在"
    unique_columns = set()
    with sqlite3.connect(str(db_path)) as conn:
        for name in unique_indexes:
            unique_columns.add(
                tuple(row[2] for row in conn.execute(f"PRAGMA index_info('{name}')"))
            )
    assert (
        "organization_id",
        "doc_role",
        "family_key",
        "version_label",
    ) in unique_columns
    assert (
        "organization_id",
        "doc_role",
        "family_key",
        "version_seq",
    ) in unique_columns
    assert ("documents", "document_id") in fks

    command.downgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "review_document_meta" not in tables


@pytest.mark.asyncio
async def test_evidence_scan_cap_is_explicit(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [
            {"original": "第一条 甲", "page": 1},
            {"original": "第二条 乙", "page": 2},
            {"original": "第三条 丙", "page": 3},
        ],
    )
    await _register_meta(factory, monkeypatch)
    monkeypatch.setattr(svc, "MAX_EVIDENCE_SCAN_CHUNKS", 2)

    async with factory() as db:
        result = await svc.query_evidence(db, await db.get(Document, DOC), limit=10)

    assert result.returned == 2
    assert result.scan_truncated is True


@pytest.mark.asyncio
async def test_evidence_scan_cap_with_no_original_does_not_fake_409(factory, monkeypatch):
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [
            {"original": None, "chunk_text": "旧一", "page": 1},
            {"original": None, "chunk_text": "旧二", "page": 2},
            {"original": "第三条 原文", "page": 3},
        ],
    )
    monkeypatch.setattr(svc, "MAX_EVIDENCE_SCAN_CHUNKS", 2)

    async with factory() as db:
        result = await svc.query_evidence(db, await db.get(Document, DOC), limit=10)

    assert result.returned == 0
    assert result.scan_truncated is True


@pytest.mark.asyncio
async def test_readiness_warns_when_evidence_scan_is_limited(factory, monkeypatch):
    """文档超过 evidence 硬扫描上限时，readiness 必须提示证据不可全量覆盖。"""
    await _seed_doc(factory)
    await _seed_chunks(
        factory,
        [{"original": "第一条 甲", "page": 1}, {"original": "第二条 乙", "page": 2}],
    )
    await _register_meta(factory, monkeypatch)
    monkeypatch.setattr(svc, "MAX_EVIDENCE_SCAN_CHUNKS", 1)

    async with factory() as db:
        readiness = await svc.compute_readiness(db, await db.get(Document, DOC))

    assert "evidence_scan_limited" in readiness.warnings


@pytest.mark.asyncio
async def test_upsert_self_heals_mismatched_meta_organization(factory, monkeypatch):
    """组织错配的 meta 行（仅手工改库可达）在重新登记时归位到文档组织。"""
    await _seed_doc(factory)
    async with factory() as db:
        db.add(
            ReviewDocumentMeta(
                document_id=DOC,
                organization_id=999,
                doc_role="contract",
                family_key="supplier-x",
                version_label="v1",
                version_seq=1,
                content_sha256="z" * 64,
                registered_by=1,
            )
        )
        await db.commit()

    meta = await _register_meta(factory, monkeypatch)

    assert meta.organization_id == ORG


@pytest.mark.asyncio
async def test_upsert_self_heal_conflicts_when_target_slot_taken(factory, monkeypatch):
    """自愈把行搬回目标组织时，目标槽位已被占用必须 409 且不半写。"""
    await _seed_doc(factory)
    async with factory() as db:
        db.add(
            Document(
                id="doc-slot-occupied",
                filename="other.pdf",
                file_path="1/doc-slot-occupied.pdf",
                file_size=100,
                file_type=DocumentType.PDF,
                status=DocumentStatus.INDEXED,
                organization_id=ORG,
                uploaded_by=1,
            )
        )
        db.add(
            ReviewDocumentMeta(
                document_id="doc-slot-occupied",
                organization_id=ORG,
                doc_role="contract",
                family_key="supplier-x",
                version_label="v1",
                version_seq=1,
                content_sha256="b" * 64,
                registered_by=1,
            )
        )
        db.add(
            ReviewDocumentMeta(
                document_id=DOC,
                organization_id=999,
                doc_role="contract",
                family_key="supplier-x",
                version_label="v1",
                version_seq=1,
                content_sha256="z" * 64,
                registered_by=1,
            )
        )
        await db.commit()

    with pytest.raises(ConflictError) as excinfo:
        await _register_meta(factory, monkeypatch)

    assert excinfo.value.error_code == "REVIEW_META_DUPLICATE"
    async with factory() as db:
        meta = await db.get(ReviewDocumentMeta, DOC)
    assert meta.organization_id == 999, "冲突时不得半写自愈"
