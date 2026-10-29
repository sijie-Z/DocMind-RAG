"""核验决策与核验单导出（Batch 4）真实 SQLite 单测。"""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.review import ReviewDocumentMeta, ReviewExport, ReviewItem
from app.models.user_audit import UserActivityLog
from app.services import review_case_service as case_service
from app.services import review_export_service as svc

ORG = 1
USER_ID = 7
POLICY = "doc-exp-policy"
BASE = "doc-exp-base"
REVISED = "doc-exp-revised"


def _chunk(doc_id, idx, text):
    return DocumentChunk(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        chunk_index=idx,
        chunk_text=text,
        original_text=text,
        chunk_length=len(text),
        start_pos=0,
        end_pos=len(text),
        page_number=1,
        meta_data={"text_provenance": "parser_original"},
    )


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-export.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_case(factory):
    async with factory() as db:
        for doc_id, role, family, label, seq in (
            (POLICY, "policy", None, None, None),
            (BASE, "contract", "supplier-x", "V1", 1),
            (REVISED, "contract", "supplier-x", "V2", 2),
        ):
            db.add(
                Document(
                    id=doc_id,
                    filename=f"{doc_id}.pdf",
                    file_path=f"1/{doc_id}.pdf",
                    file_size=100,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=1,
                )
            )
            db.add(
                ReviewDocumentMeta(
                    document_id=doc_id,
                    organization_id=ORG,
                    doc_role=role,
                    family_key=family,
                    version_label=label,
                    version_seq=seq,
                    content_sha256=doc_id[0] * 64,
                    registered_by=1,
                )
            )
        db.add_all(
            [
                _chunk(POLICY, 0, "第3条 采购付款应遵循本制度，另有约定的从其约定。"),
                _chunk(BASE, 0, "第八条 付款期限为30天，自收到发票之日起算。"),
                _chunk(BASE, 1, "第九条 验收合格后支付价款。"),
                _chunk(REVISED, 0, "第九条 付款期限为60天，自收到发票之日起算。"),
                _chunk(REVISED, 1, "第十条 验收合格后支付价款。"),
            ]
        )
        await db.commit()
        docs = {
            "policy": await db.get(Document, POLICY),
            "base": await db.get(Document, BASE),
            "revised": await db.get(Document, REVISED),
        }
        return await case_service.create_case(
            db,
            docs=docs,
            title="export case",
            checklist_version="v1",
            created_by=USER_ID,
        )


async def _items(factory, case_id):
    async with factory() as db:
        return (
            (
                await db.execute(
                    select(ReviewItem)
                    .where(ReviewItem.case_id == case_id)
                    .order_by(ReviewItem.ordinal)
                )
            )
            .scalars()
            .all()
        )


async def _decide_all(factory, case, *, status="verified"):
    items = await _items(factory, case.id)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        for item in items:
            db_item = await db.get(ReviewItem, item.id)
            await svc.decide_item(db, fresh, db_item, status=status, note="ok", user_id=USER_ID)
    return items


@pytest.mark.asyncio
async def test_decision_moves_case_to_in_review(factory):
    case = await _seed_case(factory)
    items = await _items(factory, case.id)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        item = await db.get(ReviewItem, items[0].id)
        with pytest.raises(ValidationError):
            await svc.decide_item(db, fresh, item, status="approved", note=None, user_id=USER_ID)
        row = await svc.decide_item(
            db, fresh, item, status="verified", note="looks right", user_id=USER_ID
        )
        assert row.status == "verified"
        assert row.decided_by == USER_ID
        assert row.decided_at is not None
        await db.refresh(fresh)
        assert fresh.status == "in_review"


@pytest.mark.asyncio
async def test_export_blocked_until_all_items_decided(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        with pytest.raises(ConflictError) as excinfo:
            await svc.export_case(db, fresh, user_id=USER_ID)
    err = excinfo.value
    assert err.error_code == "REVIEW_EXPORT_BLOCKED"
    assert set(err.detail["items"]) == {
        "payment_term",
        "start_condition",
        "acceptance_invoice",
        "exception",
    }

    await _decide_all(factory, case)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        export = await svc.export_case(db, fresh, user_id=USER_ID)
    assert export.item_count == 4
    assert export.schema_version == 1
    assert export.payload_sha256 == svc.payload_sha256(export.payload)


@pytest.mark.asyncio
async def test_export_payload_contract(factory):
    case = await _seed_case(factory)
    await _decide_all(factory, case, status="needs_human")
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        # 选中一条原文证据，导出只能包含已选快照
        detail = await case_service.case_detail(db, fresh)
        payment = next(i for i in detail["items"] if i["item_key"] == "payment_term")
        base_chunk = payment["evidence"]["base"][0]["chunk_id_at_capture"]
        item_row = await db.get(ReviewItem, payment["id"])
        await case_service.select_evidence(
            db,
            fresh,
            item_row,
            side="base",
            chunk_id=base_chunk,
            role="main",
            selected=True,
        )
        export = await svc.export_case(db, fresh, user_id=USER_ID)

    payload = export.payload
    assert payload["schema_version"] == 1
    assert payload["documents"]["base"]["version_label"] == "V1"
    assert payload["documents"]["revised"]["content_sha256"] == "d" * 64
    payment_payload = next(i for i in payload["items"] if i["item_key"] == "payment_term")
    assert payment_payload["status"] == "needs_human"
    assert payment_payload["evidence"]["base"], "导出必须包含已选原文证据"
    row = payment_payload["evidence"]["base"][0]
    assert row["text_provenance"] == "parser_original"
    assert "【文档背景" not in row["quote"]
    assert row["quote_sha256"]
    assert export.needs_human_count == 4
    assert payload["coverage"]["needs_human_items"]

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "chunk_text" not in serialized
    assert "X-Amz" not in serialized
    # 只扫描生成文案/字段名，不扫描原文引用（原文可能含任何词）。
    generated = json.dumps(
        {
            "statuses": [i["status"] for i in payload["items"]],
            "changes": [i["change_type"] for i in payload["items"]],
            "keys": list(payload.keys()),
            "disclaimer": payload["disclaimer"],
            "coverage_keys": list(payload["coverage"].keys()),
        },
        ensure_ascii=False,
    )
    for banned in ("合法", "违法", "合规", "不合规", "无风险", "可签", "风险分", "合规率"):
        assert banned not in generated


@pytest.mark.asyncio
async def test_export_is_deterministic_and_rows_immutable(factory):
    case = await _seed_case(factory)
    await _decide_all(factory, case)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        first = await svc.export_case(db, fresh, user_id=USER_ID)
        second = await svc.export_case(db, fresh, user_id=USER_ID)
    assert first.payload_sha256 == second.payload_sha256, "无变更重导出必须同哈希"
    assert first.id != second.id

    # 决策变化 → 案件回 in_review，旧导出行保留
    items = await _items(factory, case.id)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        item = await db.get(ReviewItem, items[0].id)
        await svc.decide_item(db, fresh, item, status="verified", note="changed", user_id=USER_ID)
        await db.refresh(fresh)
        assert fresh.status == "in_review"
        assert fresh.completed_at is None
        for rest in items[1:]:
            db_item = await db.get(ReviewItem, rest.id)
            await svc.decide_item(db, fresh, db_item, status="verified", note=None, user_id=USER_ID)
        third = await svc.export_case(db, fresh, user_id=USER_ID)
        old = await db.get(ReviewExport, first.id)

    assert third.payload_sha256 != first.payload_sha256
    assert old.payload == first.payload, "旧导出行不可变"
    assert old.payload["items"][0]["decision_note"] == "ok"


@pytest.mark.asyncio
async def test_markdown_rendering_and_audit_rows(factory):
    case = await _seed_case(factory)
    await _decide_all(factory, case, status="verified")
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        export = await svc.export_case(db, fresh, user_id=USER_ID)
        markdown = svc.render_markdown(export.payload)
        await svc.audit_download(db, fresh, export, user_id=USER_ID)
        logs = (
            (await db.execute(select(UserActivityLog).where(UserActivityLog.target_id == case.id)))
            .scalars()
            .all()
        )

    assert "# 核验单：export case" in markdown
    assert "## 文档与版本" in markdown
    assert "## 核验项" in markdown
    assert "## 证据（原文引用）" in markdown
    assert "本核验单仅覆盖本次选定的条款与文档版本" in markdown
    assert "X-Amz" not in markdown
    actions = sorted(log.action for log in logs)
    assert actions == ["review_export", "review_export_download"]


@pytest.mark.asyncio
async def test_export_get_and_list_guard(factory):
    case = await _seed_case(factory)
    await _decide_all(factory, case)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        export = await svc.export_case(db, fresh, user_id=USER_ID)
        listed = await svc.list_exports(db, fresh)
        fetched = await svc.get_export_for_case(db, fresh, export.id)
        assert [row.id for row in listed] == [export.id]
        assert fetched.id == export.id
        with pytest.raises(NotFoundError):
            await svc.get_export_for_case(db, fresh, str(uuid.uuid4()))


# ── 迁移 023 round-trip ────────────────────────────────────────────────────

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "022_review_case_evidence"
NEW_REV = "023_review_export"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def test_023_migration_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "review-export-023.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    script = ScriptDirectory.from_config(cfg)

    assert len(NEW_REV) <= 32
    heads = set(script.get_heads())
    assert len(heads) == 1
    chain = [rev.revision for rev in script.iterate_revisions(next(iter(heads)), "base")]
    assert chain.index(PREV_REV) == chain.index(NEW_REV) + 1

    command.upgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "review_export" not in tables

    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        columns = {row[1]: row for row in conn.execute("PRAGMA table_info('review_export')")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list('review_export')")}
        fks = {(row[2], row[3]) for row in conn.execute("PRAGMA foreign_key_list('review_export')")}
    assert set(columns) == {
        "id",
        "case_id",
        "organization_id",
        "schema_version",
        "payload",
        "payload_sha256",
        "item_count",
        "needs_human_count",
        "insufficient_count",
        "created_by",
        "created_at",
    }
    assert set(columns) == {
        column.name for column in Base.metadata.tables["review_export"].columns
    }, "迁移列与 ORM 模型漂移"
    assert "ix_review_export_case_created" in indexes
    assert ("organizations", "organization_id") in fks
    assert ("review_case", "case_id") in fks

    command.downgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "review_export" not in tables
