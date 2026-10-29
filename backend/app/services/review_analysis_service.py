"""确定性条款抽取与 V1↔V2 对齐（合同核验场景 Batch 3）。

口径（与 ``_scratch/orchestration/audit_contract_review_gap_20261027.md`` 对齐）：

- 只从 ``DocumentChunk.original_text``（``text_provenance=parser_original``）读取；
- 只抽取四个固定核验项的**文本事实**：期限值/单位（工作日/自然日）、起算事件、
  验收/发票前提、例外/暂缓/另有约定；无法确定时标 ``ambiguous``/``not_found``，
  绝不猜值、绝不补默认值；
- 对齐只输出 ``unchanged/modified/added/removed/renumbered/unidentified``
  这类文本差异类型，不输出任何法律结论；
- 纯规则实现：不调用 LLM、不访问网络、不依赖 ES；同一输入重复运行结果一致；
- 抽取/对齐命中的主条款会作为 candidate 证据快照落库（已存在的同一
  item/side/chunk 不重复写入），人工仍可在工作台改选。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError
from app.models.document import Document
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
# 「天」按自然日；单独「日」无法判定自然日/工作日 → ambiguous。
DURATION_RE = re.compile(r"(\d+)\s*(个)?\s*(工作日|自然日|日历日|天|日)")
MONTH_RE = re.compile(r"(\d+)\s*个?\s*月")
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


@dataclass(frozen=True)
class Clause:
    chunk_id: str
    chunk_index: int
    text: str
    clause_no: list[int] = field(default_factory=list)
    section_no: list[str] = field(default_factory=list)
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None


def normalize_clause_text(text: str) -> str:
    """归一化空白/全半角/页码噪声；只服务文本对比，不改原文。"""
    value = unicodedata.normalize("NFKC", text)
    value = re.sub(r"第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?", "", value)
    return re.sub(r"\s+", "", value)


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


def extract_payment_term(clauses: list[Clause]) -> dict[str, Any]:
    payment_clauses = [c for c in clauses if any(k in c.text for k in PAYMENT_KEYWORDS)]
    hits: list[dict[str, Any]] = []
    for clause in payment_clauses:
        for match in DURATION_RE.finditer(clause.text):
            unit_raw = match.group(3)
            hits.append(
                {
                    "value": int(match.group(1)),
                    "unit": UNIT_MAP.get(unit_raw),
                    "unit_raw": unit_raw,
                    "raw": match.group(0),
                    "locator": _primary_locator(clause),
                }
            )
        for match in MONTH_RE.finditer(clause.text):
            hits.append(
                {
                    "value": int(match.group(1)),
                    "unit": "month",
                    "unit_raw": "月",
                    "raw": match.group(0),
                    "locator": _primary_locator(clause),
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
    if len(distinct) == 1 and not ambiguous_units:
        hit = distinct[0]
        return {
            "status": "identified",
            "value": hit["value"],
            "unit": hit["unit"],
            "raw": hit["raw"],
            **hit["locator"],
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
        **distinct[0]["locator"],
    }


def extract_start_condition(clauses: list[Clause]) -> dict[str, Any]:
    payment_clauses = [c for c in clauses if any(k in c.text for k in PAYMENT_KEYWORDS)]
    events: list[dict[str, Any]] = []
    for clause in payment_clauses:
        for event in START_EVENTS:
            if event in clause.text and all(e["event"] != event for e in events):
                events.append({"event": event, **_primary_locator(clause)})
    if not events:
        reason = "no_start_event" if payment_clauses else "no_payment_clause"
        return {"status": "not_found", "reason": reason}
    first = events[0]
    locator = {
        key: first[key]
        for key in ("chunk_id", "chunk_index", "clause_no", "section_no", "page_number")
    }
    return {
        "status": "identified",
        "events": [event["event"] for event in events],
        **locator,
    }


def _collect_terms(clauses: list[Clause], terms: tuple[str, ...]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for clause in clauses:
        for term in _dedupe_terms(clause.text, terms):
            if all(hit["term"] != term for hit in hits):
                hits.append({"term": term, **_primary_locator(clause)})
    if not hits:
        return {"status": "not_found", "reason": "no_terms"}
    first = hits[0]
    locator = {
        key: first[key]
        for key in ("chunk_id", "chunk_index", "clause_no", "section_no", "page_number")
    }
    return {"status": "identified", "terms": [hit["term"] for hit in hits], **locator}


def extract_acceptance_invoice(clauses: list[Clause]) -> dict[str, Any]:
    return _collect_terms(clauses, ACCEPTANCE_TERMS)


def extract_exception(clauses: list[Clause]) -> dict[str, Any]:
    return _collect_terms(clauses, EXCEPTION_TERMS)


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
    return tuple(facts.get("events") or facts.get("terms") or ())


def align_item(item_key: str, base: dict[str, Any], revised: dict[str, Any]) -> str:
    """只比较文本事实；无法判定时返回 unidentified，绝不猜「无差异」。"""
    base_found = base["status"] != "not_found"
    revised_found = revised["status"] != "not_found"
    if not base_found and not revised_found:
        return "unidentified"
    if not base_found:
        return "added"
    if not revised_found:
        return "removed"
    if base["status"] == "ambiguous" or revised["status"] == "ambiguous":
        return "unidentified"
    if _semantic_payload(item_key, base) == _semantic_payload(item_key, revised):
        base_clause = base.get("clause_no") or []
        revised_clause = revised.get("clause_no") or []
        if base_clause and revised_clause and base_clause != revised_clause:
            return "renumbered"
        return "unchanged"
    return "modified"


async def _load_clauses(session: AsyncSession, document_id: str) -> tuple[list[Clause], int, int]:
    """返回 (原文条款, parser 原文块数, 总块数)；有块但无原文时显式拒绝。"""
    clauses: list[Clause] = []
    total = 0
    original = 0
    async for chunk in iter_chunks(session, document_id, max_chunks=MAX_EVIDENCE_SCAN_CHUNKS):
        total += 1
        text = chunk_original_text(chunk)
        if text is None:
            continue
        original += 1
        fields = extract_clause_fields(text)
        clauses.append(
            Clause(
                chunk_id=chunk.id,
                chunk_index=chunk.chunk_index,
                text=text,
                clause_no=fields["clause_no"],
                section_no=fields["section_no"],
                page_number=chunk.page_number,
                char_start=chunk.start_pos,
                char_end=chunk.end_pos,
            )
        )
    if total > 0 and original == 0:
        raise SourceProvenanceUnavailableError([document_id], unverified_count=total)
    return clauses, original, total


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
    metas = {}
    for side, document_id in side_docs.items():
        document = await session.get(Document, document_id)
        if document is None or document.organization_id != case.organization_id:
            raise ConflictError(
                "案件引用的文档已不存在，无法分析",
                error_code="REVIEW_DOCUMENT_UNAVAILABLE",
                detail={"document_id": document_id, "side": side},
            )
        clauses[side], _, _ = await _load_clauses(session, document_id)
        metas[side] = await get_review_meta(session, document_id)

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
        item.facts = {"extractor_version": EXTRACTOR_VERSION, **facts_by_side}
        item.change_type = align_item(
            item.item_key, facts_by_side["base"], facts_by_side["revised"]
        )
        # 主条款快照：让 facts 的定位在重构后仍可回查（已存在则不重复）。
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
                quote=clause.text,
                page_number=clause.page_number,
                char_start=clause.char_start,
                char_end=clause.char_end,
            )
    await session.commit()
    return await case_detail(session, case)
