"""核验案件 / 核验项 / 证据快照（Batch 2）真实 SQLite 单测。"""

import hashlib
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.organization import user_organization
from app.models.review import ReviewEvidence, ReviewItem
from app.models.user import User
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services import review_case_service as svc

ORG = 1
POLICY = "doc-case-policy"
BASE = "doc-case-base"
REVISED = "doc-case-revised"
USER_ID = 7


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-case.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_doc(factory, doc_id, *, status=DocumentStatus.INDEXED, org=ORG):
    async with factory() as db:
        db.add(
            Document(
                id=doc_id,
                filename=f"{doc_id}.pdf",
                file_path=f"{org}/{doc_id}.pdf",
                file_size=100,
                file_type=DocumentType.PDF,
                status=status,
                organization_id=org,
                uploaded_by=1,
            )
        )
        await db.commit()


def _meta_row(doc_id, *, role, family=None, label=None, seq=None, sha="a" * 64, org=ORG):
    from app.models.review import ReviewDocumentMeta

    return ReviewDocumentMeta(
        document_id=doc_id,
        organization_id=org,
        doc_role=role,
        family_key=family,
        version_label=label,
        version_seq=seq,
        content_sha256=sha,
        registered_by=1,
    )


async def _seed_meta(factory, doc_id, **kwargs):
    async with factory() as db:
        db.add(_meta_row(doc_id, **kwargs))
        await db.commit()


def _chunk_row(
    doc_id,
    idx,
    *,
    original,
    chunk_text=None,
    page=1,
    start=0,
    meta_data=None,
):
    if meta_data is None:
        meta_data = {"text_provenance": "parser_original" if original else "legacy_unverified"}
    return DocumentChunk(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        chunk_index=idx,
        chunk_text=chunk_text if chunk_text is not None else (original or ""),
        original_text=original,
        chunk_length=len(chunk_text if chunk_text is not None else (original or "")),
        start_pos=start,
        end_pos=start + len(original or ""),
        page_number=page,
        meta_data=meta_data,
    )


async def _seed_chunks(factory, doc_id, rows):
    async with factory() as db:
        db.add_all([_chunk_row(doc_id, i, **row) for i, row in enumerate(rows)])
        await db.commit()


async def _seed_ready_case(factory, *, extra_base_rows=()):
    """制度 + 合同 V1/V2（v2 版本序更大），全部可核验。"""
    for doc_id, role, family, label, seq, sha in (
        (POLICY, "policy", None, None, None, "p" * 64),
        (BASE, "contract", "supplier-x", "V1", 1, "b" * 64),
        (REVISED, "contract", "supplier-x", "V2", 2, "r" * 64),
    ):
        await _seed_doc(factory, doc_id)
        await _seed_meta(factory, doc_id, role=role, family=family, label=label, seq=seq, sha=sha)
    await _seed_chunks(
        factory,
        POLICY,
        [
            {
                "original": "第3条 采购付款应遵循本制度，另有约定的从其约定。",
                "chunk_text": "【文档背景：制度摘要】第3条 采购付款应遵循本制度，另有约定的从其约定。",
            }
        ],
    )
    await _seed_chunks(
        factory,
        BASE,
        [
            {
                "original": "第一条 付款期限为30天，自收到发票之日起算。",
                "chunk_text": "【文档背景：合同摘要】第一条 付款期限为30天，自收到发票之日起算。",
            },
            {"original": "第二条 验收合格后支付价款。"},
            *extra_base_rows,
        ],
    )
    await _seed_chunks(
        factory,
        REVISED,
        [
            {
                "original": "第一条 付款期限为60天，自收到发票之日起算。",
                "chunk_text": "【文档背景：修订摘要】第一条 付款期限为60天，自收到发票之日起算。",
            },
            {"original": "第二条 验收合格后支付价款。"},
        ],
    )


async def _create_case(factory, *, policy=POLICY, base=BASE, revised=REVISED, **kwargs):
    async with factory() as db:
        docs = {
            "policy": await db.get(Document, policy),
            "base": await db.get(Document, base),
            "revised": await db.get(Document, revised),
        }
        return await svc.create_case(
            db,
            docs=docs,
            title=kwargs.pop("title", "采购合同 V1→V2 核验"),
            checklist_version=kwargs.pop("checklist_version", "v1"),
            created_by=USER_ID,
            **kwargs,
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


async def _evidence(factory, case_id):
    async with factory() as db:
        return (
            (await db.execute(select(ReviewEvidence).where(ReviewEvidence.case_id == case_id)))
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_ready_case_creates_items_and_original_evidence(factory):
    await _seed_ready_case(factory)
    case = await _create_case(factory)

    assert case.status == "ready"
    assert case.blocking_reasons == []
    assert case.organization_id == ORG
    assert case.lock_version == 1

    items = await _items(factory, case.id)
    assert [item.item_key for item in items] == [
        "payment_term",
        "start_condition",
        "acceptance_invoice",
        "exception",
    ]
    assert all(item.status == "pending" for item in items)
    assert all(item.change_type is None for item in items)
    assert all(item.facts is None for item in items)

    rows = await _evidence(factory, case.id)
    assert {row.side for row in rows} == {"policy", "base", "revised"}
    assert rows, "建案必须落下证据快照"
    for row in rows:
        assert row.text_provenance == "parser_original"
        assert "【文档背景" not in row.quote_text, "快照只允许 parser 原文"
        assert row.quote_sha256 == hashlib.sha256(row.quote_text.encode()).hexdigest()
        assert row.selected is False
        assert row.role == "candidate"
        expected_sha = {"policy": "p" * 64, "base": "b" * 64, "revised": "r" * 64}[row.side]
        assert row.document_sha256 == expected_sha
    # 同一证据块可命中多个核验项：payment_term/start_condition 都对第一块命中。
    payment = [i for i in items if i.item_key == "payment_term"][0]
    assert any(row.item_id == payment.id and row.side == "base" for row in rows)


@pytest.mark.asyncio
async def test_case_detail_exposes_documents_items_and_fresh_snapshot(factory):
    await _seed_ready_case(factory)
    case = await _create_case(factory)

    async with factory() as db:
        detail = await svc.case_detail(db, case)

    assert detail["status"] == "ready"
    assert set(detail["documents"]) == {"policy", "base", "revised"}
    assert detail["documents"]["base"]["version_label"] == "V1"
    assert detail["documents"]["revised"]["version_label"] == "V2"
    assert detail["documents"]["base"]["readiness"]["status"] == "reviewable"
    assert detail["documents"]["base"]["missing"] is False
    assert detail["documents"]["base"]["identity_changed"] is False
    assert detail["live_source_stale"] is False
    assert detail["stale_evidence_count"] == 0

    assert [i["item_key"] for i in detail["items"]] == [
        "payment_term",
        "start_condition",
        "acceptance_invoice",
        "exception",
    ]
    for item in detail["items"]:
        assert set(item["evidence"]) == {"policy", "base", "revised"}
        assert item["selected_evidence_count"] == 0


@pytest.mark.asyncio
async def test_legacy_revised_document_blocks_case_and_no_evidence(factory):
    await _seed_ready_case(factory)
    async with factory() as db:
        await db.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == REVISED)
        )
        db.add(_chunk_row(REVISED, 0, original=None, chunk_text="【旧文】第一条 付款期限60天"))
        await db.commit()

    case = await _create_case(factory)
    assert case.status == "blocked"
    assert "revised:no_original_text" in case.blocking_reasons

    items = await _items(factory, case.id)
    assert len(items) == 4, "blocked 案件也要生成 4 个固定核验项"
    rows = await _evidence(factory, case.id)
    assert rows == [], "blocked 案件不落证据快照"

    async with factory() as db:
        item = items[0]
        with pytest.raises(ConflictError) as excinfo:
            await svc.select_evidence(
                db,
                case,
                item,
                side="base",
                chunk_id="whatever",
                role="main",
                selected=True,
            )
    assert excinfo.value.error_code == "REVIEW_CASE_NOT_READY"


@pytest.mark.asyncio
async def test_family_mismatch_and_version_order_block_case(factory):
    await _seed_ready_case(factory)
    await _seed_doc(factory, "doc-other-family")
    await _seed_meta(
        factory, "doc-other-family", role="contract", family="supplier-y", label="V2", seq=2
    )
    case = await _create_case(factory, revised="doc-other-family")
    assert case.status == "blocked"
    assert "family_mismatch" in case.blocking_reasons

    await _seed_doc(factory, "doc-older-seq")
    await _seed_meta(
        factory, "doc-older-seq", role="contract", family="supplier-x", label="V0", seq=0
    )
    case2 = await _create_case(factory, revised="doc-older-seq", title="version order")
    assert case2.status == "blocked"
    assert "version_order" in case2.blocking_reasons


@pytest.mark.asyncio
async def test_wrong_role_and_missing_meta_block_case(factory):
    await _seed_ready_case(factory)
    await _seed_doc(factory, "doc-role-policy")
    await _seed_meta(factory, "doc-role-policy", role="policy")
    case = await _create_case(factory, revised="doc-role-policy")
    assert case.status == "blocked"
    assert "revised:wrong_role" in case.blocking_reasons

    await _seed_doc(factory, "doc-no-meta")
    case2 = await _create_case(factory, policy="doc-no-meta", title="missing meta")
    assert case2.status == "blocked"
    assert "policy:missing_version_meta" in case2.blocking_reasons


@pytest.mark.asyncio
async def test_readiness_reason_codes_parse_failed_not_indexed_wrong_org(factory):
    await _seed_ready_case(factory)

    await _seed_doc(factory, "doc-failed", status=DocumentStatus.FAILED)
    await _seed_meta(factory, "doc-failed", role="contract", family="supplier-x", label="V9", seq=9)
    await _seed_chunks(factory, "doc-failed", [{"original": "付款条款", "page": 1}])
    case = await _create_case(factory, revised="doc-failed", title="parse failed")
    assert case.status == "blocked"
    assert "revised:parse_failed" in case.blocking_reasons

    await _seed_doc(factory, "doc-pending", status=DocumentStatus.PENDING)
    await _seed_meta(
        factory, "doc-pending", role="contract", family="supplier-x", label="V8", seq=8
    )
    await _seed_chunks(factory, "doc-pending", [{"original": "付款条款", "page": 1}])
    case2 = await _create_case(factory, revised="doc-pending", title="not indexed")
    assert "revised:not_indexed" in case2.blocking_reasons

    await _seed_doc(factory, "doc-wrong-org")
    await _seed_meta(
        factory,
        "doc-wrong-org",
        role="contract",
        family="supplier-x",
        label="V7",
        seq=7,
        org=2,
    )
    await _seed_chunks(factory, "doc-wrong-org", [{"original": "付款条款", "page": 1}])
    case3 = await _create_case(factory, revised="doc-wrong-org", title="wrong org meta")
    assert "revised:wrong_org" in case3.blocking_reasons


@pytest.mark.asyncio
async def test_template_role_allowed_on_policy_side(factory):
    await _seed_ready_case(factory)
    await _seed_doc(factory, "doc-template")
    await _seed_meta(factory, "doc-template", role="template")
    await _seed_chunks(
        factory, "doc-template", [{"original": "模板：付款期限按附件填写。", "page": 1}]
    )
    case = await _create_case(factory, policy="doc-template", title="template policy")
    assert case.status == "ready", case.blocking_reasons


@pytest.mark.asyncio
async def test_create_case_rejects_same_or_cross_org_documents(factory):
    await _seed_ready_case(factory)
    with pytest.raises(ValidationError):
        await _create_case(factory, revised=BASE)

    await _seed_doc(factory, "doc-org2", org=2)
    await _seed_meta(factory, "doc-org2", role="contract", family="supplier-x", label="V9", seq=9)
    with pytest.raises(ValidationError):
        await _create_case(factory, revised="doc-org2")

    with pytest.raises(ValidationError):
        await _create_case(factory, checklist_version="v9")

    with pytest.raises(ValidationError):
        await _create_case(factory, title="   ")


@pytest.mark.asyncio
async def test_rebuild_marks_live_source_stale_without_touching_snapshot(factory):
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    before = {
        (row.side, row.quote_text): row.quote_sha256 for row in await _evidence(factory, case.id)
    }
    revised_before = [q for (side, q) in before if side == "revised"]
    assert revised_before

    # 模拟 rebuild：V2 的分块全部删除并按新正文重建（chunk id 重新生成）。
    async with factory() as db:
        await db.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == REVISED)
        )
        db.add(_chunk_row(REVISED, 0, original="第一条 付款期限为90天，另需对账。"))
        db.add(_chunk_row(REVISED, 1, original="第二条 验收合格后支付价款。"))
        await db.commit()

    async with factory() as db:
        detail = await svc.case_detail(db, case)

    assert detail["live_source_stale"] is True
    assert detail["stale_evidence_count"] > 0

    rows = await _evidence(factory, case.id)
    after = {(row.side, row.quote_text): row.quote_sha256 for row in rows}
    assert after == before, "快照不得被实时正文悄悄替换"
    for row in rows:
        assert row.quote_sha256 == hashlib.sha256(row.quote_text.encode()).hexdigest()

    stale_info = {
        (item["item_key"], side, row["id"]): row["stale"]
        for item in detail["items"]
        for side, rows_ in item["evidence"].items()
        for row in rows_
    }
    assert any(stale for stale in stale_info.values())
    assert all(
        stale for (item_key, side, _rid), stale in stale_info.items() if side == "revised"
    ), "V2 重建后 revised 侧快照必须全部标记 stale"
    assert all(
        not stale for (_k, side, _rid), stale in stale_info.items() if side in ("base", "policy")
    )


@pytest.mark.asyncio
async def test_select_evidence_snapshots_and_rejects_foreign_chunks(factory):
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    items = await _items(factory, case.id)
    payment = items[0]

    async with factory() as db:
        base_chunk = (
            await db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == BASE)
                .order_by(DocumentChunk.chunk_index)
                .limit(1)
            )
        ).scalar_one()
        row = await svc.select_evidence(
            db,
            case,
            payment,
            side="base",
            chunk_id=base_chunk.id,
            role="main",
            selected=True,
        )
    assert row.selected is True
    assert row.role == "main"
    async with factory() as db:
        row = await db.get(ReviewEvidence, row.id)
        assert row.selected is True
        assert row.role == "main"
        assert row.quote_text == base_chunk.original_text
        assert row.quote_sha256 == hashlib.sha256(row.quote_text.encode()).hexdigest()
        assert row.document_sha256 == "b" * 64
        assert row.chunk_id_at_capture == base_chunk.id
        assert row.clause_no == [1]

        # 取消选择：同一证据行保留快照，只改 selected。
        row2 = await svc.select_evidence(
            db,
            case,
            payment,
            side="base",
            chunk_id=base_chunk.id,
            role="main",
            selected=False,
        )
        assert row2.selected is False
        assert row2.quote_sha256 == row.quote_sha256

    # 另一文档的 chunk 不能当作本侧证据。
    async with factory() as db:
        policy_chunk = (
            await db.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == POLICY).limit(1)
            )
        ).scalar_one()
        with pytest.raises(NotFoundError):
            await svc.select_evidence(
                db, case, payment, side="base", chunk_id=policy_chunk.id, role="main", selected=True
            )

    # legacy 分块：显式拒绝，不回退 chunk_text。
    async with factory() as db:
        db.add(_chunk_row(BASE, 99, original=None, chunk_text="旧文本 付款期限30天"))
        await db.commit()
        legacy_chunk = (
            await db.execute(
                select(DocumentChunk).where(
                    DocumentChunk.document_id == BASE, DocumentChunk.chunk_index == 99
                )
            )
        ).scalar_one()
        with pytest.raises(SourceProvenanceUnavailableError):
            await svc.select_evidence(
                db,
                case,
                payment,
                side="base",
                chunk_id=legacy_chunk.id,
                role="main",
                selected=True,
            )

    # 不属于本案件的核验项 id。
    async with factory() as db:
        with pytest.raises(NotFoundError):
            await svc.get_item_for_case(db, case, str(uuid.uuid4()))


async def _seed_user(factory, user_id, *, org, active=True):
    async with factory() as db:
        db.add(
            User(
                id=user_id,
                username=f"u{user_id}",
                email=f"u{user_id}@example.com",
                hashed_password="x",
                role="user",
                is_active=active,
                organization_id=org,
            )
        )
        await db.commit()


@pytest.mark.asyncio
async def test_assignee_must_be_active_member_of_case_org(factory):
    await _seed_ready_case(factory)
    await _seed_user(factory, 21, org=2)
    await _seed_user(factory, 22, org=ORG, active=False)
    await _seed_user(factory, 23, org=2)
    async with factory() as db:
        await db.execute(user_organization.insert().values(user_id=23, organization_id=ORG))
        await db.commit()

    with pytest.raises(ValidationError):
        await _create_case(factory, assignee_id=21, title="foreign assignee")
    with pytest.raises(ValidationError):
        await _create_case(factory, assignee_id=22, title="inactive assignee")
    with pytest.raises(ValidationError):
        await _create_case(factory, assignee_id=999, title="missing assignee")

    case = await _create_case(factory, assignee_id=23, title="member assignee")
    assert case.status == "ready"
    assert case.assignee_id == 23


@pytest.mark.asyncio
async def test_rebuild_without_evidence_marks_identity_changed(factory):
    """某一侧零候选证据时，文档 rebuild 也必须被发现（不能依赖证据行）。"""
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    async with factory() as db:
        await db.execute(
            ReviewEvidence.__table__.delete().where(
                ReviewEvidence.case_id == case.id, ReviewEvidence.side == "policy"
            )
        )
        await db.commit()

    async with factory() as db:
        before = await svc.case_detail(db, case)
    assert before["live_source_stale"] is False, "无变化的案件不得误报 stale"

    async with factory() as db:
        await db.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == POLICY)
        )
        db.add(_chunk_row(POLICY, 0, original="采购制度新版本正文，不含旧关键词。"))
        await db.execute(
            update(Document).where(Document.id == POLICY).values(indexed_at=datetime.now(UTC))
        )
        await db.commit()

    async with factory() as db:
        after = await svc.case_detail(db, case)
    assert after["documents"]["policy"]["identity_changed"] is True
    assert after["live_source_stale"] is True
    assert after["stale_evidence_count"] == 0, "该侧没有任何证据行，仍须靠文档指纹发现变化"
    assert after["documents"]["policy"]["readiness"]["status"] == "reviewable", (
        "建案快照不得被实时状态改写"
    )


@pytest.mark.asyncio
async def test_list_cases_filters_by_org_status_and_pages(factory):
    await _seed_ready_case(factory)
    await _seed_doc(factory, "doc-policy-2", org=2)
    await _seed_meta(factory, "doc-policy-2", role="policy")
    await _seed_doc(factory, "doc-base-2", org=2)
    await _seed_meta(factory, "doc-base-2", role="contract", family="f2", label="V1", seq=1)
    await _seed_doc(factory, "doc-revised-2", org=2)
    await _seed_meta(factory, "doc-revised-2", role="contract", family="f2", label="V2", seq=2)
    await _seed_chunks(factory, "doc-policy-2", [{"original": "制度", "page": 1}])
    await _seed_chunks(factory, "doc-base-2", [{"original": "付款30天", "page": 1}])
    await _seed_chunks(factory, "doc-revised-2", [{"original": "付款60天", "page": 1}])

    org1_case = await _create_case(factory)
    async with factory() as db:
        docs = {
            "policy": await db.get(Document, "doc-policy-2"),
            "base": await db.get(Document, "doc-base-2"),
            "revised": await db.get(Document, "doc-revised-2"),
        }
        await svc.create_case(
            db,
            docs=docs,
            title="org2 case",
            checklist_version="v1",
            created_by=USER_ID,
        )

    async with factory() as db:
        page = await svc.list_cases(db, organization_id=ORG)
        assert page["total"] == 1
        assert page["items"][0]["id"] == org1_case.id
        assert page["items"][0]["base_version_label"] == "V1"

        ready_only = await svc.list_cases(db, organization_id=ORG, status="ready")
        assert ready_only["total"] == 1
        none = await svc.list_cases(db, organization_id=ORG, status="blocked")
        assert none["total"] == 0

        org2_page = await svc.list_cases(db, organization_id=2, page_size=1)
        assert org2_page["total"] == 1

        with pytest.raises(ValidationError):
            await svc.list_cases(db, organization_id=ORG, status="unknown")


@pytest.mark.asyncio
async def test_candidate_capture_is_bounded_and_keyword_scoped(factory):
    await _seed_ready_case(
        factory,
        extra_base_rows=[{"original": f"付款补充约定 {n}：支付周期另行确认。"} for n in range(6)],
    )
    case = await _create_case(factory)
    items = await _items(factory, case.id)
    payment = [i for i in items if i.item_key == "payment_term"][0]
    async with factory() as db:
        count = (
            await db.execute(
                select(func.count(ReviewEvidence.id)).where(
                    ReviewEvidence.item_id == payment.id,
                    ReviewEvidence.side == "base",
                )
            )
        ).scalar()
    assert count == svc.EVIDENCE_CANDIDATES_PER_ITEM_SIDE


@pytest.mark.asyncio
async def test_deleted_document_marks_case_stale_and_missing(factory):
    await _seed_ready_case(factory)
    case = await _create_case(factory)
    async with factory() as db:
        await db.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == POLICY)
        )
        await db.execute(Document.__table__.delete().where(Document.id == POLICY))
        await db.commit()

    async with factory() as db:
        detail = await svc.case_detail(db, case)
    assert detail["documents"]["policy"]["missing"] is True
    assert detail["live_source_stale"] is True


# ── 迁移 022 round-trip ─────────────────────────────────────────────────────

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "021_chat_clear_watermark"
NEW_REV = "022_review_case_evidence"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def test_022_migration_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "review-case-022.db"
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
    for name in ("review_case", "review_item", "review_evidence"):
        assert name not in tables

    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        case_columns = {row[1]: row for row in conn.execute("PRAGMA table_info('review_case')")}
        item_columns = {row[1]: row for row in conn.execute("PRAGMA table_info('review_item')")}
        evidence_columns = {
            row[1]: row for row in conn.execute("PRAGMA table_info('review_evidence')")
        }
        evidence_indexes = {
            row[1]: row for row in conn.execute("PRAGMA index_list('review_evidence')")
        }
        evidence_fks = {
            (row[2], row[3]) for row in conn.execute("PRAGMA foreign_key_list('review_evidence')")
        }
        case_fks = {
            (row[2], row[3]) for row in conn.execute("PRAGMA foreign_key_list('review_case')")
        }

    assert set(case_columns) == {
        "id",
        "organization_id",
        "title",
        "policy_document_id",
        "base_document_id",
        "revised_document_id",
        "checklist_version",
        "status",
        "blocking_reasons",
        "readiness_snapshot",
        "created_by",
        "assignee_id",
        "completed_at",
        "completed_by",
        "lock_version",
        "created_at",
        "updated_at",
    }
    assert set(item_columns) == {
        "id",
        "case_id",
        "item_key",
        "ordinal",
        "change_type",
        "status",
        "facts",
        "decided_by",
        "decided_at",
        "decision_note",
        "created_at",
        "updated_at",
    }
    assert set(evidence_columns) == {
        "id",
        "case_id",
        "item_id",
        "side",
        "role",
        "selected",
        "document_id",
        "doc_role",
        "doc_version_label",
        "doc_version_seq",
        "document_sha256",
        "chunk_id_at_capture",
        "chunk_index",
        "clause_no",
        "section_no",
        "page_number",
        "char_start",
        "char_end",
        "quote_text",
        "quote_sha256",
        "text_provenance",
        "captured_at",
        "updated_at",
    }
    orm_tables = {
        "review_case": Base.metadata.tables["review_case"],
        "review_item": Base.metadata.tables["review_item"],
        "review_evidence": Base.metadata.tables["review_evidence"],
    }
    for name, pragma_cols in (
        ("review_case", case_columns),
        ("review_item", item_columns),
        ("review_evidence", evidence_columns),
    ):
        assert set(pragma_cols) == {column.name for column in orm_tables[name].columns}, (
            f"{name} 迁移列与 ORM 模型漂移"
        )
    assert evidence_columns["document_id"][3] == 1, "quote/document 快照必须 NOT NULL"
    assert evidence_columns["quote_text"][3] == 1
    assert ("documents", "document_id") not in evidence_fks, (
        "证据快照不得建到 documents 的外键（删除/重建后仍须可读）"
    )
    assert ("review_case", "case_id") in evidence_fks
    assert ("review_item", "item_id") in evidence_fks
    assert ("review_case", "case_id") not in case_fks
    assert ("organizations", "organization_id") in case_fks
    unique_columns = set()
    with sqlite3.connect(str(db_path)) as conn:
        for name, row in evidence_indexes.items():
            if row[2] == 1:
                unique_columns.add(
                    tuple(r[2] for r in conn.execute(f"PRAGMA index_info('{name}')"))
                )
    assert ("item_id", "side", "chunk_id_at_capture") in unique_columns

    command.downgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    for name in ("review_case", "review_item", "review_evidence"):
        assert name not in tables
