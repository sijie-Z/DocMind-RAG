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
    assert chinese["status"] == "identified"
    assert (chinese["value"], chinese["unit"]) == (6, "month")

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


def test_negative_payment_deadline_is_identified():
    """「N 天内未付款」是在定义付款截止期，不是逾期阈值（最终评审 R2 R1）。"""
    a = analysis.extract_payment_term([_clause("第八条 甲方收到发票后30天内未付款的，视为逾期。")])
    assert a["status"] == "identified"
    assert (a["value"], a["unit"]) == (30, "day")

    b = analysis.extract_payment_term(
        [_clause("第八条 如甲方在收到发票后30天内未付款，乙方有权暂停供货。")]
    )
    assert b["status"] == "identified"

    c = analysis.extract_payment_term(
        [_clause("第八条 甲方应在收到发票后30个工作日内未付款的，视为逾期。")]
    )
    assert (c["value"], c["unit"]) == (30, "workday")


def test_threshold_overdue_variants_are_not_payment_terms():
    """超过/延迟/逾期紧邻或等距的违约阈值不得当作账期。"""
    over = analysis.extract_payment_term(
        [_clause("第八条 甲方超过30天未支付货款的，乙方可解除合同。")]
    )
    assert over == {"status": "not_found", "reason": "no_duration"}

    delayed = analysis.extract_payment_term(
        [_clause("第八条 甲方延迟付款超过30天的，乙方可解除合同。")]
    )
    assert delayed == {"status": "not_found", "reason": "no_duration"}

    overdue_tie = analysis.extract_payment_term(
        [_clause("第八条 甲方逾期30天支付货款的，乙方可解除合同。")]
    )
    assert overdue_tie == {"status": "not_found", "reason": "no_duration"}


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


def test_delivery_after_payment_is_boundary():
    facts = analysis.extract_payment_term([_clause("第八条 甲方付款后30天内，乙方交货。")])
    assert facts == {"status": "not_found", "reason": "no_duration"}


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


# ── D6：按项屏蔽文本哈希 + ambiguous 候选值比较 ─────────────────────────────


def _contract_clauses(payment: str, acceptance: str, extra: str | None = None):
    clauses = [
        _clause(f"第三条 付款期限：{payment}", clause_no=[3]),
        _clause(f"第四条 验收：{acceptance}", clause_no=[4]),
    ]
    if extra:
        clauses.append(_clause(f"第九条 例外：{extra}", clause_no=[9]))
    return clauses


_ACC_TEXT = "乙方交付后10个工作日内完成验收。"


def test_duration_change_does_not_flip_start_or_acceptance():
    """付款天数 30→60 只属于 payment_term；起算/验收不得被整句哈希污染。"""
    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = _contract_clauses("甲方应在验收合格后60天支付货款。", _ACC_TEXT)
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "modified"
    )
    assert (
        analysis.align_item(
            "start_condition",
            analysis.extract_start_condition(base),
            analysis.extract_start_condition(revised),
        )
        == "unchanged"
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "unchanged"
    )


def test_start_trigger_change_does_not_flip_payment():
    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = _contract_clauses("甲方应在收到发票后30天支付货款。", _ACC_TEXT)
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )
    assert (
        analysis.align_item(
            "start_condition",
            analysis.extract_start_condition(base),
            analysis.extract_start_condition(revised),
        )
        == "modified"
    )


def test_acceptance_term_change_does_not_flip_payment():
    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = _contract_clauses("甲方应在验收合格并对账无误后30天支付货款。", _ACC_TEXT)
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_exception_duration_does_not_flip_payment():
    """例外条款里的“顺延30天”属于 exception，不是付款基线变化。"""
    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = _contract_clauses(
        "甲方应在验收合格后30天支付货款。",
        _ACC_TEXT,
        extra="因不可抗力导致验收延迟的，付款期限相应顺延30天。",
    )
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )
    assert (
        analysis.align_item(
            "exception",
            analysis.extract_exception(base),
            analysis.extract_exception(revised),
        )
        == "added"
    )


def test_acceptance_own_duration_change_is_still_modified():
    """验收条款自己的期限（10→15 个工作日）必须仍然算 modified。"""
    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = _contract_clauses(
        "甲方应在验收合格后30天支付货款。",
        "乙方交付后15个工作日内完成验收。",
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )


def test_ambiguous_candidates_compare_values():
    """单位不明但数值可确定：30日→60日 modified；30日→30日 unchanged。"""
    changed_base = _contract_clauses("甲方应在验收合格后30日内支付货款。", _ACC_TEXT)
    changed_rev = _contract_clauses("甲方应在验收合格后60日内支付货款。", _ACC_TEXT)
    same_rev = _contract_clauses("甲方应在验收合格后30日内支付货款。", _ACC_TEXT)

    base_facts = analysis.extract_payment_term(changed_base)
    assert base_facts["status"] == "ambiguous"
    assert (
        analysis.align_item("payment_term", base_facts, analysis.extract_payment_term(changed_rev))
        == "modified"
    )
    assert (
        analysis.align_item("payment_term", base_facts, analysis.extract_payment_term(same_rev))
        == "unchanged"
    )


def test_payment_subject_and_negation_still_modified():
    """屏蔽噪音不得吞掉主体/否定这类真实文本变化。"""
    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    subject = _contract_clauses("乙方应在验收合格后30天支付货款。", _ACC_TEXT)
    negated = _contract_clauses("甲方应在验收不合格后30天支付货款。", _ACC_TEXT)
    for revised in (subject, negated):
        assert (
            analysis.align_item(
                "payment_term",
                analysis.extract_payment_term(base),
                analysis.extract_payment_term(revised),
            )
            == "modified"
        )


# ── D6 R1 评审回归（更精确的片段级屏蔽）────────────────────────────────────


def test_exception_duration_change_is_modified():
    """例外条款自己的顺延期限 30→15 必须报 exception modified；付款不变。"""
    base = _contract_clauses(
        "甲方应在验收合格后30天支付货款。",
        _ACC_TEXT,
        extra="因不可抗力导致验收延迟的，付款期限相应顺延30天。",
    )
    revised = _contract_clauses(
        "甲方应在验收合格后30天支付货款。",
        _ACC_TEXT,
        extra="因不可抗力导致验收延迟的，付款期限相应顺延15天。",
    )
    assert (
        analysis.align_item(
            "exception", analysis.extract_exception(base), analysis.extract_exception(revised)
        )
        == "modified"
    )
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )


def test_payment_clause_with_exception_word_keeps_subject_and_negation():
    """付款条款正文含「除外」不得让整条失去主体/否定保护。"""
    base = _contract_clauses("甲方应在验收合格后30天支付货款，双方另有约定的除外。", _ACC_TEXT)
    subject = _contract_clauses("乙方应在验收合格后30天支付货款，双方另有约定的除外。", _ACC_TEXT)
    negated = _contract_clauses("甲方应在验收不合格后30天支付货款，双方另有约定的除外。", _ACC_TEXT)
    for revised in (subject, negated):
        assert (
            analysis.align_item(
                "payment_term",
                analysis.extract_payment_term(base),
                analysis.extract_payment_term(revised),
            )
            == "modified"
        )


def test_shared_sentence_keeps_acceptance_duration():
    """付款与验收同句（逗号分隔）时，验收自己的 10→15 个工作日仍须 modified。"""
    base = [
        _clause(
            "第三条 付款与验收：乙方应在10个工作日内完成验收，甲方应在验收合格后30天内付款。",
            clause_no=[3],
        )
    ]
    revised = [
        _clause(
            "第三条 付款与验收：乙方应在15个工作日内完成验收，甲方应在验收合格后30天内付款。",
            clause_no=[3],
        )
    ]
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_mixed_unit_candidates_do_not_crash():
    """候选值相同但 unit 混合 None/str（30天和30日）不得抛 TypeError。"""
    clauses = _contract_clauses("甲方应在验收合格后30天和30日内支付货款。", _ACC_TEXT)
    facts = analysis.extract_payment_term(clauses)
    assert facts["status"] == "ambiguous"
    assert analysis.align_item("payment_term", facts, facts) == "unchanged"


def test_truncated_scan_with_equal_ambiguous_candidates_is_unidentified():
    """截断扫描时不得宣称 ambiguous 候选相同即 unchanged。"""
    clauses = _contract_clauses("甲方应在验收合格后30日内支付货款。", _ACC_TEXT)
    facts = analysis.extract_payment_term(clauses)
    assert facts["status"] == "ambiguous"
    assert analysis.align_item("payment_term", facts, facts, scan_truncated=True) == "unidentified"


def test_payment_clause_without_title_colon_not_misclassified():
    """无标题冒号的付款条款（正文含「除外」）不得被整条误判为例外条款。"""
    base = [_clause("甲方应在验收合格后30天支付货款，双方另有约定的除外。", clause_no=[3])]
    subject = [_clause("乙方应在验收合格后30天支付货款，双方另有约定的除外。", clause_no=[3])]
    base_facts = analysis.extract_payment_term(base)
    assert base_facts["status"] == "identified"
    assert (
        analysis.align_item("payment_term", base_facts, analysis.extract_payment_term(subject))
        == "modified"
    )


def test_payment_clause_without_colon_and_exception_prefix():
    """无标题冒号、正文以「除不可抗力外」开头的付款条款不得被整条剔除。"""
    base = [_clause("除不可抗力外，甲方应在验收合格后30天支付货款。", clause_no=[3])]
    subject = [_clause("除不可抗力外，乙方应在验收合格后30天支付货款。", clause_no=[3])]
    base_facts = analysis.extract_payment_term(base)
    assert base_facts["status"] == "identified"
    assert (
        analysis.align_item("payment_term", base_facts, analysis.extract_payment_term(subject))
        == "modified"
    )


def test_payment_heading_with_exception_word_not_misclassified():
    """标题含「不可抗力」但主题是付款（含付款词）时不得判为例外条款。"""
    base = [_clause("第三条 不可抗力情形下的付款：甲方应在验收合格后30天支付货款。", clause_no=[3])]
    subject = [
        _clause("第三条 不可抗力情形下的付款：乙方应在验收合格后30天支付货款。", clause_no=[3])
    ]
    base_facts = analysis.extract_payment_term(base)
    assert base_facts["status"] == "identified"
    assert (
        analysis.align_item("payment_term", base_facts, analysis.extract_payment_term(subject))
        == "modified"
    )


def test_shared_fragment_without_comma_keeps_acceptance_duration():
    """同一片段（无逗号）里「10个工作日内完成对账并在30天内支付」：
    验收的 10→15 必须 modified（锚点归属：10 最近验收/对账，30 最近支付）。"""
    base = [
        _clause(
            "第三条 付款：甲方应在验收合格后10个工作日内完成对账并在30天内支付货款。",
            clause_no=[3],
        )
    ]
    revised = [
        _clause(
            "第三条 付款：甲方应在验收合格后15个工作日内完成对账并在30天内支付货款。",
            clause_no=[3],
        )
    ]
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_payment_verb_after_filler_words_still_shields_duration():
    """付款动词隔几个字（内向乙方/内完成/内安排）时，期限仍归属付款片段。"""
    cases = [
        "甲方应在验收合格后30个自然日内向乙方支付货款。",
        "甲方应在验收合格后30个工作日内完成付款。",
        "甲方应在验收合格后30天内安排付款。",
    ]
    acceptance = "乙方交付后10个工作日内完成验收。"
    for payment_text in cases:
        base = _contract_clauses(payment_text, acceptance)
        revised = _contract_clauses(payment_text.replace("30", "60", 1), acceptance)
        assert (
            analysis.align_item(
                "payment_term",
                analysis.extract_payment_term(base),
                analysis.extract_payment_term(revised),
            )
            == "modified"
        ), payment_text
        assert (
            analysis.align_item(
                "start_condition",
                analysis.extract_start_condition(base),
                analysis.extract_start_condition(revised),
            )
            == "unchanged"
        ), payment_text
        assert (
            analysis.align_item(
                "acceptance_invoice",
                analysis.extract_acceptance_invoice(base),
                analysis.extract_acceptance_invoice(revised),
            )
            == "unchanged"
        ), payment_text


# ── D7：主证据定位器按核验项选择条款 ─────────────────────────────────────


def test_acceptance_primary_locator_prefers_acceptance_heading():
    """验收证据主定位器应选「第四条 验收：…」，而不是先命中术语的付款条款。"""
    clauses = [
        _clause("第三条 付款期限：甲方应在验收合格后30天支付货款。", clause_no=[3]),
        _clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4]),
    ]
    facts = analysis.extract_acceptance_invoice(clauses)
    assert facts["clause_no"] == [4]
    assert facts["clause_no_union"] == [3, 4]


def test_exception_primary_locator_prefers_exception_heading():
    """例外主证据应选「第九条 例外：…」，而不是前文顺便提到不可抗力的条款。"""
    clauses = [
        _clause("第二条 定义：不可抗力指不能预见、不能避免的客观情况。", clause_no=[2]),
        _clause("第九条 例外：双方可另行约定顺延情形。", clause_no=[9]),
    ]
    facts = analysis.extract_exception(clauses)
    assert facts["clause_no"] == [9]
    assert 2 in (facts["clause_no_union"] or [])


def test_invoice_reconciliation_change_points_primary_to_payment_clause():
    """发票/对账在付款条款新增时，差异感知主证据指向付款条款（变化处）。"""
    base = [
        _clause("第三条 付款期限：甲方应在验收合格后30天支付货款。", clause_no=[3]),
        _clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4]),
    ]
    invoice = [
        _clause("第三条 付款期限：甲方应在收到发票后30天支付货款。", clause_no=[3]),
        _clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4]),
    ]
    recon = [
        _clause("第三条 付款期限：甲方应在验收合格并对账无误后30天支付货款。", clause_no=[3]),
        _clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4]),
    ]
    base_facts = analysis.extract_acceptance_invoice(base)
    for revised in (invoice, recon):
        revised_facts = analysis.extract_acceptance_invoice(revised)
        assert analysis.align_item("acceptance_invoice", base_facts, revised_facts) == "modified"
        analysis.refine_primary_locator(
            "acceptance_invoice",
            base_facts,
            revised_facts,
            "modified",
            base_clauses=base,
            revised_clauses=revised,
        )
        assert revised_facts["clause_no"] == [3]


def test_change_aware_primary_fixes_acceptance_first_render():
    """验收条款在前、付款条款含发票时，验收期限变化的主证据必须指回验收条款。"""
    base = [
        _clause("第三条 验收：乙方交付后10个工作日内完成验收。", clause_no=[3]),
        _clause("第四条 付款期限：甲方应在收到发票后30天支付货款。", clause_no=[4]),
    ]
    revised = [
        _clause("第三条 验收：乙方交付后15个工作日内完成验收。", clause_no=[3]),
        _clause("第四条 付款期限：甲方应在收到发票后30天支付货款。", clause_no=[4]),
    ]
    base_facts = analysis.extract_acceptance_invoice(base)
    revised_facts = analysis.extract_acceptance_invoice(revised)
    assert analysis.align_item("acceptance_invoice", base_facts, revised_facts) == "modified"
    analysis.refine_primary_locator(
        "acceptance_invoice",
        base_facts,
        revised_facts,
        "modified",
        base_clauses=base,
        revised_clauses=revised,
    )
    assert revised_facts["clause_no"] == [3]


def test_mixed_heading_primary_is_order_independent():
    """混合标题「验收与付款」条款必须被选为主证据，且不依赖条款顺序/全局去重。"""
    mixed_text = "验收与付款：验收合格后凭发票付款，并对账无误后结算。"
    variants = [
        [
            _clause("第三条 付款期限：甲方应在验收合格后30天支付货款。", clause_no=[3]),
            _clause(f"第五条 {mixed_text}", clause_no=[5]),
        ],
        [
            _clause(f"第三条 {mixed_text}", clause_no=[3]),
            _clause("第五条 付款期限：甲方应在验收合格后30天支付货款。", clause_no=[5]),
        ],
    ]
    for clauses in variants:
        facts = analysis.extract_acceptance_invoice(clauses)
        mixed_no = [c.clause_no for c in clauses if "验收与付款" in c.text][0]
        assert facts["clause_no"] == mixed_no
        assert {3, 5} <= set(facts["clause_no_union"])


def test_shared_term_union_keeps_acceptance_clause():
    """付款条款写「验收后」时，独立验收条款仍须出现在证据 union 中。"""
    clauses = [
        _clause("第三条 付款期限：甲方应在验收后30天支付货款。", clause_no=[3]),
        _clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4]),
    ]
    facts = analysis.extract_acceptance_invoice(clauses)
    assert facts["clause_no"] == [4]
    assert {3, 4} <= set(facts["clause_no_union"])


def test_refine_primary_locator_keeps_static_when_no_clause_numbers():
    """section 编号/无第N条文档：refine 必须保留静态主证据，不能乱移。"""
    base = [
        _clause("2.1 总则：双方应保持书面沟通。"),
        _clause("2.3 验收：乙方交付后10个工作日内完成验收。"),
    ]
    revised = [
        _clause("2.1 总则：双方应每日书面沟通。"),
        _clause("2.3 验收：乙方交付后15个工作日内完成验收。"),
    ]
    base_facts = analysis.extract_acceptance_invoice(base)
    revised_facts = analysis.extract_acceptance_invoice(revised)
    static_chunk = revised_facts["chunk_id"]
    assert analysis.align_item("acceptance_invoice", base_facts, revised_facts) == "modified"
    analysis.refine_primary_locator(
        "acceptance_invoice",
        base_facts,
        revised_facts,
        "modified",
        base_clauses=base,
        revised_clauses=revised,
    )
    assert revised_facts["chunk_id"] == static_chunk


def test_cross_ref_only_renumber_is_detected():
    """例外项的交叉引用条款只改编号（10→11）必须报 renumbered，而不是 unchanged。"""
    base = [
        _clause("第九条 不可抗力暂缓履行。", clause_no=[9]),
        _clause("第十条 未尽事宜详见第12条。", clause_no=[10]),
    ]
    revised = [
        _clause("第九条 不可抗力暂缓履行。", clause_no=[9]),
        _clause("第十一条 未尽事宜详见第12条。", clause_no=[11]),
    ]
    assert (
        analysis.align_item(
            "exception",
            analysis.extract_exception(base),
            analysis.extract_exception(revised),
        )
        == "renumbered"
    )


# ── D8：中文数字期限 / 共享术语第二条款 / 无冒号例外条款 ──────────────────


def test_cn_numeral_durations_extracted():
    workday = analysis.extract_payment_term(
        [_clause("第三条 付款期限：甲方应在验收合格后三十个工作日内支付货款。", clause_no=[3])]
    )
    assert workday["status"] == "identified"
    assert (workday["value"], workday["unit"]) == (30, "workday")

    bare = analysis.extract_payment_term(
        [_clause("第三条 付款期限：甲方应在验收合格后六十日内支付货款。", clause_no=[3])]
    )
    assert bare["status"] == "ambiguous"
    assert bare["candidates"][0]["value"] == 60

    hundred = analysis.extract_payment_term(
        [_clause("第三条 付款期限：甲方应在验收合格后一百零五天内支付货款。", clause_no=[3])]
    )
    assert hundred["status"] == "identified"
    assert (hundred["value"], hundred["unit"]) == (105, "day")


def test_cn_numeral_date_and_ordinal_are_not_durations():
    date_like = analysis.extract_payment_term(
        [_clause("第三条 付款期限：付款日为三月十五日，甲方应支付货款。", clause_no=[3])]
    )
    assert date_like["status"] == "not_found"
    ordinal = analysis.extract_payment_term(
        [_clause("第三条 付款期限：甲方应在第三个工作日支付货款。", clause_no=[3])]
    )
    assert ordinal["status"] == "not_found"


def test_cn_numeral_duration_change_does_not_flip_other_items():
    base = _contract_clauses("甲方应在验收合格后三十个工作日内支付货款。", _ACC_TEXT)
    revised = _contract_clauses("甲方应在验收合格后六十个工作日内支付货款。", _ACC_TEXT)
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "modified"
    )
    assert (
        analysis.align_item(
            "start_condition",
            analysis.extract_start_condition(base),
            analysis.extract_start_condition(revised),
        )
        == "unchanged"
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "unchanged"
    )


def test_shared_term_second_clause_change_is_detected():
    """付款条款写「验收后」时，独立验收条款自身的期限变化必须 modified（S1）。"""
    base = _contract_clauses("甲方应在验收后30天支付货款。", _ACC_TEXT)
    revised = _contract_clauses(
        "甲方应在验收后30天支付货款。",
        "乙方交付后15个工作日内完成验收。",
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_acceptance_second_clause_added_is_modified():
    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = base + [_clause("第九条 验收：甲方书面确认后方视为验收完成。", clause_no=[9])]
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_no_colon_exception_article_detected_but_payment_heading_not():
    exception_like = _clause(
        "第九条 例外 因不可抗力导致验收延迟的，付款期限相应顺延30天。", clause_no=[9]
    )
    assert analysis._is_exception_article(exception_like) is True
    payment_heading = _clause(
        "第三条 不可抗力情形下的付款：甲方应在验收合格后30天支付货款。", clause_no=[3]
    )
    assert analysis._is_exception_article(payment_heading) is False

    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = base + [
        _clause("第九条 例外 因不可抗力导致验收延迟的，付款期限相应顺延30天。", clause_no=[9])
    ]
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )


def test_cn_numeral_compounds_are_not_durations():
    """同一日/唯一日期/统一日 等复合词里的「一日」不得抽成期限。"""
    texts = (
        "第三条 付款期限：甲方应自验收合格同一日起30天内支付货款。",
        "第三条 付款期限：付款以发票开具的唯一日期为准，甲方应在验收合格后30天内支付货款。",
        "第三条 付款期限：双方于合同生效统一日确认，甲方应在验收合格后30天内支付货款。",
    )
    for text in texts:
        facts = analysis.extract_payment_term([_clause(text, clause_no=[3])])
        assert facts["status"] == "identified", text
        assert (facts["value"], facts["unit"]) == (30, "day"), text


def test_cn_to_int_edge_cases():
    assert analysis._cn_to_int("十") == 10
    assert analysis._cn_to_int("十五") == 15
    assert analysis._cn_to_int("二十三") == 23
    assert analysis._cn_to_int("一百零五") == 105


def test_no_colon_exception_with_payment_in_first_segment():
    """无冒号例外条款即便首段含「付款」，也应按例外条款处理（B2）。"""
    exception_clause = _clause(
        "第九条 例外 因不可抗力导致付款延迟的，付款期限相应顺延30天。", clause_no=[9]
    )
    assert analysis._is_exception_article(exception_clause) is True

    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = base + [exception_clause]
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )


def test_no_colon_exception_payment_heading_not_exception():
    """「例外/不可抗力情形下的付款」是付款条款，不是独立例外条款。"""
    clause = _clause("第三条 不可抗力情形下的付款 甲方应在验收合格后30天支付货款。", clause_no=[3])
    assert analysis._is_exception_article(clause) is False


def test_exception_heading_with_broader_payment_nouns_not_exception():
    """「例外情形下的结算/账期/应付款项/首付款」仍是付款条款（B3）。"""
    texts = (
        "第三条 例外情形下的结算：甲方应在验收合格后30天支付货款。",
        "第三条 不可抗力情形下的账期：甲方应在验收合格后30天支付货款。",
        "第三条 例外情形下的应付款项 甲方应在验收合格后30天支付货款。",
        "第三条 例外情形下的首付款 甲方应在验收合格后30天支付货款。",
    )
    for text in texts:
        clause = _clause(text, clause_no=[3])
        assert analysis._is_exception_article(clause) is False, text
        facts = analysis.extract_payment_term([clause])
        assert facts["status"] == "identified", text
        assert (facts["value"], facts["unit"]) == (30, "day"), text


def test_no_colon_exception_body_prefix_words_not_misclassified():
    """「补充协议未作约定…」「暂缓履行…」开头的付款条款不得被当例外条款（F1）。"""
    texts = (
        "第八条 补充协议未作约定的，甲方应在验收合格后30天内支付货款。",
        "第八条 暂缓履行需另行约定的，甲方应在验收合格后30天内支付货款。",
        "第八条 另行约定其他事项的，甲方应在验收合格后30天内支付货款。",
    )
    for text in texts:
        clause = _clause(text, clause_no=[8])
        assert analysis._is_exception_article(clause) is False, text
        facts = analysis.extract_payment_term([clause])
        assert facts["status"] == "identified", text
        assert (facts["value"], facts["unit"]) == (30, "day"), text


def test_cn_approximation_and_thousand_not_durations():
    """「一千二百个工作日」「数十日」不得静默截取值（F2）。"""
    texts = (
        "第三条 付款期限：甲方应在验收合格后一千二百个工作日支付货款。",
        "第三条 付款期限：甲方应在验收合格后数十日内支付货款。",
    )
    for text in texts:
        facts = analysis.extract_payment_term([_clause(text, clause_no=[3])])
        assert facts["status"] == "not_found", text


def test_no_colon_exception_with_chinese_punctuation_separator():
    """「第九条 例外，/。/；因…」同样是合法标题分隔（B4）。"""
    for sep in ("，", "。", "；", "、"):
        clause = _clause(
            f"第九条 例外{sep}因不可抗力导致付款延迟的，付款期限相应顺延30天。",
            clause_no=[9],
        )
        assert analysis._is_exception_article(clause) is True, sep

    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = base + [
        _clause("第九条 例外，因不可抗力导致付款延迟的，付款期限相应顺延30天。", clause_no=[9])
    ]
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )


def test_exception_heading_payment_nouns_with_separator_pins_both_layers():
    """带分隔的付款主题标题：钉住冒号短路与定语正则两层机制（A 变异 g/j）。"""
    colon = _clause("第三条 例外 情形下的结算：甲方应在验收合格后30天支付货款。", clause_no=[3])
    assert analysis._is_exception_article(colon) is False
    assert analysis.extract_payment_term([colon])["status"] == "identified"

    no_colon = _clause("第三条 例外 应付款项 甲方应在验收合格后30天支付货款。", clause_no=[3])
    assert analysis._is_exception_article(no_colon) is False
    facts = analysis.extract_payment_term([no_colon])
    assert facts["status"] == "identified"
    assert (facts["value"], facts["unit"]) == (30, "day")


def test_no_colon_exception_with_colon_in_body_is_exception():
    """例外词后有分隔、正文后面才出现冒号时仍是独立例外条款（B 的 A 项）。"""
    clause = _clause("第九条 例外 因不可抗力导致付款延迟的：付款期限相应顺延30天。", clause_no=[9])
    assert analysis._is_exception_article(clause) is True

    base = _contract_clauses("甲方应在验收合格后30天支付货款。", _ACC_TEXT)
    revised = base + [clause]
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )


def test_exception_word_with_subject_after_separator_is_body_not_heading():
    """「补充协议，甲方应…」「另行约定，甲方应…」是正文，不是例外条款（B 残留）。"""
    texts = (
        "第八条 补充协议，甲方应在验收合格后30天内支付货款。",
        "第八条 另行约定，甲方应在验收合格后30天内支付货款。",
    )
    for text in texts:
        clause = _clause(text, clause_no=[8])
        assert analysis._is_exception_article(clause) is False, text
        facts = analysis.extract_payment_term([clause])
        assert facts["status"] == "identified", text
        assert (facts["value"], facts["unit"]) == (30, "day"), text


def test_exception_payment_heading_with_connector_is_not_exception():
    """「例外 及结算：…」这类付款主题标题仍是付款条款（A 变异 g）。"""
    clause = _clause("第三条 例外 及结算：甲方应在验收合格后30天支付货款。", clause_no=[3])
    assert analysis._is_exception_article(clause) is False
    facts = analysis.extract_payment_term([clause])
    assert facts["status"] == "identified"
    assert (facts["value"], facts["unit"]) == (30, "day")


def test_exception_body_subject_list_covers_common_roles():
    """卖方/买方/供应商/承包人 等主语出现在分隔后时同样判正文（A 残留）。"""
    for role in ("卖方", "买方", "供应商", "承包人", "任何一方", "丙方"):
        text = f"第八条 补充协议，{role}应在验收合格后30天内支付货款。"
        clause = _clause(text, clause_no=[8])
        assert analysis._is_exception_article(clause) is False, text
        facts = analysis.extract_payment_term([clause])
        assert facts["status"] == "identified", text
        assert (facts["value"], facts["unit"]) == (30, "day"), text


# ── D9：中文数字月份 / 连谓归因 / 交付期限 ─────────────────────────────────


def test_cn_month_durations_extracted():
    three = analysis.extract_payment_term(
        [_clause("第三条 付款期限：甲方应在验收合格后三个月内支付货款。", clause_no=[3])]
    )
    assert three["status"] == "identified"
    assert (three["value"], three["unit"]) == (3, "month")

    date_like = analysis.extract_payment_term(
        [_clause("第三条 付款期限：合同于2025年3月签订，甲方应支付货款。", clause_no=[3])]
    )
    assert date_like["status"] == "not_found"
    cn_date = analysis.extract_payment_term(
        [_clause("第三条 付款期限：付款日为三月十五日，甲方应支付货款。", clause_no=[3])]
    )
    assert cn_date["status"] == "not_found"


def test_connected_predicate_payment_duration_attribution():
    """「30天内完成对账并支付」的 30 归付款；改天数不得污染起算/验收。"""
    base = _contract_clauses("甲方应在验收合格后30天内完成对账并支付货款。", _ACC_TEXT)
    revised = _contract_clauses("甲方应在验收合格后60天内完成对账并支付货款。", _ACC_TEXT)
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "modified"
    )
    assert (
        analysis.align_item(
            "start_condition",
            analysis.extract_start_condition(base),
            analysis.extract_start_condition(revised),
        )
        == "unchanged"
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "unchanged"
    )


def test_delivery_duration_is_not_payment_term():
    """交付期限 10→15 个工作日归交付/验收，不被付款锚点吸走。"""
    base = _contract_clauses(
        "甲方应在验收合格后30天支付货款。", "乙方应在10个工作日内交付，甲方随后付款。"
    )
    revised = _contract_clauses(
        "甲方应在验收合格后30天支付货款。", "乙方应在15个工作日内交付，甲方随后付款。"
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )
    payment = analysis.extract_payment_term(base)
    assert payment["status"] == "identified"
    assert (payment["value"], payment["unit"]) == (30, "day")


def test_acceptance_duration_with_payment_in_next_sentence_not_masked():
    """后一句的「付款」不得把前一句的验收期限判成付款并吞掉变化（D9 阻断回归）。"""
    base = [_clause("第四条 验收：乙方应在10个工作日内完成验收。甲方应付款。", clause_no=[4])]
    revised = [_clause("第四条 验收：乙方应在15个工作日内完成验收。甲方应付款。", clause_no=[4])]
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_arabic_month_leading_char_guard_matches_cn():
    """「自第3个月起」与「自第三个月起」口径一致，均不抽为月份期限。"""
    arabic = analysis.extract_payment_term(
        [_clause("第三条 付款期限：甲方应自第3个月起支付货款。", clause_no=[3])]
    )
    chinese = analysis.extract_payment_term(
        [_clause("第三条 付款期限：甲方应自第三个月起支付货款。", clause_no=[3])]
    )
    assert arabic["status"] == "not_found"
    assert chinese["status"] == "not_found"


def test_payment_after_delivery_event_is_still_payment():
    """交货/交付作为触发事件时，期限后的付款动词仍应识别为付款期限（A-1）。"""
    cases = {
        "第三条 付款期限：交货后30日内，甲方支付货款。": ("ambiguous", 30),
        "第三条 付款期限：交付后5个工作日内，甲方支付货款。": ("identified", 5),
        "第三条 付款期限：甲方应在30天内交付货款。": ("identified", 30),
        "第三条 付款期限：乙方发货后30天内甲方付款。": ("identified", 30),
    }
    for text, (status, value) in cases.items():
        facts = analysis.extract_payment_term([_clause(text, clause_no=[3])])
        assert facts["status"] == status, text
        if status == "identified":
            assert facts["value"] == value, text
        else:
            assert facts["candidates"][0]["value"] == value, text


def test_payment_first_delivery_after_is_not_payment():
    """「付款后30天内交货」的 30 是交付期限，不是付款期限。"""
    facts = analysis.extract_payment_term([_clause("第八条 甲方付款后30天内，乙方交货。")])
    assert facts == {"status": "not_found", "reason": "no_duration"}


def test_refine_excludes_exception_candidates_without_moving_static():
    """refine 候选排除独立例外条款：只剩例外候选时保留静态主证据（A-7）。"""
    base_clause = _clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4])
    exception_clause = _clause("第九条 例外 因不可抗力导致验收延迟的，顺延30天。", clause_no=[9])
    base_facts = analysis.extract_acceptance_invoice([base_clause])
    revised_facts = analysis.extract_acceptance_invoice([base_clause, exception_clause])
    # 人工构造 modified 场景，直接验证 refine 的例外候选过滤。
    static_chunk = revised_facts["chunk_id"]
    analysis.refine_primary_locator(
        "acceptance_invoice",
        base_facts,
        revised_facts,
        "modified",
        base_clauses=[base_clause],
        revised_clauses=[base_clause, exception_clause],
    )
    assert revised_facts["chunk_id"] == static_chunk


def test_acceptance_duration_with_payment_in_next_comma_clause_not_masked():
    """逗号分句后的「随后付款」不得回吸前一片段的验收期限（B 残余 blocker）。"""
    base = [_clause("第四条 验收：乙方应在10个工作日内完成验收，甲方随后付款。", clause_no=[4])]
    revised = [_clause("第四条 验收：乙方应在15个工作日内完成验收，甲方随后付款。", clause_no=[4])]
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_comma_conjunctive_payment_continuation_is_masked():
    """逗号后「并/且支付」式谓语延续（无新主语）时，付款期限仍归付款。"""
    variants = (
        "甲方应在验收合格后30天内完成对账，并支付货款。",
        "甲方应在验收合格后30天内，完成对账并支付货款。",
    )
    for text in variants:
        base = _contract_clauses(text, _ACC_TEXT)
        revised = _contract_clauses(text.replace("30", "60", 1), _ACC_TEXT)
        assert (
            analysis.align_item(
                "payment_term",
                analysis.extract_payment_term(base),
                analysis.extract_payment_term(revised),
            )
            == "modified"
        ), text
        assert (
            analysis.align_item(
                "start_condition",
                analysis.extract_start_condition(base),
                analysis.extract_start_condition(revised),
            )
            == "unchanged"
        ), text
        assert (
            analysis.align_item(
                "acceptance_invoice",
                analysis.extract_acceptance_invoice(base),
                analysis.extract_acceptance_invoice(revised),
            )
            == "unchanged"
        ), text


def test_delivery_payment_compound_variants_are_payment():
    """交付首期/剩余/合同约定 货款等词形仍按付款短语处理（A-2 残余）。"""
    texts = (
        "第三条 付款期限：甲方应在30天内向乙方交付首期货款。",
        "第三条 付款期限：甲方应在30天内交付剩余价款。",
        "第三条 付款期限：甲方应在30天内交付合同约定款项。",
    )
    for text in texts:
        facts = analysis.extract_payment_term([_clause(text, clause_no=[3])])
        assert facts["status"] == "identified", text
        assert (facts["value"], facts["unit"]) == (30, "day"), text


def test_comma_payment_without_conjunction_does_not_shield_acceptance():
    """「，随后付款/付款/之后付款/再付款」是新谓语，不得屏蔽验收期限（B 回归）。"""
    for tail in ("随后付款", "付款", "之后付款", "再付款"):
        base = [_clause(f"第四条 验收：乙方应在10个工作日内完成验收，{tail}。", clause_no=[4])]
        revised = [_clause(f"第四条 验收：乙方应在15个工作日内完成验收，{tail}。", clause_no=[4])]
        assert (
            analysis.align_item(
                "acceptance_invoice",
                analysis.extract_acceptance_invoice(base),
                analysis.extract_acceptance_invoice(revised),
            )
            == "modified"
        ), tail


def test_conjunction_with_new_subject_does_not_shield_acceptance():
    """连词后出现新主语时，付款是新主语的谓语，不得回吸验收期限（B 回归）。"""
    for tail in ("并由甲方支付货款", "并约定甲方付款", "并要求甲方付款"):
        base = [_clause(f"第四条 验收：乙方应在10个工作日内完成验收，{tail}。", clause_no=[4])]
        revised = [_clause(f"第四条 验收：乙方应在15个工作日内完成验收，{tail}。", clause_no=[4])]
        assert (
            analysis.align_item(
                "acceptance_invoice",
                analysis.extract_acceptance_invoice(base),
                analysis.extract_acceptance_invoice(revised),
            )
            == "modified"
        ), tail


def test_conjunction_with_notification_verb_does_not_shield_acceptance():
    """连词后接通知/约定类动词时，付款只是被提及，不得回吸验收期限（B 回归）。"""
    for tail in ("并通知甲方付款", "并提醒付款", "并确认付款方式"):
        base = [_clause(f"第四条 验收：乙方应在10个工作日内完成验收，{tail}。", clause_no=[4])]
        revised = [_clause(f"第四条 验收：乙方应在15个工作日内完成验收，{tail}。", clause_no=[4])]
        assert (
            analysis.align_item(
                "acceptance_invoice",
                analysis.extract_acceptance_invoice(base),
                analysis.extract_acceptance_invoice(revised),
            )
            == "modified"
        ), tail


def test_payment_follows_explicit_clause_reference():
    """付款条款显式引用另一条款定义账期：即使目标条款没有付款词也要识别（#1）。"""
    clauses = [
        _clause("第三条 付款期限：甲方以银行转账方式支付，具体账期按第四条执行。", clause_no=[3]),
        _clause("第四条 期限：验收合格后30天内。", clause_no=[4]),
    ]
    facts = analysis.extract_payment_term(clauses)
    assert facts["status"] == "identified"
    assert (facts["value"], facts["unit"]) == (30, "day")
    assert facts["clause_no"] == [4]


def test_payment_reference_ignores_unrelated_pointer():
    """「违约责任详见第九条」不是付款引用，不得把违约期限吸成账期。"""
    clauses = [
        _clause("第三条 付款期限：付款方式为银行转账，违约责任详见第九条。", clause_no=[3]),
        _clause("第九条 违约：逾期超过30天的，甲方有权解除合同。", clause_no=[9]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_payment_reference_skips_exception_target():
    """引用目标是独立例外条款时不按付款定义解析（例外归 exception 项）。"""
    clauses = [
        _clause("第三条 付款期限：具体账期按第九条执行。", clause_no=[3]),
        _clause("第九条 例外：因不可抗力导致付款延迟的，期限顺延30天。", clause_no=[9]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_payment_reference_rejects_non_payment_context_duration():
    """被引用条款里的质保期等非付款期限不得被引用解析吸成账期。"""
    clauses = [
        _clause("第三条 付款期限：具体账期按第四条执行。", clause_no=[3]),
        _clause("第四条 期限：质量保证期为24个月。", clause_no=[4]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_start_condition_ignores_exception_article_events():
    """新增例外条款里的新起算事件是顺延条件，不得让起算条件误报 modified（#3）。"""
    base = [_clause("第三条 付款期限：甲方应在收到发票后30天内支付货款。", clause_no=[3])]
    revised = base + [
        _clause("第九条 例外：因不可抗力导致验收合格延迟的，付款相应顺延。", clause_no=[9])
    ]
    assert (
        analysis.align_item(
            "start_condition",
            analysis.extract_start_condition(base),
            analysis.extract_start_condition(revised),
        )
        == "unchanged"
    )


def test_acceptance_terms_ignore_exception_article_mentions():
    """例外条款里的「验收合格」是顺延条件，不得成为验收项的事实词元。"""
    base = [_clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4])]
    revised = base + [
        _clause("第九条 例外：因不可抗力导致验收合格延迟的，已验收部分相应顺延。", clause_no=[9])
    ]
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "unchanged"
    )


def test_payment_reference_does_not_cross_punctuation():
    """「付款期限：…收到发票后按第四条约定支付」跨冒号/插入语不得算期限引用。"""
    clauses = [
        _clause("第三条 付款期限：甲方应在收到发票后按第四条约定支付货款。", clause_no=[3]),
        _clause("第九条 期限：自签署之日起12个月。", clause_no=[9]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_payment_reference_does_not_cross_comma():
    """期限型短语与引用词之间跨逗号（账期另行约定，详见第九条）不得命中。"""
    clauses = [
        _clause("第三条 付款期限：账期另行约定，详见第九条。", clause_no=[3]),
        _clause("第九条 期限：自签署之日起12个月。", clause_no=[9]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_payment_reference_follows_parallel_targets():
    """同一引用短语的并列目标（第四条、第五条）必须全部跟随，不得只取第一个。"""
    clauses = [
        _clause("第三条 付款期限：付款期限分别按第四条、第五条执行。", clause_no=[3]),
        _clause("第四条 期限：首期款于验收合格后30天内结清。", clause_no=[4]),
        _clause("第五条 期限：尾款于交货后60天内结清。", clause_no=[5]),
    ]
    facts = analysis.extract_payment_term(clauses)
    assert facts["status"] == "ambiguous"
    assert "multiple_durations" in facts["reason"]
    assert {(c["value"], c["unit"]) for c in facts["candidates"]} == {(30, "day"), (60, "day")}
    assert facts["clause_no_union"] == [4, 5]


def test_parallel_reference_target_change_is_modified():
    """并列引用目标之一变更（60→90）必须报 payment modified，不得假 unchanged。"""
    base = [
        _clause("第三条 付款期限：付款期限分别按第四条、第五条执行。", clause_no=[3]),
        _clause("第四条 期限：首期款于验收合格后30天内结清。", clause_no=[4]),
        _clause("第五条 期限：尾款于交货后60天内结清。", clause_no=[5]),
    ]
    revised = [
        _clause("第三条 付款期限：付款期限分别按第四条、第五条执行。", clause_no=[3]),
        _clause("第四条 期限：首期款于验收合格后30天内结清。", clause_no=[4]),
        _clause("第五条 期限：尾款于交货后90天内结清。", clause_no=[5]),
    ]
    assert (
        analysis.align_item(
            "payment_term",
            analysis.extract_payment_term(base),
            analysis.extract_payment_term(revised),
        )
        == "modified"
    )


def test_payment_reference_range_expands_to_intermediate_clauses():
    """第X条至第Y条 的区间引用覆盖中间条款。"""
    clauses = [
        _clause("第三条 付款期限：付款期限按第四条至第六条执行。", clause_no=[3]),
        _clause("第四条 期限：第一期于验收合格后30天内结清。", clause_no=[4]),
        _clause("第五条 期限：第二期于交货后60天内结清。", clause_no=[5]),
        _clause("第六条 期限：第三期于验收合格后90天内结清。", clause_no=[6]),
    ]
    facts = analysis.extract_payment_term(clauses)
    assert facts["status"] == "ambiguous"
    assert {(c["value"], c["unit"]) for c in facts["candidates"]} == {
        (30, "day"),
        (60, "day"),
        (90, "day"),
    }


def test_payment_reference_unresolved_target_is_ambiguous():
    """引用不存在的条款号时不得只报已找到的值，降级 ambiguous。"""
    clauses = [
        _clause("第三条 付款期限：付款期限按第四条、第九条执行。", clause_no=[3]),
        _clause("第四条 期限：首期款于验收合格后30天内结清。", clause_no=[4]),
    ]
    facts = analysis.extract_payment_term(clauses)
    assert facts["status"] == "ambiguous"
    assert "reference_unresolved" in facts["reason"]
    assert "value" not in facts
    assert [c["value"] for c in facts["candidates"]] == [30]


def test_acceptance_union_excludes_exception_articles():
    """例外条款不是验收证据：验收 union 不得包含例外条款号。"""
    clauses = [
        _clause("第四条 验收：乙方交付后10个工作日内完成验收。", clause_no=[4]),
        _clause("第九条 例外：因不可抗力导致验收合格延迟的，期限顺延。", clause_no=[9]),
    ]
    facts = analysis.extract_acceptance_invoice(clauses)
    assert facts["clause_no_union"] == [4]


def test_payment_reference_ignores_penalty_target():
    """「支付违约金按第九条」不是付款期限引用：赔偿期限不得吸成账期（评审 B R1）。"""
    clauses = [
        _clause("第三条 付款期限：支付违约金按第九条执行。", clause_no=[3]),
        _clause("第九条 责任：甲方应在15个工作日内向乙方赔偿全部损失。", clause_no=[9]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_payment_reference_rejects_invoicing_duration():
    """被引用条款的期限后接开具发票：开票期限不是付款期限（评审 B R2）。"""
    clauses = [
        _clause("第三条 付款期限：甲方应在收到发票后付款，具体账期详见第四条。", clause_no=[3]),
        _clause("第四条 开票：乙方应在交付后10个工作日内开具发票。", clause_no=[4]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_payment_reference_rejects_action_duration():
    """被引用条款的期限后接提交对账单：对账期限不是付款期限。"""
    clauses = [
        _clause("第三条 付款期限：具体账期按第四条执行。", clause_no=[3]),
        _clause("第四条 对账：乙方应在10个工作日内提交对账单。", clause_no=[4]),
    ]
    assert analysis.extract_payment_term(clauses) == {
        "status": "not_found",
        "reason": "no_duration",
    }


def test_payment_reference_ignores_process_change():
    """付款期限已明示 30 天：流程引用的审批期限 60→90 不得污染付款项（评审 B B2）。"""
    base = [
        _clause(
            "第三条 付款期限：甲方应在收到发票后30天内支付货款，具体付款流程按第四条执行。",
            clause_no=[3],
        ),
        _clause("第四条 期限：材料齐备后60天内完成内部审批。", clause_no=[4]),
    ]
    revised = [
        _clause(
            "第三条 付款期限：甲方应在收到发票后30天内支付货款，具体付款流程按第四条执行。",
            clause_no=[3],
        ),
        _clause("第四条 期限：材料齐备后90天内完成内部审批。", clause_no=[4]),
    ]
    facts = analysis.extract_payment_term(base)
    assert facts["status"] == "identified"
    assert (facts["value"], facts["unit"]) == (30, "day")
    assert (
        analysis.align_item(
            "payment_term",
            facts,
            analysis.extract_payment_term(revised),
        )
        == "unchanged"
    )


def test_payment_reference_heading_form():
    """标题形态「付款期限：按第四条执行」仍应跟随引用。"""
    clauses = [
        _clause("第三条 付款期限：按第四条执行。", clause_no=[3]),
        _clause("第四条 期限：验收合格后30天内。", clause_no=[4]),
    ]
    facts = analysis.extract_payment_term(clauses)
    assert facts["status"] == "identified"
    assert (facts["value"], facts["unit"]) == (30, "day")
    assert facts["clause_no"] == [4]


def test_payment_reference_heading_form_covers_term_phrases():
    """标题形态覆盖全部期限型短语：账期/付款时间/结算周期：按第四条执行。"""
    for heading in ("账期", "付款时间", "结算周期"):
        clauses = [
            _clause(f"第三条 {heading}：按第四条执行。", clause_no=[3]),
            _clause("第四条 期限：验收合格后30天内。", clause_no=[4]),
        ]
        facts = analysis.extract_payment_term(clauses)
        assert facts["status"] == "identified", heading
        assert (facts["value"], facts["unit"]) == (30, "day"), heading
        assert facts["clause_no"] == [4], heading


def test_start_condition_recognizes_public_template_events():
    """公开示范文本高频起算：合同签订生效 + 具备实施条件（D11）。"""
    facts = analysis.extract_start_condition(
        [
            _clause(
                "第三条 付款方式：合同签订生效并具备实施条件后5个工作日内支付预付款。",
                clause_no=[3],
            )
        ]
    )
    assert facts["status"] == "identified"
    assert set(facts["events"]) == {"合同签订生效", "具备实施条件"}


def test_start_condition_recognizes_contract_effective():
    """合同生效后起算（未写签署之日/生效之日）也应识别。"""
    facts = analysis.extract_start_condition(
        [_clause("第三条 付款期限：甲方应在合同生效后10个工作日内支付首期款。", clause_no=[3])]
    )
    assert facts["status"] == "identified"
    assert facts["events"] == ["合同生效"]


def test_start_event_word_change_in_referenced_clause_is_modified():
    """被引用条款里 合同生效→合同签订生效 必须报 payment modified（评审 A B1）。"""
    base = [
        _clause("第三条 付款方式：验收合格后，具体账期按第四条执行。", clause_no=[3]),
        _clause("第四条 履行期限：合同生效后10个工作日内付清。", clause_no=[4]),
    ]
    revised = [
        _clause("第三条 付款方式：验收合格后，具体账期按第四条执行。", clause_no=[3]),
        _clause("第四条 履行期限：合同签订生效后10个工作日内付清。", clause_no=[4]),
    ]
    base_facts = analysis.extract_payment_term(base)
    revised_facts = analysis.extract_payment_term(revised)
    assert (base_facts["value"], base_facts["unit"]) == (10, "workday")
    assert analysis.align_item("payment_term", base_facts, revised_facts) == "modified"
    assert (
        analysis.align_item(
            "start_condition",
            analysis.extract_start_condition(base),
            analysis.extract_start_condition(revised),
        )
        == "unchanged"
    )


def test_overlapping_start_events_do_not_double_count():
    """「合同生效之日起」只算一个起算事件，不得同时计 合同生效 + 生效之日。"""
    facts = analysis.extract_start_condition(
        [
            _clause(
                "第三条 付款期限：甲方应在合同生效之日起10个工作日内支付货款。",
                clause_no=[3],
            )
        ]
    )
    assert facts["status"] == "identified"
    assert facts["events"] == ["合同生效"]


def test_referenced_clause_acceptance_term_change_does_not_flip_payment():
    """引用条款内验收词变化只归验收项，付款项不得一起报 modified（评审 B N7）。"""
    base = [
        _clause("第三条 付款方式：验收合格后，具体账期按第四条执行。", clause_no=[3]),
        _clause(
            "第四条 履行期限：合同生效后10个工作日内，验收安排按本条约定办理。",
            clause_no=[4],
        ),
    ]
    revised = [
        _clause("第三条 付款方式：验收合格后，具体账期按第四条执行。", clause_no=[3]),
        _clause(
            "第四条 履行期限：合同生效后10个工作日内，对账安排按本条约定办理。",
            clause_no=[4],
        ),
    ]
    base_payment = analysis.extract_payment_term(base)
    assert (base_payment["value"], base_payment["unit"]) == (10, "workday")
    assert (
        analysis.align_item("payment_term", base_payment, analysis.extract_payment_term(revised))
        == "unchanged"
    )
    assert (
        analysis.align_item(
            "acceptance_invoice",
            analysis.extract_acceptance_invoice(base),
            analysis.extract_acceptance_invoice(revised),
        )
        == "modified"
    )


def test_start_condition_ignores_liability_articles():
    """违约/赔偿条款里的「合同生效」是责任成立条件，不是付款起算（N1）。"""
    clauses = [
        _clause("第三条 付款期限：甲方应在验收合格后30天内支付货款。", clause_no=[3]),
        _clause(
            "第六条 违约责任：合同生效后，违约方应向守约方支付违约金，违约金为合同总价的5%。",
            clause_no=[6],
        ),
    ]
    facts = analysis.extract_start_condition(clauses)
    assert facts["events"] == ["验收合格"]

    only_liability = analysis.extract_start_condition(
        [
            _clause(
                "第六条 违约责任：合同生效后，违约方应向守约方支付违约金。",
                clause_no=[6],
            )
        ]
    )
    assert only_liability["status"] == "not_found"


def test_liability_heading_with_payment_terms_is_not_excluded():
    """混合标题（付款期限及违约责任/逾期付款违约金）不得整条踢出起算（评审 B B1）。"""
    for heading in ("付款期限及违约责任", "逾期付款违约金", "违约责任及付款期限"):
        facts = analysis.extract_start_condition(
            [
                _clause(
                    f"第六条 {heading}：甲方应在验收合格后30天内支付货款。",
                    clause_no=[6],
                )
            ]
        )
        assert facts["status"] == "identified", heading
        assert facts["events"] == ["验收合格"], heading


def test_liability_heading_after_clause_number_colon_is_excluded():
    """「第六条：违约责任：…」也是独立责任条款，应排除出起算。"""
    facts = analysis.extract_start_condition(
        [
            _clause(
                "第六条：违约责任：合同生效后，违约方应向守约方支付违约金。",
                clause_no=[6],
            )
        ]
    )
    assert facts["status"] == "not_found"
