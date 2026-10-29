"""确定性条款抽取与 V1↔V2 对齐（合同核验场景 Batch 3）。

口径（与 ``_scratch/orchestration/audit_contract_review_gap_20261027.md`` 对齐）：

- 只从 ``DocumentChunk.original_text``（``text_provenance=parser_original``）读取；
  文档未索引/无分块/混有 legacy 时分析显式拒绝，绝不把不完整输入当全量；
- 只抽取四个固定核验项的**文本事实**：期限值/单位（工作日/自然日/月）、起算
  事件、验收/发票前提、例外/暂缓/另有约定/交叉引用；无法确定时标
  ``ambiguous``/``not_found``，绝不猜值；
- 抽取前统一做版式归一（NFKC、去空白、去页码噪声），但证据 quote 始终保存
  原始原文；日期片段（2025年3月/3月15日）与保修/质保/审核等非付款期限
  一律不作为账期候选；
- 对齐 = 文本事实 + 归一化主条款文本哈希：事实不同 → modified；事实相同但
  文本变化（否定/主体/限定语）→ modified；事实与文本相同、条号变化 →
  renumbered；完全相同 → unchanged；无法判定/扫描截断 → unidentified；
- 条款/事件的先后顺序不产生事实差异（按集合比较，仅顺序变化 → unchanged）；
  moved 类型 v1 不产出；
- 纯规则实现：不调用 LLM、不访问网络、不依赖 ES；同一输入重复运行结果一致；
- 主条款以 candidate 证据快照落库（已存在的同一 item/side/chunk 不重复写入，
  不触碰人工已选/角色）。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.document import Document, DocumentChunk, DocumentStatus
from app.models.review import ReviewCase, ReviewItem
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services.clause_refs import extract_clause_fields
from app.services.review_case_service import case_detail, ensure_candidate_evidence
from app.services.review_document_service import (
    MAX_EVIDENCE_SCAN_CHUNKS,
    chunk_original_text,
    get_review_meta,
    iter_chunks,
)

EXTRACTOR_VERSION = 1
ITEM_KEYS = ("payment_term", "start_condition", "acceptance_invoice", "exception")
# 人工/导出可判定的对齐结果（moved 需要版式位置语义，v1 不产出）。
CHANGE_TYPES = (
    "unchanged",
    "modified",
    "added",
    "removed",
    "renumbered",
    "unidentified",
)

PAYMENT_KEYWORDS = ("付款", "支付", "账期", "价款", "货款", "结算")
# 期限匹配必须离付款锚点比这些「非付款期限」语境更近（保修期/日期签署等）。
NON_PAYMENT_CONTEXT = (
    "保修",
    "质保",
    "审核",
    "有效期",
    "保密期",
    "到期",
    "保证金",
    "押金",
    "逾期",
    "违约",
    "滞纳",
    "罚息",
)
# 「天」按自然日；单独「日」无法判定自然日/工作日 → ambiguous。
DURATION_RE = re.compile(r"(\d+)\s*(个)?\s*(工作日|自然日|日历日|天|日)")
MONTH_RE = re.compile(r"(\d+)\s*个\s*月")
SENTENCE_SPLIT_RE = re.compile(r"[。；;！!？?\n]+")
FRAGMENT_SPLIT_RE = re.compile(r"[，,、]")
UNIT_MAP = {"工作日": "workday", "自然日": "day", "日历日": "day", "天": "day"}
START_EVENTS = (
    "收到发票",
    "收到对账单",
    "对账无误",
    "对账无异议",
    "验收合格",
    "货物交付",
    "交付验收",
    "签署之日",
    "生效之日",
    "开票",
)
ACCEPTANCE_TERMS = ("验收合格", "对账单", "验收", "发票", "对账")
EXCEPTION_TERMS = (
    "不可抗力",
    "补充协议",
    "另行协商",
    "另行约定",
    "另有约定",
    "暂缓",
    "除外",
    "例外",
)
CROSS_REF_RE = re.compile(
    r"(?:详见|见|依据|根据|按照|参照)(?:本合同|本协议)?"
    r"第[一二三四五六七八九十百零〇0-9]+条"
)


@dataclass(frozen=True)
class Clause:
    """条款：text 为版式归一后的抽取文本，raw_text 为证据 quote 原文。"""

    chunk_id: str
    chunk_index: int
    text: str
    raw_text: str
    clause_no: list[int]
    section_no: list[str]
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None


def normalize_clause_text(text: str) -> str:
    """归一化空白/全半角/页码噪声；只服务抽取与文本对比，不改原文。"""
    value = unicodedata.normalize("NFKC", text)
    value = re.sub(r"第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?", "", value)
    return re.sub(r"\s+", "", value)


# 比较文本事实时剥掉行首条号：仅条号变化记 renumbered，不记 modified。
_CLAUSE_NUMBER_PREFIX_RE = re.compile(r"^第[一二三四五六七八九十百零〇0-9]+条")


def _content_sha(clause: Clause) -> str:
    content = _CLAUSE_NUMBER_PREFIX_RE.sub("", clause.text, count=1)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _matched_text_sha(clauses: list[Clause]) -> str:
    """命中条款集合的文本哈希（按集合，条款先后顺序不产生差异）。"""
    unique = {clause.chunk_id: clause for clause in clauses}
    return hashlib.sha256(
        "|".join(sorted(_content_sha(c) for c in unique.values())).encode()
    ).hexdigest()


def _clause_no_union(clauses: list[Clause]) -> list[int]:
    return sorted({number for clause in clauses for number in clause.clause_no})


def _dedupe_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    """同一条款里取最长命中词，避免「对账单」同时命中「对账」。"""
    matched = [term for term in terms if term in text]
    result: list[str] = []
    for term in matched:
        if any(term != other and term in other for other in matched):
            continue
        if term not in result:
            result.append(term)
    return result


def _primary_locator(clause: Clause) -> dict[str, Any]:
    return {
        "chunk_id": clause.chunk_id,
        "chunk_index": clause.chunk_index,
        "clause_no": list(clause.clause_no),
        "section_no": list(clause.section_no),
        "page_number": clause.page_number,
    }


def _iter_sentences(text: str):
    start = 0
    for match in SENTENCE_SPLIT_RE.finditer(text):
        yield text[start : match.start()]
        start = match.end()
    yield text[start:]


def _anchor_distance(sentence: str, start: int, end: int, anchors: tuple[str, ...]) -> int | None:
    """最近的锚点到 [start, end) 的字符距离；锚点在数字前后都算。"""
    best: int | None = None
    for anchor in anchors:
        index = sentence.find(anchor)
        while index != -1:
            anchor_start, anchor_end = index, index + len(anchor)
            if anchor_end <= start:
                distance = start - anchor_end
            elif anchor_start >= end:
                distance = anchor_start - end
            else:  # 理论上不会与纯数字区间重叠
                distance = 0
            if best is None or distance < best:
                best = distance
            index = sentence.find(anchor, index + 1)
    return best


def _iter_fragments(sentence: str):
    """按逗号/顿号切分句内片段，yield (片段文本, 起始偏移)。"""
    start = 0
    for match in FRAGMENT_SPLIT_RE.finditer(sentence):
        yield sentence[start : match.start()], start
        start = match.end()
    yield sentence[start:], start


def _adjacent_to_exclusion(fragment: str, start: int, end: int) -> bool:
    for exclusion in NON_PAYMENT_CONTEXT:
        index = fragment.find(exclusion)
        while index != -1:
            exclusion_start, exclusion_end = index, index + len(exclusion)
            if start <= exclusion_end + 1 and exclusion_start <= end + 1:
                return True
            index = fragment.find(exclusion, index + 1)
    return False


def _payment_anchor_distance(fragment: str, start: int, end: int) -> int | None:
    """最近的付款锚点距离；与排除锚点相邻的付款词（如「逾期付款」）不算。"""
    best: int | None = None
    for anchor in PAYMENT_KEYWORDS:
        index = fragment.find(anchor)
        while index != -1:
            anchor_start, anchor_end = index, index + len(anchor)
            if not _adjacent_to_exclusion(fragment, anchor_start, anchor_end):
                if anchor_end <= start:
                    distance = start - anchor_end
                elif anchor_start >= end:
                    distance = anchor_start - end
                else:
                    distance = 0
                if best is None or distance < best:
                    best = distance
            index = fragment.find(anchor, index + 1)
    return best


def _duration_context_ok(sentence: str, start: int, end: int) -> bool:
    """期限只归属它所在的分句片段（中文「保修期为12个月，付款时扣留」
    里的付款动作不能把保修期认成账期）。

    - 片段内有付款锚点：与排除锚点（保修/逾期/违约…）按距离裁决，付款
      更近或没有排除锚点才接受——覆盖「收到发票后 30 天内支付」这类
      付款动词在数字之后的常见语序；
    - 片段内没有付款锚点：仅当上一分句有付款锚点且本片段没有排除锚点
      时接受（如「关于付款，期限为 30 天」）。
    """
    previous_text = ""
    for fragment, offset in _iter_fragments(sentence):
        fragment_end = offset + len(fragment)
        if start >= offset and end <= fragment_end:
            local_start, local_end = start - offset, end - offset
            payment = _payment_anchor_distance(fragment, local_start, local_end)
            excluded = _anchor_distance(fragment, local_start, local_end, NON_PAYMENT_CONTEXT)
            if payment is not None:
                return excluded is None or payment < excluded
            if excluded is not None:
                return False
            return _anchor_distance(previous_text, 0, 0, PAYMENT_KEYWORDS) is not None
        previous_text = fragment
    return False


def _is_date_fragment(text: str, start: int, unit_raw: str) -> bool:
    """排除日期片段：2025年3月、3月15日中的「3月」「15日」。"""
    before = text[:start].rstrip()
    if unit_raw == "月":
        return before.endswith("年")
    if unit_raw in ("日", "天"):
        return before.endswith("月") or before.endswith("年")
    return False


def extract_payment_term(clauses: list[Clause]) -> dict[str, Any]:
    payment_clauses = [c for c in clauses if any(k in c.text for k in PAYMENT_KEYWORDS)]
    hits: list[dict[str, Any]] = []
    for clause in payment_clauses:
        for sentence in _iter_sentences(clause.text):
            for match in DURATION_RE.finditer(sentence):
                unit_raw = match.group(3)
                if not _duration_context_ok(sentence, match.start(), match.end()):
                    continue
                if _is_date_fragment(sentence, match.start(), unit_raw):
                    continue
                hits.append(
                    {
                        "value": int(match.group(1)),
                        "unit": UNIT_MAP.get(unit_raw),
                        "unit_raw": unit_raw,
                        "raw": match.group(0),
                        "_clause": clause,
                    }
                )
            for match in MONTH_RE.finditer(sentence):
                if not _duration_context_ok(sentence, match.start(), match.end()):
                    continue
                if _is_date_fragment(sentence, match.start(), "月"):
                    continue
                hits.append(
                    {
                        "value": int(match.group(1)),
                        "unit": "month",
                        "unit_raw": "月",
                        "raw": match.group(0),
                        "_clause": clause,
                    }
                )
    if not hits:
        reason = "no_duration" if payment_clauses else "no_payment_clause"
        return {"status": "not_found", "reason": reason}
    ambiguous_units = [hit for hit in hits if hit["unit"] is None]
    distinct: list[dict[str, Any]] = []
    for hit in hits:
        key = (hit["value"], hit["unit"], hit["unit_raw"])
        if all((d["value"], d["unit"], d["unit_raw"]) != key for d in distinct):
            distinct.append(hit)
    matched_clauses = [hit["_clause"] for hit in hits]
    text_sha = _matched_text_sha(matched_clauses)
    clause_no_union = _clause_no_union(matched_clauses)
    if len(distinct) == 1 and not ambiguous_units:
        hit = distinct[0]
        return {
            "status": "identified",
            "value": hit["value"],
            "unit": hit["unit"],
            "raw": hit["raw"],
            "text_sha256": text_sha,
            **_primary_locator(hit["_clause"]),
            "clause_no_union": clause_no_union,
        }
    reasons: list[str] = []
    if ambiguous_units:
        reasons.append("unit_unclear")
    if len(distinct) > 1:
        reasons.append("multiple_durations")
    return {
        "status": "ambiguous",
        "reason": "+".join(reasons),
        "candidates": [
            {key: hit[key] for key in ("value", "unit", "unit_raw", "raw")} for hit in distinct
        ],
        "text_sha256": text_sha,
        **_primary_locator(distinct[0]["_clause"]),
        "clause_no_union": clause_no_union,
    }


def extract_start_condition(clauses: list[Clause]) -> dict[str, Any]:
    payment_clauses = [c for c in clauses if any(k in c.text for k in PAYMENT_KEYWORDS)]
    events: list[dict[str, Any]] = []
    for clause in payment_clauses:
        for event in START_EVENTS:
            if event in clause.text and all(e["event"] != event for e in events):
                events.append({"event": event, "_clause": clause, **_primary_locator(clause)})
    if not events:
        reason = "no_start_event" if payment_clauses else "no_payment_clause"
        return {"status": "not_found", "reason": reason}
    first = events[0]
    locator = {
        key: first[key]
        for key in ("chunk_id", "chunk_index", "clause_no", "section_no", "page_number")
    }
    matched_clauses = [event["_clause"] for event in events]
    return {
        "status": "identified",
        "events": [event["event"] for event in events],
        "text_sha256": _matched_text_sha(matched_clauses),
        **locator,
        "clause_no_union": _clause_no_union(matched_clauses),
    }


def _collect_terms(clauses: list[Clause], terms: tuple[str, ...]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for clause in clauses:
        for term in _dedupe_terms(clause.text, terms):
            if all(hit["term"] != term for hit in hits):
                hits.append({"term": term, "_clause": clause, **_primary_locator(clause)})
    if not hits:
        return {"status": "not_found", "reason": "no_terms"}
    first = hits[0]
    locator = {
        key: first[key]
        for key in ("chunk_id", "chunk_index", "clause_no", "section_no", "page_number")
    }
    matched_clauses = [hit["_clause"] for hit in hits]
    return {
        "status": "identified",
        "terms": [hit["term"] for hit in hits],
        "text_sha256": _matched_text_sha(matched_clauses),
        **locator,
        "clause_no_union": _clause_no_union(matched_clauses),
    }


def extract_cross_refs(clauses: list[Clause]) -> list[str]:
    refs: list[str] = []
    for clause in clauses:
        for match in CROSS_REF_RE.finditer(clause.text):
            if match.group(0) not in refs:
                refs.append(match.group(0))
    return refs


def extract_acceptance_invoice(clauses: list[Clause]) -> dict[str, Any]:
    return _collect_terms(clauses, ACCEPTANCE_TERMS)


def extract_exception(clauses: list[Clause]) -> dict[str, Any]:
    facts = _collect_terms(clauses, EXCEPTION_TERMS)
    facts["cross_refs"] = extract_cross_refs(clauses)
    return facts


EXTRACTORS = {
    "payment_term": extract_payment_term,
    "start_condition": extract_start_condition,
    "acceptance_invoice": extract_acceptance_invoice,
    "exception": extract_exception,
}


def _semantic_payload(item_key: str, facts: dict[str, Any]) -> Any:
    if facts["status"] != "identified":
        return None
    if item_key == "payment_term":
        return (facts.get("value"), facts.get("unit"))
    # 集合语义：条款/事件的先后顺序不是事实差异（顺序变化由主条款文本哈希体现）。
    return tuple(sorted(facts.get("events") or facts.get("terms") or ()))


def align_item(
    item_key: str,
    base: dict[str, Any],
    revised: dict[str, Any],
    *,
    scan_truncated: bool = False,
) -> str:
    """只比较文本事实 + 归一化主条款文本；无法判定时不报「无差异」。"""
    base_found = base["status"] != "not_found"
    revised_found = revised["status"] != "not_found"
    result: str
    if not base_found and not revised_found:
        result = "unidentified"
    elif not base_found:
        result = "added"
    elif not revised_found:
        result = "removed"
    elif base["status"] == "ambiguous" or revised["status"] == "ambiguous":
        result = "unidentified"
    elif _semantic_payload(item_key, base) == _semantic_payload(item_key, revised):
        base_sha = base.get("text_sha256")
        revised_sha = revised.get("text_sha256")
        if base_sha and revised_sha and base_sha != revised_sha:
            # 事实词元相同但文本变了（否定/主体/限定语）：必须报差异。
            result = "modified"
        elif (base.get("clause_no_union") or base.get("clause_no") or []) != (
            revised.get("clause_no_union") or revised.get("clause_no") or []
        ):
            result = "renumbered"
        else:
            result = "unchanged"
    else:
        result = "modified"
    if scan_truncated and result != "modified":
        # 扫描被截断时不能宣称「无差异/仅重编号/新增/缺失」这类需要全量的结论。
        return "unidentified"
    return result


async def _load_clauses(
    session: AsyncSession, document: Document, side: str
) -> tuple[list[Clause], dict[str, Any]]:
    """读取一方的 parser 原文条款；未索引/无分块/混 legacy 都显式拒绝。"""
    if document.status != DocumentStatus.INDEXED:
        raise ConflictError(
            "案件文档当前不可用于分析（未索引或解析失败）",
            error_code="REVIEW_DOCUMENT_NOT_READY",
            detail={"side": side, "status": document.status.value},
        )
    total = int(
        (
            await session.execute(
                select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id)
            )
        ).scalar()
        or 0
    )
    if total == 0:
        raise ConflictError(
            "案件文档没有可分析的分块",
            error_code="REVIEW_DOCUMENT_NOT_READY",
            detail={"side": side, "reason": "no_chunks"},
        )
    clauses: list[Clause] = []
    scanned = 0
    legacy = 0
    async for chunk in iter_chunks(session, document.id, max_chunks=MAX_EVIDENCE_SCAN_CHUNKS):
        scanned += 1
        raw = chunk_original_text(chunk)
        if raw is None:
            legacy += 1
            continue
        fields = extract_clause_fields(raw)
        clauses.append(
            Clause(
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                text=normalize_clause_text(raw),
                raw_text=raw,
                clause_no=fields["clause_no"],
                section_no=fields["section_no"],
                page_number=chunk.page_number,
                char_start=chunk.start_pos,
                char_end=chunk.end_pos,
            )
        )
    if legacy > 0:
        # 混有未迁移分块 = 证据覆盖不完整；与 Batch 1 readiness 的 legacy_unverified 同口径。
        raise SourceProvenanceUnavailableError([document.id], unverified_count=legacy)
    return clauses, {
        "scanned_chunks": scanned,
        "truncated": scanned >= MAX_EVIDENCE_SCAN_CHUNKS and scanned < total,
    }


def _clause_by_locator(clauses: list[Clause], facts: dict[str, Any]) -> Clause | None:
    chunk_id = facts.get("chunk_id")
    if not chunk_id:
        return None
    for clause in clauses:
        if clause.chunk_id == chunk_id:
            return clause
    return None


async def analyze_case(session: AsyncSession, case: ReviewCase) -> dict[str, Any]:
    """对 ready/in_review 案件做确定性抽取与对齐，更新 items 并快照主条款证据。"""
    if case.status not in ("ready", "in_review"):
        raise ConflictError(
            "当前案件状态不允许执行核验分析",
            error_code="REVIEW_CASE_NOT_READY",
            detail={"status": case.status},
        )

    side_docs = {
        "policy": case.policy_document_id,
        "base": case.base_document_id,
        "revised": case.revised_document_id,
    }
    clauses: dict[str, list[Clause]] = {}
    scans: dict[str, dict[str, Any]] = {}
    metas = {}
    for side, document_id in side_docs.items():
        document = await session.get(Document, document_id)
        if document is None or document.organization_id != case.organization_id:
            raise ConflictError(
                "案件引用的文档已不存在，无法分析",
                error_code="REVIEW_DOCUMENT_UNAVAILABLE",
                detail={"document_id": document_id, "side": side},
            )
        clauses[side], scans[side] = await _load_clauses(session, document, side)
        metas[side] = await get_review_meta(session, document_id)
        if metas[side] is None:
            raise ConflictError(
                "案件文档缺少核验身份，无法分析",
                error_code="REVIEW_META_MISSING",
                detail={"document_id": document_id, "side": side},
            )

    items = (
        (
            await session.execute(
                select(ReviewItem)
                .where(ReviewItem.case_id == case.id)
                .order_by(ReviewItem.ordinal.asc(), ReviewItem.id.asc())
            )
        )
        .scalars()
        .all()
    )
    for item in items:
        extractor = EXTRACTORS.get(item.item_key)
        if extractor is None:
            continue
        facts_by_side = {side: extractor(clauses[side]) for side in side_docs}
        scan_truncated = scans["base"]["truncated"] or scans["revised"]["truncated"]
        item.facts = {
            "extractor_version": EXTRACTOR_VERSION,
            "scan": {side: dict(scan) for side, scan in scans.items()},
            **facts_by_side,
        }
        item.change_type = align_item(
            item.item_key,
            facts_by_side["base"],
            facts_by_side["revised"],
            scan_truncated=scan_truncated,
        )
        for side in side_docs:
            facts = facts_by_side[side]
            if facts["status"] == "not_found":
                continue
            clause = _clause_by_locator(clauses[side], facts)
            if clause is None or metas[side] is None:
                continue
            await ensure_candidate_evidence(
                session,
                case,
                item,
                side=side,
                document_id=side_docs[side],
                meta=metas[side],
                chunk_id=clause.chunk_id,
                chunk_index=clause.chunk_index,
                quote=clause.raw_text,
                page_number=clause.page_number,
                char_start=clause.char_start,
                char_end=clause.char_end,
            )
    try:
        await session.commit()
    except IntegrityError as exc:
        # 并发两次 analyze 同时补同一 (item, side, chunk)：唯一约束最后兜底。
        await session.rollback()
        raise ConflictError(
            "核验分析并发冲突，请重试", error_code="REVIEW_CASE_ANALYSIS_CONFLICT"
        ) from exc
    # commit 后 onupdate 列（updated_at）可能处于过期状态；在**当前会话**里重载
    # 案件再组装详情，避免对游离对象 refresh 或同步属性访问触发隐式 IO。
    fresh = await session.get(ReviewCase, case.id)
    if fresh is None:
        raise ConflictError("核验案件已被删除", error_code="REVIEW_CASE_NOT_FOUND")
    await session.refresh(fresh)
    return await case_detail(session, fresh)
