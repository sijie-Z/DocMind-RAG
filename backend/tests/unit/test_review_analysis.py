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
from app.models.review import ReviewCase, ReviewDocumentMeta, ReviewEvidence, ReviewItem
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
        text=analysis.normalize_clause_text(text),
        raw_text=text,
        clause_no=clause_no or [],
        section_no=[],
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
    assert missing == {"status": "not_found", "reason": "no_terms", "cross_refs": []}


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


def test_dates_and_warranty_periods_are_not_payment_terms():
    """日期片段、保修/质保期不得被当作账期（评审 F1）。"""
    date_only = analysis.extract_payment_term(
        [_clause("第三条 付款方式为银行转账。本合同于2025年3月生效。")]
    )
    assert date_only == {"status": "not_found", "reason": "no_duration"}

    warranty = analysis.extract_payment_term(
        [_clause("第一条 合同总价3%为质保金，保修期为12个月，付款时扣留。")]
    )
    assert warranty == {"status": "not_found", "reason": "no_duration"}

    chinese = analysis.extract_payment_term(
        [_clause("第一条 付款期限为六个月，合同于2025年3月签订。")]
    )
    assert chinese == {"status": "not_found", "reason": "no_duration"}

    mixed = analysis.extract_payment_term(
        [_clause("第一条 付款期限为30天，合同签署日期2025年3月15日。")]
    )
    assert mixed["status"] == "identified"
    assert (mixed["value"], mixed["unit"]) == (30, "day")


def test_postfix_payment_phrasing_is_identified():
    """中文合同常见语序：付款动词在期限数字之后（评审 A/B R2 阻断项）。"""
    invoice = analysis.extract_payment_term([_clause("第八条 乙方应在收到发票后30天内支付货款。")])
    assert invoice["status"] == "identified"
    assert (invoice["value"], invoice["unit"]) == (30, "day")

    workday = analysis.extract_payment_term([_clause("第八条 验收合格后30个工作日内支付尾款。")])
    assert (workday["value"], workday["unit"]) == (30, "workday")

    month = analysis.extract_payment_term([_clause("第八条 甲方应在2个月内付款。")])
    assert (month["value"], month["unit"]) == (2, "month")

    term = analysis.extract_payment_term([_clause("第八条 甲方给予乙方30天账期。")])
    assert (term["value"], term["unit"]) == (30, "day")

    effective = analysis.extract_payment_term([_clause("第八条 本合同生效后30天内支付首付款。")])
    assert effective["status"] == "identified"

    signed = analysis.extract_payment_term([_clause("第八条 合同签署后30天内支付首付款。")])
    assert signed["status"] == "identified"


def test_payment_anchor_in_adjacent_fragment_is_inherited():
    """「…后 30 日内，向乙方支付…」：付款动作在下一个分句也要识别。"""
    workday = analysis.extract_payment_term(
        [_clause("第八条 验收合格后30个工作日内，甲方支付至合同总价的95%。")]
    )
    assert workday["status"] == "identified"
    assert (workday["value"], workday["unit"]) == (30, "workday")

    month = analysis.extract_payment_term([_clause("第八条 甲方应在2个月内，向乙方付清全部货款。")])
    assert (month["value"], month["unit"]) == (2, "month")

    ri = analysis.extract_payment_term(
        [_clause("第八条 甲方应在收到乙方开具的发票后30日内，向乙方支付合同价款的95%。")]
    )
    assert ri["status"] == "ambiguous"
    assert "unit_unclear" in ri["reason"]


def test_non_payment_periods_are_not_inherited_as_terms():
    """交货期/合同期限/质量保证期不得被相邻付款分句继承成账期。"""
    delivery = analysis.extract_payment_term([_clause("第八条 付款方式为银行转账，交货期为30天。")])
    assert delivery == {"status": "not_found", "reason": "no_duration"}

    contract = analysis.extract_payment_term(
        [_clause("第八条 付款方式为银行转账，合同期限为12个月。")]
    )
    assert contract == {"status": "not_found", "reason": "no_duration"}

    quality = analysis.extract_payment_term(
        [_clause("第八条 付款期限为30天，质量保证期为24个月。")]
    )
    assert quality["status"] == "identified"
    assert (quality["value"], quality["unit"]) == (30, "day")


def test_equal_distance_prefers_payment_anchor():
    """「到期后30天内付款」付款与排除词等距时应按付款处理，而不是误杀。"""
    facts = analysis.extract_payment_term([_clause("第八条 到期后30天内付款。")])
    assert facts["status"] == "identified"
    assert (facts["value"], facts["unit"]) == (30, "day")


def test_cross_fragment_overdue_is_not_payment_term():
    facts = analysis.extract_payment_term(
        [_clause("第八条 甲方逾期付款，超过30天的，乙方有权解除合同。")]
    )
    assert facts == {"status": "not_found", "reason": "no_duration"}


def test_postfix_ri_is_ambiguous_not_not_found():
    """「30日内支付」：期限能识别、单位存疑 → ambiguous，不是 not_found。"""
    facts = analysis.extract_payment_term([_clause("第八条 甲方应在30日内支付货款。")])
    assert facts["status"] == "ambiguous"
    assert "unit_unclear" in facts["reason"]


def test_overdue_condition_is_not_payment_term():
    facts = analysis.extract_payment_term(
        [_clause("第八条 逾期付款超过30天的，甲方有权解除合同。")]
    )
    assert facts == {"status": "not_found", "reason": "no_duration"}


def test_cross_phrasing_30_vs_60_is_modified_not_removed():
    """一侧前置、一侧后置语序：真实期限变化必须报 modified，不得报 removed。"""
    base = analysis.extract_payment_term([_clause("第八条 付款期限为30天。", clause_no=[8])])
    revised = analysis.extract_payment_term(
        [_clause("第八条 收到发票后60天内支付货款。", clause_no=[8])]
    )
    assert base["status"] == "identified" and revised["status"] == "identified"
    assert analysis.align_item("payment_term", base, revised) == "modified"

    comma = analysis.extract_payment_term(
        [_clause("第八条 收到发票后60天内，甲方支付货款。", clause_no=[8])]
    )
    assert comma["status"] == "identified"
    assert analysis.align_item("payment_term", base, comma) == "modified"

    both_base = analysis.extract_payment_term(
        [_clause("第八条 收到发票后30天内支付货款。", clause_no=[8])]
    )
    both_revised = analysis.extract_payment_term(
        [_clause("第八条 收到发票后60天内支付货款。", clause_no=[8])]
    )
    assert analysis.align_item("payment_term", both_base, both_revised) == "modified"


def test_negation_and_subject_change_is_modified_not_unchanged():
    """事实词元相同但文本变化（否定/主体/限定语）必须报 modified（评审 F2）。"""
    base = analysis.extract_acceptance_invoice([_clause("第八条 经甲方验收合格后支付价款。")])
    negated = analysis.extract_acceptance_invoice(
        [_clause("第八条 无需甲方验收合格即可支付价款。")]
    )
    assert base["terms"] == negated["terms"] == ["验收合格"]
    assert analysis.align_item("acceptance_invoice", base, negated) == "modified"

    base_start = analysis.extract_start_condition(
        [_clause("第八条 付款自收到发票并经甲方书面确认后起算。")]
    )
    subject = analysis.extract_start_condition(
        [_clause("第八条 付款自收到发票并经乙方口头通知后起算。")]
    )
    assert base_start["events"] == subject["events"] == ["收到发票"]
    assert analysis.align_item("start_condition", base_start, subject) == "modified"


def test_page_noise_inside_duration_still_unchanged():
    """页码噪声夹在期限数字中：归一化后仍应 unchanged，而不是 removed（评审 F3）。"""
    base = analysis.extract_payment_term([_clause("第八条 付款期限为30天。")])
    noisy = analysis.extract_payment_term([_clause("第八条 付款期限为30第 1 页 共 2 页天。")])
    assert noisy["status"] == "identified"
    assert (noisy["value"], noisy["unit"]) == (30, "day")
    assert analysis.align_item("payment_term", base, noisy) == "unchanged"


def test_cross_refs_extracted_as_exception_facts():
    facts = analysis.extract_exception(
        [_clause("第十条 付款争议见第12条处理，暂缓履行需另行约定。")]
    )
    assert "见第12条" in facts["cross_refs"]
    assert "暂缓" in facts["terms"]


def test_truncated_scan_never_claims_unchanged():
    base = analysis.extract_payment_term([_clause("第八条 付款期限为30天。")])
    revised = analysis.extract_payment_term([_clause("第八条 付款期限为30天。")])
    assert analysis.align_item("payment_term", base, revised) == "unchanged"
    assert analysis.align_item("payment_term", base, revised, scan_truncated=True) == "unidentified"


def test_clause_order_only_is_unchanged():
    """条款先后顺序变化不产生事实差异（集合语义；moved 不产出）。"""
    base = analysis.extract_acceptance_invoice(
        [
            _clause("第九条 验收合格后支付价款。", clause_no=[9]),
            _clause("第十条 需提供发票。", clause_no=[10]),
        ]
    )
    revised = analysis.extract_acceptance_invoice(
        [
            _clause("第十条 需提供发票。", clause_no=[10]),
            _clause("第九条 验收合格后支付价款。", clause_no=[9]),
        ]
    )
    assert analysis.align_item("acceptance_invoice", base, revised) == "unchanged"


def test_cross_ref_change_is_modified():
    """交叉引用变化（详见第12条→第15条）不得报 unchanged（最终评审 F1）。"""
    base = analysis.extract_exception(
        [
            _clause("第十条 不可抗力暂缓履行。", clause_no=[10]),
            _clause("第十一条 未尽事宜详见第12条。", clause_no=[11]),
        ]
    )
    revised = analysis.extract_exception(
        [
            _clause("第十条 不可抗力暂缓履行。", clause_no=[10]),
            _clause("第十一条 未尽事宜详见第15条。", clause_no=[11]),
        ]
    )
    assert base["cross_refs"] == ["详见第12条"]
    assert revised["cross_refs"] == ["详见第15条"]
    assert analysis.align_item("exception", base, revised) == "modified"


def test_synonym_units_do_not_make_ambiguous():
    """30 个自然日 / 30 天 是同一事实，重复表述不得降级 ambiguous。"""
    facts = analysis.extract_payment_term(
        [_clause("第八条 付款期限为30个自然日，自收到发票之日起30天内支付货款。")]
    )
    assert facts["status"] == "identified"
    assert (facts["value"], facts["unit"]) == (30, "day")


def test_ambiguous_side_never_reported_as_added_or_removed():
    ambiguous = analysis.extract_payment_term([_clause("第八条 付款期限为30日内。")])
    missing = {"status": "not_found", "reason": "no_duration"}
    assert analysis.align_item("payment_term", ambiguous, missing) == "unidentified"
    assert analysis.align_item("payment_term", missing, ambiguous) == "unidentified"


def test_compound_overdue_phrases_are_not_payment_terms():
    """逾期支付货款 / 超过 N 天未付款：复合语境不得被近义付款词带回。"""
    paid_late = analysis.extract_payment_term(
        [_clause("第八条 甲方逾期支付货款超过30天，乙方有权解除合同。")]
    )
    assert paid_late == {"status": "not_found", "reason": "no_duration"}

    unpaid = analysis.extract_payment_term(
        [_clause("第八条 甲方超过30天未付款的，乙方有权解除合同。")]
    )
    assert unpaid == {"status": "not_found", "reason": "no_duration"}


@pytest.mark.xfail(reason="v1 边界：付款后 N 天内交货的复合语序未区分方向", strict=True)
def test_delivery_after_payment_is_boundary():
    facts = analysis.extract_payment_term([_clause("第八条 甲方付款后30天内，乙方交货。")])
    assert facts == {"status": "not_found", "reason": "no_duration"}


@pytest.mark.xfail(reason="v1 边界：N 天内交货 + 下一分句付款", strict=True)
def test_delivery_first_with_payment_next_is_boundary():
    facts = analysis.extract_payment_term([_clause("第八条 甲方应在30天内交货，乙方付款。")])
    assert facts == {"status": "not_found", "reason": "no_duration"}


def test_guarantee_period_same_sentence_is_conservatively_ambiguous():
    """宽限/质保金期限同句：v1 保守降级 ambiguous（已登记边界，不产出错误值）。"""
    facts = analysis.extract_payment_term(
        [_clause("第八条 付款期限为30天，质保金在到期后60天内支付。")]
    )
    assert facts["status"] == "ambiguous"
    assert "multiple_durations" in facts["reason"]


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


async def _seed_case(factory, *, revised_text=None, legacy_revised=False, suffix=""):
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
                _chunk(
                    revised_id,
                    0,
                    revised_text or "第九条 付款期限为60天，对账无异议并收到发票之日起算。",
                ),
                _chunk(revised_id, 1, "第十条 验收合格后支付价款。"),
            ]
        )
        if legacy_revised:
            db.add(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=revised_id,
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
            "policy": await db.get(Document, policy_id),
            "base": await db.get(Document, base_id),
            "revised": await db.get(Document, revised_id),
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
    assert items["exception"]["facts"]["base"] == {
        "status": "not_found",
        "reason": "no_terms",
        "cross_refs": [],
    }

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


@pytest.mark.asyncio
async def test_analyze_snapshots_evidence_missing_at_creation(factory):
    """制度侧「另有约定」不在建案关键词里：analyze 必须补 candidate 快照。"""
    case = await _seed_case(factory)
    async with factory() as db:
        before = (
            await db.execute(
                select(func.count(ReviewEvidence.id)).where(
                    ReviewEvidence.case_id == case.id, ReviewEvidence.side == "policy"
                )
            )
        ).scalar()
        detail = await analysis.analyze_case(db, case)
        after = (
            await db.execute(
                select(func.count(ReviewEvidence.id)).where(
                    ReviewEvidence.case_id == case.id, ReviewEvidence.side == "policy"
                )
            )
        ).scalar()
    exception_item = next(i for i in detail["items"] if i["item_key"] == "exception")
    policy_rows = exception_item["evidence"]["policy"]
    assert policy_rows, "分析应把制度侧「另有约定」主条款快照成候选证据"
    assert "另有约定" in policy_rows[0]["quote"]
    assert after > before


@pytest.mark.asyncio
async def test_analyze_keeps_manual_selection(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        items = (
            (await db.execute(select(ReviewItem).where(ReviewItem.case_id == case.id)))
            .scalars()
            .all()
        )
        payment = next(i for i in items if i.item_key == "payment_term")
        candidate = (
            (
                await db.execute(
                    select(ReviewEvidence).where(
                        ReviewEvidence.item_id == payment.id, ReviewEvidence.side == "base"
                    )
                )
            )
            .scalars()
            .first()
        )
        row = await case_service.select_evidence(
            db,
            case,
            payment,
            side="base",
            chunk_id=candidate.chunk_id_at_capture,
            role="main",
            selected=True,
        )
        await analysis.analyze_case(db, case)
        refreshed = await db.get(ReviewEvidence, row.id)
    assert refreshed.selected is True
    assert refreshed.role == "main"


@pytest.mark.asyncio
async def test_analyze_not_indexed_or_empty_documents_fail_closed(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        doc = await db.get(Document, REVISED)
        doc.status = DocumentStatus.PARSING
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await analysis.analyze_case(db, case)
    assert excinfo.value.error_code == "REVIEW_DOCUMENT_NOT_READY"

    case2 = await _seed_case(factory, suffix="-2")
    async with factory() as db:
        await db.execute(
            DocumentChunk.__table__.delete().where(DocumentChunk.document_id == f"{POLICY}-2")
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo2:
            await analysis.analyze_case(db, case2)
    assert excinfo2.value.detail["reason"] == "no_chunks"


@pytest.mark.asyncio
async def test_analyze_missing_document_and_mixed_legacy_fail_closed(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        await db.execute(DocumentChunk.__table__.delete().where(DocumentChunk.document_id == BASE))
        await db.execute(
            ReviewDocumentMeta.__table__.delete().where(ReviewDocumentMeta.document_id == BASE)
        )
        await db.execute(Document.__table__.delete().where(Document.id == BASE))
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await analysis.analyze_case(db, case)
    assert excinfo.value.error_code == "REVIEW_DOCUMENT_UNAVAILABLE"

    case2 = await _seed_case(factory, suffix="-2")
    async with factory() as db:
        db.add(
            DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=f"{REVISED}-2",
                chunk_index=9,
                chunk_text="旧文",
                original_text=None,
                chunk_length=2,
                page_number=1,
                meta_data={"text_provenance": "legacy_unverified"},
            )
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(SourceProvenanceUnavailableError):
            await analysis.analyze_case(db, case2)


@pytest.mark.asyncio
async def test_analyze_scan_truncation_is_explicit_and_conservative(factory, monkeypatch):
    case = await _seed_case(factory)
    async with factory() as db:
        await db.execute(DocumentChunk.__table__.delete().where(DocumentChunk.document_id == BASE))
        db.add(_chunk(BASE, 0, "第一条 争议由仲裁解决。"))
        db.add(_chunk(BASE, 1, "第八条 付款期限为30天，自收到发票之日起算。"))
        await db.commit()
    monkeypatch.setattr(analysis, "MAX_EVIDENCE_SCAN_CHUNKS", 1)
    async with factory() as db:
        detail = await analysis.analyze_case(db, case)
    payment = next(i for i in detail["items"] if i["item_key"] == "payment_term")
    assert payment["facts"]["scan"]["base"] == {"scanned_chunks": 1, "truncated": True}
    assert payment["change_type"] == "unidentified", "截断时不得宣称无差异/新增/缺失"


@pytest.mark.asyncio
async def test_analyze_allows_in_review_status(factory):
    seed = await _seed_case(factory)
    async with factory() as db:
        case = await db.get(ReviewCase, seed.id)
        case.status = "in_review"
        await db.commit()
        detail = await analysis.analyze_case(db, case)
    assert detail["items"]


@pytest.mark.asyncio
async def test_analyze_missing_meta_fails_closed(factory):
    case = await _seed_case(factory)
    async with factory() as db:
        await db.execute(
            ReviewDocumentMeta.__table__.delete().where(ReviewDocumentMeta.document_id == REVISED)
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await analysis.analyze_case(db, case)
    assert excinfo.value.error_code == "REVIEW_META_MISSING"


@pytest.mark.asyncio
async def test_analyze_autoflush_conflict_maps_409(factory, monkeypatch):
    """并发冲突可能在循环内 SELECT 的 autoflush 阶段抛出，也必须映射 409。"""
    case = await _seed_case(factory)
    original = analysis.ensure_candidate_evidence

    def _duplicate(row):
        return ReviewEvidence(
            id=str(uuid.uuid4()),
            case_id=row.case_id,
            item_id=row.item_id,
            side=row.side,
            role="candidate",
            selected=False,
            document_id=row.document_id,
            doc_role=row.doc_role,
            doc_version_label=row.doc_version_label,
            doc_version_seq=row.doc_version_seq,
            document_sha256=row.document_sha256,
            chunk_id_at_capture=row.chunk_id_at_capture,
            chunk_index=row.chunk_index,
            clause_no=row.clause_no,
            section_no=row.section_no,
            page_number=row.page_number,
            char_start=row.char_start,
            char_end=row.char_end,
            quote_text=row.quote_text,
            quote_sha256=row.quote_sha256,
            text_provenance=row.text_provenance,
        )

    async def _conflicting(session, case_, item, **kwargs):
        row = await original(session, case_, item, **kwargs)
        session.add(_duplicate(row))
        return row

    monkeypatch.setattr(analysis, "ensure_candidate_evidence", _conflicting)
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await analysis.analyze_case(db, case)
    assert excinfo.value.error_code == "REVIEW_CASE_ANALYSIS_CONFLICT"
