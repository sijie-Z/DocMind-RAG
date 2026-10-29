"""核验决策与核验单导出（Batch 4）真实 SQLite 单测。"""

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.review import ReviewDocumentMeta, ReviewExport, ReviewItem
from app.models.user_audit import UserActivityLog
from app.services import review_analysis_service as analysis_service
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


async def _seed_case(factory, suffix=""):
    policy_id, base_id, revised_id = f"{POLICY}{suffix}", f"{BASE}{suffix}", f"{REVISED}{suffix}"
    async with factory() as db:
        for doc_id, role, family, label, seq in (
            (policy_id, "policy", None, None, None),
            (base_id, "contract", f"supplier-x{suffix}", "V1", 1),
            (revised_id, "contract", f"supplier-x{suffix}", "V2", 2),
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
                _chunk(policy_id, 0, "第3条 采购付款应遵循本制度，另有约定的从其约定。"),
                _chunk(base_id, 0, "第八条 付款期限为30天，自收到发票之日起算。"),
                _chunk(base_id, 1, "第九条 验收合格后支付价款。"),
                _chunk(revised_id, 0, "第九条 付款期限为60天，自收到发票之日起算。"),
                _chunk(revised_id, 1, "第十条 验收合格后支付价款。"),
            ]
        )
        await db.commit()
        docs = {
            "policy": await db.get(Document, policy_id),
            "base": await db.get(Document, base_id),
            "revised": await db.get(Document, revised_id),
        }
        return await case_service.create_case(
            db,
            docs=docs,
            title=f"export case{suffix}",
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


async def _analyze(factory, case):
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        await analysis_service.analyze_case(db, fresh)


async def _decide_all(factory, case, *, status="verified", note="ok"):
    items = await _items(factory, case.id)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        for item in items:
            db_item = await db.get(ReviewItem, item.id)
            await svc.decide_item(db, fresh, db_item, status=status, note=note, user_id=USER_ID)


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
        assert fresh.lock_version >= 2


@pytest.mark.asyncio
async def test_export_blocked_until_all_items_decided(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        with pytest.raises(ConflictError) as excinfo:
            await svc.export_case(db, fresh, user_id=USER_ID)
    err = excinfo.value
    assert err.error_code == "REVIEW_EXPORT_BLOCKED"
    blocking = err.detail["items"]
    assert {item["item_key"] for item in blocking} == {
        "payment_term",
        "start_condition",
        "acceptance_invoice",
        "exception",
    }
    assert all(item["reasons"] == ["undecided"] for item in blocking)

    await _decide_all(factory, case)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        export = await svc.export_case(db, fresh, user_id=USER_ID)
    assert export.item_count == 4
    assert export.schema_version == 1
    assert export.payload_sha256 == svc.payload_sha256(export.payload)


@pytest.mark.asyncio
async def test_export_blocked_merges_reasons_for_new_case(factory):
    """未决策且未分析的新案件：每个条目一条记录，reasons 同时含两项。"""
    case = await _seed_case(factory)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        with pytest.raises(ConflictError) as excinfo:
            await svc.export_case(db, fresh, user_id=USER_ID)
    blocking = excinfo.value.detail["items"]
    assert len(blocking) == 4, "每个核验项只出现一次"
    assert all(item["reasons"] == ["undecided", "not_analyzed"] for item in blocking)


@pytest.mark.asyncio
async def test_export_requires_analysis_first(factory):
    case = await _seed_case(factory)
    await _decide_all(factory, case)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        with pytest.raises(ConflictError) as excinfo:
            await svc.export_case(db, fresh, user_id=USER_ID)
    blocking = excinfo.value.detail["items"]
    assert blocking and all(item["reasons"] == ["not_analyzed"] for item in blocking)


@pytest.mark.asyncio
async def test_export_gate_blocks_insufficient_and_parse_failed(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
    await _decide_all(factory, case)
    items = await _items(factory, case.id)
    async with factory() as db:
        row = await db.get(ReviewItem, items[0].id)
        row.status = "insufficient"
        await db.commit()
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        with pytest.raises(ConflictError) as excinfo:
            await svc.export_case(db, fresh, user_id=USER_ID)
    assert any(
        item["status"] == "insufficient" and "undecided" in item["reasons"]
        for item in excinfo.value.detail["items"]
    )

    async with factory() as db:
        row = await db.get(ReviewItem, items[0].id)
        row.status = "parse_failed"
        await db.commit()
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        with pytest.raises(ConflictError):
            await svc.export_case(db, fresh, user_id=USER_ID)


@pytest.mark.asyncio
async def test_export_payload_contract(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
    await _decide_all(factory, case, status="needs_human")
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
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
    assert payment_payload["change_type"] == "modified"
    base_rows = payment_payload["evidence"]["base"]
    assert len(base_rows) == 1, "导出只能包含已选证据，候选行不得混入"
    row = base_rows[0]
    assert row["text_provenance"] == "parser_original"
    assert row["role"] == "main"
    assert row["stale"] is False
    assert "【文档背景" not in row["quote"]
    assert row["quote_sha256"]
    assert export.needs_human_count == 4
    assert export.unidentified_count == 1, "exception 项应为 unidentified"
    assert payload["coverage"]["needs_human_items"]

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "chunk_text" not in serialized
    assert "X-Amz" not in serialized
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

    # Markdown 渲染器的生成文案同样不得含禁词（把原文 quote 清空后扫描）。
    blanked = json.loads(serialized)
    for item in blanked["items"]:
        for rows in item["evidence"].values():
            for row in rows:
                row["quote"] = ""
    markdown_generated = svc.render_markdown(blanked).lower()
    for banned in (
        "合法",
        "违法",
        "合规",
        "无风险",
        "可签",
        "legal conclusion",
        "risk score",
        "compliance status",
        "minio",
        "x-amz",
    ):
        assert banned not in markdown_generated


@pytest.mark.asyncio
async def test_export_is_deterministic_and_rows_immutable(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
    await _decide_all(factory, case)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        first = await svc.export_case(db, fresh, user_id=USER_ID)
        second = await svc.export_case(db, fresh, user_id=USER_ID)
    assert first.payload_sha256 == second.payload_sha256, "无变更重导出必须同哈希"
    assert first.id != second.id

    items = await _items(factory, case.id)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        item = await db.get(ReviewItem, items[0].id)
        await svc.decide_item(db, fresh, item, status="verified", note="changed", user_id=USER_ID)
        await db.refresh(fresh)
        assert fresh.status == "in_review"
        assert fresh.completed_at is None
        assert fresh.completed_by is None
        for rest in items[1:]:
            db_item = await db.get(ReviewItem, rest.id)
            await svc.decide_item(db, fresh, db_item, status="verified", note=None, user_id=USER_ID)
        third = await svc.export_case(db, fresh, user_id=USER_ID)
        old = await db.get(ReviewExport, first.id)

    assert third.payload_sha256 != first.payload_sha256
    assert old.payload == first.payload, "旧导出行不可变"
    assert old.payload["items"][0]["decision_note"] == "ok"


@pytest.mark.asyncio
async def test_export_payload_marks_stale_source(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
    await _decide_all(factory, case)
    async with factory() as db:
        await db.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == REVISED)
        )
        db.add(_chunk(REVISED, 0, "第九条 付款期限为90天，自收到发票之日起算。"))
        await db.execute(
            update(Document).where(Document.id == REVISED).values(indexed_at=datetime.now(UTC))
        )
        await db.commit()
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        export = await svc.export_case(db, fresh, user_id=USER_ID)
    assert export.payload["source_stale"] is True


@pytest.mark.asyncio
async def test_markdown_rendering_and_audit_rows(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
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
    assert "| 决策人 | 决策时间 |" in markdown
    assert "## 证据（原文引用）" in markdown
    assert "本核验单仅覆盖本次选定的条款与文档版本" in markdown
    assert "X-Amz" not in markdown
    assert "minio" not in markdown.lower()
    actions = sorted(log.action for log in logs)
    assert actions == ["review_export", "review_export_download"]


@pytest.mark.asyncio
async def test_markdown_escapes_table_and_flattens_quotes(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
    items = await _items(factory, case.id)
    async with factory() as db:
        fresh = await db.get(type(case), case.id)
        fresh.title = "export case\n## 伪造章节"
        await db.commit()
        for item in items:
            db_item = await db.get(ReviewItem, item.id)
            note = "已核对 | 见附件\n第二行" if item.id == items[0].id else "ok"
            await svc.decide_item(db, fresh, db_item, status="verified", note=note, user_id=USER_ID)
        export = await svc.export_case(db, fresh, user_id=USER_ID)
        markdown = svc.render_markdown(export.payload)
    assert "已核对 \\| 见附件 第二行" in markdown
    assert "\n第二行" not in markdown
    assert "# 核验单：export case ## 伪造章节" in markdown, "标题换行必须折叠"
    assert "\n## 伪造章节" not in markdown, "标题不得注入独立章节"


@pytest.mark.asyncio
async def test_export_get_and_list_guard(factory):
    case = await _seed_case(factory)
    await _analyze(factory, case)
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


@pytest.mark.asyncio
async def test_export_cross_case_export_is_404(factory):
    case_a = await _seed_case(factory)
    case_b = await _seed_case(factory, suffix="-2")
    await _analyze(factory, case_a)
    await _decide_all(factory, case_a)
    async with factory() as db:
        fresh_a = await db.get(type(case_a), case_a.id)
        export = await svc.export_case(db, fresh_a, user_id=USER_ID)
        fresh_b = await db.get(type(case_b), case_b.id)
        with pytest.raises(NotFoundError):
            await svc.get_export_for_case(db, fresh_b, export.id)


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
        "unidentified_count",
        "created_by",
        "created_at",
    }
    assert set(columns) == {
        column.name for column in Base.metadata.tables["review_export"].columns
    }, "迁移列与 ORM 模型漂移"
    assert columns["payload"][2].upper() == "JSON"
    assert columns["created_at"][4] is not None, "created_at 必须有 server_default"
    assert "ix_review_export_case_created" in indexes
    assert ("organizations", "organization_id") in fks
    assert ("review_case", "case_id") in fks

    command.downgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "review_export" not in tables
