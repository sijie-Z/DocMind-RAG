"""Batch 3 确定性抽取与 V1↔V2 对齐：fixture 矩阵 + 幂等 + 无网络 + 禁用词。"""

import json
import socket
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base
from app.exceptions import ConflictError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.review import ReviewCase, ReviewDocumentMeta, ReviewEvidence
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services import review_analysis_service as analysis
from app.services import review_case_service as case_service

ORG = 1
USER_ID = 7
POLICY = "doc-an-policy"
BASE = "doc-an-base"
REVISED = "doc-an-revised"


def _clause(text, *, chunk_index=0, clause_no=None):
    return analysis.Clause(
        chunk_id=str(uuid.uuid4()),
        chunk_index=chunk_index,
        text=text,
        clause_no=clause_no or [],
    )


# ── 纯函数 fixture 矩阵 ─────────────────────────────────────────────────────


def test_payment_term_units_identified():
    day = analysis.extract_payment_term([_clause("第一条 付款期限为30天。", clause_no=[1])])
    assert day["status"] == "identified"
    assert (day["value"], day["unit"]) == (30, "day")
    assert day["clause_no"] == [1]

    workday = analysis.extract_payment_term([_clause("第二条 付款期限为30个工作日。")])
    assert (workday["value"], workday["unit"]) == (30, "workday")

    month = analysis.extract_payment_term([_clause("第三条 账期为2个月。")])
    assert (month["value"], month["unit"]) == (2, "month")


def test_payment_term_single_ri_is_ambiguous():
    """「30日内」无法判定自然日/工作日：必须 ambiguous，不得猜单位。"""
    facts = analysis.extract_payment_term([_clause("第一条 付款期限为30日内。")])
    assert facts["status"] == "ambiguous"
    assert "unit_unclear" in facts["reason"]
    assert "value" not in facts, "ambiguous 不得给出猜测值"


def test_payment_term_multiple_durations_ambiguous():
    facts = analysis.extract_payment_term(
        [_clause("第一条 付款分两期：首款30天，尾款60天，均自验收合格起算。")]
    )
    assert facts["status"] == "ambiguous"
    assert "multiple_durations" in facts["reason"]
    values = {(c["value"], c["unit"]) for c in facts["candidates"]}
    assert values == {(30, "day"), (60, "day")}


def test_payment_term_not_found_reasons():
    no_payment = analysis.extract_payment_term([_clause("第一条 争议解决方式为仲裁。")])
    assert no_payment == {"status": "not_found", "reason": "no_payment_clause"}

    no_duration = analysis.extract_payment_term([_clause("第一条 合同价款按附件结算。")])
    assert no_duration == {"status": "not_found", "reason": "no_duration"}


def test_start_condition_and_acceptance_terms():
    clauses = [
        _clause("第八条 付款期限为30天，自收到发票之日起算。", clause_no=[8]),
        _clause("第九条 验收合格并完成对账单确认后支付尾款。", clause_no=[9]),
    ]
    start = analysis.extract_start_condition(clauses)
    assert start["status"] == "identified"
    assert start["events"] == ["收到发票", "验收合格"]

    acceptance = analysis.extract_acceptance_invoice(clauses)
    assert acceptance["status"] == "identified"
    # 长词优先：验收合格存在时不再单独记「验收」；对账单存在时不再记「对账」。
    assert acceptance["terms"] == ["发票", "验收合格", "对账单"]


def test_exception_terms_and_absence():
    found = analysis.extract_exception([_clause("第十条 因不可抗力暂缓履行的，双方另行协商。")])
    assert found["status"] == "identified"
    assert set(found["terms"]) == {"不可抗力", "暂缓", "另行协商"}

    missing = analysis.extract_exception([_clause("第一条 付款期限为30天。")])
    assert missing == {"status": "not_found", "reason": "no_terms"}


def test_normalize_strips_layout_noise_only():
    # NFKC 会把全角逗号归一成半角逗号；页码噪声被整段删除。
    assert analysis.normalize_clause_text("第 8 条 付款期限为 30 天 ，第 1 页 共 2 页") == (
        "第8条付款期限为30天,"
    )
    assert analysis.normalize_clause_text("第八条　付款期限为３０天") == "第八条付款期限为30天"


def test_align_fixture_matrix():
    base30 = analysis.extract_payment_term([_clause("第八条 付款期限为30天。", clause_no=[8])])
    revised60 = analysis.extract_payment_term([_clause("第八条 付款期限为60天。", clause_no=[8])])
    assert analysis.align_item("payment_term", base30, revised60) == "modified"

    # 第 8 条 → 第 9 条仅重编号：事实相同、条号不同。
    renumbered = analysis.extract_payment_term([_clause("第九条 付款期限为30天。", clause_no=[9])])
    assert analysis.align_item("payment_term", base30, renumbered) == "renumbered"

    # 仅排版变化（空格/全角/页码噪声）：归一化后事实一致、条号一致。
    layout = analysis.extract_payment_term([_clause("第八条  付款期限为 ３０ 天。", clause_no=[8])])
    assert analysis.align_item("payment_term", base30, layout) == "unchanged"

    empty = {"status": "not_found", "reason": "no_terms"}
    assert analysis.align_item("exception", base30, empty) == "removed"
    assert analysis.align_item("exception", empty, base30) == "added"
    assert analysis.align_item("exception", empty, dict(empty)) == "unidentified"

    ambiguous = analysis.extract_payment_term([_clause("第八条 付款期限为30日内。")])
    assert analysis.align_item("payment_term", ambiguous, revised60) == "unidentified"


def test_start_condition_added_event_is_modified():
    base = analysis.extract_start_condition(
        [_clause("第八条 付款期限为30天，自收到发票之日起算。")]
    )
    revised = analysis.extract_start_condition(
        [_clause("第八条 付款期限为30天，对账无异议并收到发票后起算。")]
    )
    assert analysis.align_item("start_condition", base, revised) == "modified"


# ── service 级（真实 SQLite） ───────────────────────────────────────────────


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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-analyze.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def _seed_case(factory, *, revised_text=None, legacy_revised=False):
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
                _chunk(
                    REVISED,
                    0,
                    revised_text or "第九条 付款期限为60天，对账无异议并收到发票之日起算。",
                ),
                _chunk(REVISED, 1, "第十条 验收合格后支付价款。"),
            ]
        )
        if legacy_revised:
            db.add(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=REVISED,
                    chunk_index=2,
                    chunk_text="旧文",
                    original_text=None,
                    chunk_length=2,
                    page_number=1,
                    meta_data={"text_provenance": "legacy_unverified"},
                )
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
            title="analyze case",
            checklist_version="v1",
            created_by=USER_ID,
        )


@pytest.mark.asyncio
async def test_analyze_updates_items_and_snapshots_primary_clause(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        detail = await analysis.analyze_case(db, case)

    items = {item["item_key"]: item for item in detail["items"]}
    payment = items["payment_term"]
    assert payment["change_type"] == "modified"
    assert payment["facts"]["base"]["value"] == 30
    assert payment["facts"]["revised"]["value"] == 60
    assert payment["facts"]["extractor_version"] == analysis.EXTRACTOR_VERSION

    start = items["start_condition"]
    assert start["change_type"] == "modified"
    assert "对账无异议" in start["facts"]["revised"]["events"]

    assert items["exception"]["change_type"] == "unidentified"
    assert items["exception"]["facts"]["base"] == {"status": "not_found", "reason": "no_terms"}

    # facts 的主条款会作为 candidate 证据快照（未人工选择）。
    for side_rows in payment["evidence"].values():
        for row in side_rows:
            assert row["text_provenance"] == "parser_original"
    assert payment["selected_evidence_count"] == 0


@pytest.mark.asyncio
async def test_analyze_is_deterministic_and_idempotent(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        first = await analysis.analyze_case(db, case)
        count_first = (
            await db.execute(
                select(func.count(ReviewEvidence.id)).where(ReviewEvidence.case_id == case.id)
            )
        ).scalar()
        second = await analysis.analyze_case(db, case)
        count_second = (
            await db.execute(
                select(func.count(ReviewEvidence.id)).where(ReviewEvidence.case_id == case.id)
            )
        ).scalar()

    def _items(detail):
        return {i["item_key"]: (i["change_type"], i["facts"]) for i in detail["items"]}

    assert _items(first) == _items(second), "同一输入重复运行必须逐字段一致"
    assert count_first == count_second, "重复分析不得重复插入证据快照"


@pytest.mark.asyncio
async def test_analyze_blocked_case_is_409(factory):
    seed = await _seed_case(factory)
    async with factory() as db:
        blocked = await db.get(ReviewCase, seed.id)
        blocked.status = "blocked"
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await analysis.analyze_case(db, blocked)
    assert excinfo.value.error_code == "REVIEW_CASE_NOT_READY"


@pytest.mark.asyncio
async def test_analyze_legacy_document_fails_closed(factory):
    """建案后 V2 被重建为 legacy：分析必须显式拒绝，不得把旧文本当原文。"""
    case = await _seed_case(factory)
    async with factory() as db:
        await db.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == REVISED)
        )
        db.add(
            DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=REVISED,
                chunk_index=0,
                chunk_text="旧文 付款期限60天",
                original_text=None,
                chunk_length=10,
                page_number=1,
                meta_data={"text_provenance": "legacy_unverified"},
            )
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(SourceProvenanceUnavailableError):
            await analysis.analyze_case(db, case)


@pytest.mark.asyncio
async def test_analyze_makes_no_network_calls(factory, monkeypatch):
    case = await _seed_case(factory)

    def _no_network(*_args, **_kwargs):
        raise AssertionError("核验分析不得使用网络")

    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)

    async with factory() as db:
        detail = await analysis.analyze_case(db, case)
    assert detail["items"], "无网络环境下分析必须正常工作"


@pytest.mark.asyncio
async def test_analysis_output_has_no_legal_conclusions(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        detail = await analysis.analyze_case(db, case)
    payload = json.dumps(
        [{"change_type": i["change_type"], "facts": i["facts"]} for i in detail["items"]],
        ensure_ascii=False,
    )
    for banned in ("合法", "违法", "合规", "不合规", "无风险", "可签", "风险分", "合规率"):
        assert banned not in payload
