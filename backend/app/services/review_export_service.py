"""核验项决策与核验单导出（合同核验场景 Batch 4）。

口径（与 ``_scratch/orchestration/audit_contract_review_gap_20261027.md`` 对齐）：

- 决策只允许 ``verified`` / ``needs_human`` / ``not_applicable``；``pending`` /
  ``insufficient`` / ``parse_failed`` 不能算完成，存在任何未决项时导出返回
  409 ``REVIEW_EXPORT_BLOCKED`` 并列出项；
- 导出 payload 只包含文本事实与证据快照：版本/哈希/条款/页码/字符区间/
  quote/决策人与时间/覆盖范围/免责声明；不含 chunk_text、预签名 URL，也不含
  任何法律结论字段或文案；
- payload 的 ``payload_sha256`` 只对确定性内容计算（不含导出时间），同一状态
  重复导出得到相同哈希；决策变化 → 新行，旧行不可变；
- 导出成功把案件置为 ``completed``；completed 后再改决策会回到 ``in_review``
  并清空完成标记（旧导出保留，提示需要重新导出）；
- 导出/下载写 ``UserActivityLog`` 审计。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.review import REVIEW_SIDES, ReviewCase, ReviewExport, ReviewItem
from app.models.user_audit import UserActivityLog
from app.services.review_case_service import case_detail

DECISION_STATUSES = ("verified", "needs_human", "not_applicable")
INCOMPLETE_STATUSES = ("pending", "insufficient", "parse_failed")
EXPORT_SCHEMA_VERSION = 1
DISCLAIMER = (
    "本核验单仅覆盖本次选定的条款与文档版本；未识别与需人工项不代表任何结论，"
    "导出内容不构成法律意见。"
)
# 导出中允许出现的证据字段（quote 是原文引用；其余为定位与哈希）。
EVIDENCE_FIELDS = (
    "quote",
    "quote_sha256",
    "clause_no",
    "section_no",
    "page_number",
    "char_start",
    "char_end",
    "chunk_index",
    "role",
    "doc_version_label",
    "document_sha256",
    "text_provenance",
    "captured_at",
    "stale",
)

# Markdown 报告是给业务方的交付物：渲染层把内部枚举翻成业务文案，
# 不改动 JSON 快照（快照保持机器可读、可复算哈希）。文案与前端
# `reviews.*` 词条口径一致；未知码原样透传，不吞信息。
SIDE_LABELS = {"base": "合同基准版", "revised": "合同修订版", "policy": "制度/模板"}
ITEM_LABELS = {
    "payment_term": "付款期限",
    "start_condition": "起算条件",
    "acceptance_invoice": "验收/发票前提",
    "exception": "例外/暂缓/另有约定",
}
CHANGE_TYPE_LABELS = {
    "unchanged": "无差异",
    "modified": "有差异",
    "added": "仅修订版有",
    "removed": "仅基准版有",
    "renumbered": "条号变化",
    "unidentified": "未识别",
}
ITEM_STATUS_LABELS = {
    "verified": "已核对",
    "needs_human": "需人工处理",
    "not_applicable": "不适用",
}
FACT_STATUS_LABELS = {"identified": "已识别", "ambiguous": "存疑", "not_found": "未识别"}
FACT_REASON_LABELS = {
    "unit_unclear": "单位无法判定（自然日/工作日）",
    "multiple_durations": "同一核验项出现多个期限候选",
    "reference_unresolved": "引用的条款无法定位",
}
UNIT_LABELS = {"day": "自然日", "workday": "工作日", "month": "个月"}


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _audit(
    session: AsyncSession,
    user_id: int,
    action: str,
    case: ReviewCase,
    detail: dict[str, Any],
) -> None:
    session.add(
        UserActivityLog(
            user_id=user_id,
            action=action,
            target_type="review_case",
            target_id=case.id,
            detail=_canonical_json(detail),
        )
    )


async def _lock_case(session: AsyncSession, case: ReviewCase) -> ReviewCase:
    """锁定案件行：decision/export 的状态机必须串行。

    MySQL REPEATABLE READ 下，请求内先前的普通 SELECT 已建立 read view，
    直接在旧事务里 FOR UPDATE 后接普通 SELECT 仍会读到旧快照（实测）。
    因此先结束只读事务，让加锁 SELECT 成为新事务的第一条语句；之后的
    refresh/case_detail 才能看到锁内最新已提交值。SQLite 忽略 FOR UPDATE。
    """
    case_id = case.id
    await session.commit()  # 结束只读事务，重建 read view
    fresh = (
        await session.execute(select(ReviewCase).where(ReviewCase.id == case_id).with_for_update())
    ).scalar_one_or_none()
    if fresh is None:
        raise ConflictError("核验案件已被删除", error_code="REVIEW_CASE_NOT_FOUND")
    await session.refresh(fresh)
    return fresh


async def decide_item(
    session: AsyncSession,
    case: ReviewCase,
    item: ReviewItem,
    *,
    status: str,
    note: str | None,
    user_id: int,
) -> ReviewItem:
    """逐项人工决策；completed 后再改决策会回到 in_review。"""
    if status not in DECISION_STATUSES:
        raise ValidationError(f"未知决策状态: {status!r}", field="status")
    case = await _lock_case(session, case)
    if case.archived_at is not None:
        raise ConflictError(
            "案件已归档：不能再变更决策",
            error_code="REVIEW_CASE_ARCHIVED",
            detail={"case_id": case.id, "status": case.status},
        )
    if case.status not in ("ready", "in_review", "completed"):
        raise ConflictError(
            "当前案件状态不允许决策",
            error_code="REVIEW_CASE_NOT_READY",
            detail={"status": case.status},
        )
    item.status = status
    item.decision_note = (note or "").strip() or None
    item.decided_by = user_id
    item.decided_at = _now()
    if case.status == "ready":
        case.status = "in_review"
    elif case.status == "completed":
        # 决策变化意味着旧导出已过期：回到 in_review，旧导出行保留。
        case.status = "in_review"
        case.completed_at = None
        case.completed_by = None
    case.lock_version = (case.lock_version or 1) + 1
    await session.commit()
    await session.refresh(item)
    return item


def decision_to_dict(item: ReviewItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "item_key": item.item_key,
        "status": item.status,
        "decided_by": item.decided_by,
        "decided_at": item.decided_at.isoformat() if item.decided_at else None,
        "decision_note": item.decision_note,
        "change_type": item.change_type,
    }


def _build_payload(case: ReviewCase, detail: dict[str, Any]) -> dict[str, Any]:
    documents: dict[str, Any] = {}
    for side in REVIEW_SIDES:
        entry = detail["documents"].get(side) or {}
        documents[side] = {
            "document_id": entry.get("document_id"),
            "filename": entry.get("filename"),
            "doc_role": entry.get("doc_role"),
            "version_label": entry.get("version_label"),
            "version_seq": entry.get("version_seq"),
            "content_sha256": entry.get("content_sha256"),
            "missing": bool(entry.get("missing")),
            "identity_changed": bool(entry.get("identity_changed")),
        }
    items: list[dict[str, Any]] = []
    scan: dict[str, Any] = {}
    for item in detail["items"]:
        evidence: dict[str, list[dict[str, Any]]] = {}
        for side in REVIEW_SIDES:
            rows = item["evidence"].get(side) or []
            evidence[side] = [
                {key: row.get(key) for key in EVIDENCE_FIELDS} for row in rows if row["selected"]
            ]
        facts = item.get("facts") or {}
        if not scan and isinstance(facts.get("scan"), dict):
            scan = facts["scan"]
        items.append(
            {
                "item_key": item["item_key"],
                "change_type": item["change_type"],
                "status": item["status"],
                "decided_by": item["decided_by"],
                "decided_at": item["decided_at"],
                "decision_note": item["decision_note"],
                "facts": facts,
                "evidence": evidence,
            }
        )
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "case": {
            "id": case.id,
            "title": case.title,
            "checklist_version": case.checklist_version,
        },
        "documents": documents,
        "items": items,
        "coverage": {
            "checklist_items": [item["item_key"] for item in items],
            "unidentified_items": [
                item["item_key"] for item in items if item["change_type"] == "unidentified"
            ],
            "needs_human_items": [
                item["item_key"] for item in items if item["status"] == "needs_human"
            ],
            "items_without_selected_evidence": [
                item["item_key"]
                for item in items
                if not any(item["evidence"].get(side) for side in REVIEW_SIDES)
            ],
            "scan": scan,
        },
        "source_stale": bool(detail["live_source_stale"]),
        "disclaimer": DISCLAIMER,
    }


async def export_case(session: AsyncSession, case: ReviewCase, *, user_id: int) -> ReviewExport:
    """生成不可变核验单快照；未决项阻止导出。"""
    case = await _lock_case(session, case)
    if case.archived_at is not None:
        raise ConflictError(
            "案件已归档：不能再导出核验单",
            error_code="REVIEW_CASE_ARCHIVED",
            detail={"case_id": case.id, "status": case.status},
        )
    if case.status not in ("ready", "in_review", "completed"):
        raise ConflictError(
            "当前案件状态不允许导出",
            error_code="REVIEW_CASE_NOT_READY",
            detail={"status": case.status},
        )
    detail = await case_detail(session, case)
    blocking: list[dict[str, Any]] = []
    for item in detail["items"]:
        reasons: list[str] = []
        if item["status"] in INCOMPLETE_STATUSES:
            reasons.append("undecided")
        if item["change_type"] is None:
            # 未运行分析（change_type 为 None）不算「已核验」，不能导出完成态。
            reasons.append("not_analyzed")
        if reasons:
            blocking.append(
                {"item_key": item["item_key"], "status": item["status"], "reasons": reasons}
            )
    if blocking:
        raise ConflictError(
            "存在未决或未分析的核验项，不能导出",
            error_code="REVIEW_EXPORT_BLOCKED",
            detail={"items": blocking},
        )
    payload = _build_payload(case, detail)
    digest = payload_sha256(payload)
    export = ReviewExport(
        id=str(uuid.uuid4()),
        case_id=case.id,
        organization_id=case.organization_id,
        schema_version=EXPORT_SCHEMA_VERSION,
        payload=payload,
        payload_sha256=digest,
        item_count=len(payload["items"]),
        needs_human_count=sum(1 for item in payload["items"] if item["status"] == "needs_human"),
        unidentified_count=sum(
            1 for item in payload["items"] if item["change_type"] == "unidentified"
        ),
        created_by=user_id,
        created_at=_now(),
    )
    session.add(export)
    case.status = "completed"
    case.completed_at = _now()
    case.completed_by = user_id
    case.lock_version = (case.lock_version or 1) + 1
    _audit(
        session,
        user_id,
        "review_export",
        case,
        {"export_id": export.id, "payload_sha256": digest},
    )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "核验单导出并发冲突，请重试", error_code="REVIEW_EXPORT_CONFLICT"
        ) from exc
    await session.refresh(export)
    return export


def export_summary(export: ReviewExport) -> dict[str, Any]:
    return {
        "id": export.id,
        "case_id": export.case_id,
        "schema_version": export.schema_version,
        "payload_sha256": export.payload_sha256,
        "item_count": export.item_count,
        "needs_human_count": export.needs_human_count,
        "unidentified_count": export.unidentified_count,
        "created_by": export.created_by,
        "created_at": export.created_at.isoformat() if export.created_at else None,
    }


async def list_exports(session: AsyncSession, case: ReviewCase) -> list[ReviewExport]:
    return list(
        (
            await session.execute(
                select(ReviewExport)
                .where(ReviewExport.case_id == case.id)
                .order_by(ReviewExport.created_at.desc(), ReviewExport.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def get_export_for_case(
    session: AsyncSession, case: ReviewCase, export_id: str
) -> ReviewExport:
    export = await session.get(ReviewExport, export_id)
    if (
        export is None
        or export.case_id != case.id
        or export.organization_id != case.organization_id
    ):
        raise NotFoundError("核验单导出不存在", resource="review_export", resource_id=export_id)
    return export


async def audit_download(
    session: AsyncSession, case: ReviewCase, export: ReviewExport, *, user_id: int
) -> None:
    _audit(
        session,
        user_id,
        "review_export_download",
        case,
        {"export_id": export.id, "payload_sha256": export.payload_sha256},
    )
    await session.commit()


def _cell(value: Any) -> str:
    """表格单元转义：先折叠空白（含 \\r/\\t），再转义竖线，避免破坏行列。"""
    return _flatten(value).replace("|", "\\|")


def _flatten(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split())


def _format_time(value: Any) -> str:
    """时间统一为显式 UTC；数据库读回的 naive 时间是 UTC，不能裸输出误导为本地时间。"""
    text = _flatten(value)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.strftime("%Y-%m-%d %H:%M:%S UTC")


def _label(mapping: dict[str, str], value: Any) -> str:
    text = "" if value is None else str(value)
    return mapping.get(text, text)


def _labels(mapping: dict[str, str], values: Any) -> str:
    if not isinstance(values, (list, tuple)):
        return _flatten(_label(mapping, values))
    return "、".join(_flatten(_label(mapping, value)) for value in values)


def _locator_text(clause_no: Any, section_no: Any, page_number: Any) -> str:
    parts: list[str] = []
    if isinstance(clause_no, (list, tuple)) and clause_no:
        parts.append("第 " + ",".join(_flatten(n) for n in clause_no) + " 条")
    if isinstance(section_no, (list, tuple)) and section_no:
        parts.append("第 " + ",".join(_flatten(n) for n in section_no) + " 节")
    if page_number:
        parts.append(f"第 {_flatten(page_number)} 页")
    return " · ".join(parts)


def _fact_reason(reason: Any) -> str:
    """组合码（如 ``unit_unclear+multiple_durations``）逐段翻译，未知段透传。"""
    parts = [part.strip() for part in str(reason or "").split("+") if part.strip()]
    return " · ".join(_flatten(FACT_REASON_LABELS.get(part, part)) for part in parts)


def _unit_text(unit_raw: Any, unit: Any) -> str:
    if unit_raw:
        return _flatten(unit_raw)
    if unit:
        return _flatten(UNIT_LABELS.get(str(unit), str(unit)))
    return ""


def _fact_values(fact: dict[str, Any]) -> str:
    candidates = fact.get("candidates")
    if isinstance(candidates, list) and candidates:
        raws: list[str] = []
        for candidate in candidates:
            if isinstance(candidate, dict):
                raw = candidate.get("raw")
                if raw:
                    raws.append(_flatten(raw))
                elif candidate.get("value") is not None:
                    unit = _unit_text(candidate.get("unit_raw"), candidate.get("unit"))
                    raws.append(_flatten(f"{candidate.get('value')} {unit}"))
            elif candidate is not None:
                raws.append(_flatten(candidate))
        if raws:
            return " / ".join(raws)
    parts: list[str] = []
    if fact.get("value") is not None:
        unit = _unit_text(fact.get("unit_raw"), fact.get("unit"))
        parts.append(_flatten(f"{fact.get('value')} {unit}"))
    for key, label in (
        ("events", "起算事件"),
        ("terms", "命中词"),
        ("cross_refs", "交叉引用"),
    ):
        values = fact.get(key)
        if isinstance(values, list) and values:
            parts.append(f"{label}：{'、'.join(_flatten(value) for value in values)}")
    return " · ".join(parts)


def _fact_text(fact: Any) -> str:
    """单个侧文本事实 → 业务文案；歧义只列候选与原因，不替用户选值。"""
    if not isinstance(fact, dict):
        return "—"
    status = str(fact.get("status") or "")
    if status == "identified":
        return _fact_values(fact) or "无"
    if status == "ambiguous":
        text = _fact_reason(fact.get("reason")) or "未识别"
        candidates = _fact_values(fact)
        if candidates:
            text += f" · 候选：{candidates}"
        return text
    if status == "not_found":
        return "未识别"
    return _label(FACT_STATUS_LABELS, status) or "—"


def _fact_signature(fact: Any) -> tuple[str, tuple[str, ...], str] | None:
    """事实的集合语义签名：候选/词元/引用顺序不同不算事实变化。"""
    if not isinstance(fact, dict):
        return None
    values: list[str] = []
    candidates = fact.get("candidates")
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, dict):
                raw = candidate.get("raw")
                if raw:
                    values.append(_flatten(raw))
                elif candidate.get("value") is not None:
                    unit = _unit_text(candidate.get("unit_raw"), candidate.get("unit"))
                    values.append(_flatten(f"{candidate.get('value')} {unit}"))
            elif candidate is not None:
                values.append(_flatten(candidate))
    if fact.get("value") is not None:
        unit = _unit_text(fact.get("unit_raw"), fact.get("unit"))
        values.append(_flatten(f"{fact.get('value')} {unit}"))
    for key in ("events", "terms", "cross_refs"):
        raw_values = fact.get(key)
        if isinstance(raw_values, list):
            values.extend(_flatten(value) for value in raw_values if value is not None)
    return (
        str(fact.get("status") or ""),
        tuple(sorted(values)),
        _flatten(fact.get("reason") or ""),
    )


def _fact_locator(fact: Any) -> str:
    if not isinstance(fact, dict):
        return ""
    return _locator_text(fact.get("clause_no"), fact.get("section_no"), fact.get("page_number"))


def _status_summary(items: list[dict[str, Any]]) -> str:
    counts: dict[str, int] = {}
    for item in items:
        status = item.get("status")
        if status:
            counts[str(status)] = counts.get(str(status), 0) + 1
    ordered = list(DECISION_STATUSES) + [key for key in counts if key not in DECISION_STATUSES]
    return " · ".join(
        f"{_label(ITEM_STATUS_LABELS, status)} {counts[status]}"
        for status in ordered
        if counts.get(status)
    )


def render_markdown(payload: dict[str, Any], *, export_meta: dict[str, Any] | None = None) -> str:
    """把导出 payload 渲染为可交付的 Markdown 报告。

    内容只来自快照：事实歧义列出候选与原因，未识别如实标注，不引入第二套真源。
    ``export_meta`` 只含快照行元数据（导出编号/生成时间/哈希），不参与 payload 哈希。
    """
    case = payload["case"]
    documents = payload.get("documents") or {}
    items = payload.get("items") or []
    lines = [f"# 核验单：{_flatten(case.get('title'))}", ""]
    lines.append(f"- 案件：{_flatten(case.get('id'))}")
    if export_meta:
        meta_parts: list[str] = []
        if export_meta.get("export_id"):
            meta_parts.append(f"导出编号：{_flatten(export_meta['export_id'])}")
        if export_meta.get("created_at"):
            meta_parts.append(f"生成时间：{_format_time(export_meta['created_at'])}")
        if export_meta.get("payload_sha256"):
            meta_parts.append(f"快照哈希：{_flatten(export_meta['payload_sha256'])}")
        if meta_parts:
            lines.append("- " + " · ".join(meta_parts))
    lines.append(f"- 清单版本：{_flatten(case.get('checklist_version'))}")
    lines.append(f"- 源文档在建案后发生变化：{'是' if payload.get('source_stale') else '否'}")
    summary = _status_summary(items)
    if summary:
        lines.append(f"- 核验项：{len(items)} 项 · {summary}")
    lines.append("")
    lines.append("## 文档与版本")
    lines.append("| 侧 | 文件 | 版本 | 内容哈希 |")
    lines.append("| --- | --- | --- | --- |")
    for side in REVIEW_SIDES:
        entry = documents.get(side) or {}
        lines.append(
            f"| {_cell(_label(SIDE_LABELS, side))} | {_cell(entry.get('filename') or '')} | "
            f"{_cell(entry.get('version_label') or '')} | {_cell(entry.get('content_sha256') or '')} |"
        )
    lines.append("")
    lines.append("## 核验项")
    lines.append("| 核验项 | 差异类型 | 决策 | 决策人 | 决策时间 | 说明 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in items:
        decided_by = item.get("decided_by")
        lines.append(
            f"| {_cell(_label(ITEM_LABELS, item.get('item_key')))} | "
            f"{_cell(_label(CHANGE_TYPE_LABELS, item.get('change_type')))} | "
            f"{_cell(_label(ITEM_STATUS_LABELS, item.get('status')))} | "
            f"{_cell(f'#{decided_by}' if decided_by is not None else '')} | "
            f"{_cell(_format_time(item.get('decided_at')))} | {_cell(item.get('decision_note') or '')} |"
        )
    lines.append("")
    lines.append("## 逐项核验")
    for item in items:
        change = _label(CHANGE_TYPE_LABELS, item.get("change_type"))
        heading = _flatten(_label(ITEM_LABELS, item.get("item_key")))
        lines.append(f"### {heading}{f'（{_flatten(change)}）' if change else ''}")
        lines.append(f"- 决策：{_flatten(_label(ITEM_STATUS_LABELS, item.get('status')))}")
        decided_by = item.get("decided_by")
        if decided_by is not None:
            lines.append(f"- 决策人：#{_flatten(decided_by)}")
        if item.get("decided_at"):
            lines.append(f"- 决策时间：{_format_time(item.get('decided_at'))}")
        note = item.get("decision_note")
        if note and str(note).strip():
            lines.append(f"- 说明：{_flatten(note)}")
        facts = item.get("facts") or {}
        fact_lines: list[str] = []
        for side in REVIEW_SIDES:
            fact = facts.get(side) if isinstance(facts, dict) else None
            if not isinstance(fact, dict):
                continue
            locator = _fact_locator(fact)
            suffix = f"（{locator}）" if locator else ""
            fact_lines.append(f"- {SIDE_LABELS[side]}：{_fact_text(fact)}{suffix}")
        if fact_lines:
            lines.append("")
            lines.append("**文本事实**")
            lines.extend(fact_lines)
        base_fact = facts.get("base") if isinstance(facts, dict) else None
        revised_fact = facts.get("revised") if isinstance(facts, dict) else None
        base_signature = _fact_signature(base_fact)
        revised_signature = _fact_signature(revised_fact)
        if (
            item.get("change_type") in ("modified", "renumbered")
            and base_signature is not None
            and base_signature == revised_signature
        ):
            # 与工作台口径一致：差异来自正文/条号时，不拿相同的提取值硬造对比。
            lines.append("")
            lines.append("- 提示：文本事实一致；差异来自正文或条号变化。")
        lines.append("")
    lines.append("## 证据（原文引用）")
    for item in items:
        lines.append(f"### {_flatten(_label(ITEM_LABELS, item.get('item_key')))}")
        has_evidence = False
        evidence = item.get("evidence") or {}
        for side in REVIEW_SIDES:
            for row in evidence.get(side) or []:
                has_evidence = True
                locator = _locator_text(
                    row.get("clause_no"), row.get("section_no"), row.get("page_number")
                )
                stale = " · 快照已过期（当前文档已变化）" if row.get("stale") else ""
                lines.append(
                    f"- [{SIDE_LABELS[side]}] {locator or '定位未记录'}{stale}："
                    f"{_flatten(row.get('quote'))}"
                )
        if not has_evidence:
            lines.append("- （无已选证据）")
        lines.append("")
    coverage = payload.get("coverage") or {}
    lines.append("## 覆盖范围")
    lines.append(f"- 固定核验项：{_labels(ITEM_LABELS, coverage.get('checklist_items')) or '无'}")
    lines.append(f"- 未识别项：{_labels(ITEM_LABELS, coverage.get('unidentified_items')) or '无'}")
    lines.append(
        f"- 需人工处理项：{_labels(ITEM_LABELS, coverage.get('needs_human_items')) or '无'}"
    )
    lines.append(
        "- 无已选证据项："
        f"{_labels(ITEM_LABELS, coverage.get('items_without_selected_evidence')) or '无'}"
    )
    scan = coverage.get("scan") or {}
    if scan:
        truncated = any(
            (entry or {}).get("truncated") for entry in scan.values() if isinstance(entry, dict)
        )
        lines.append(f"- 分析扫描截断：{'是' if truncated else '否'}")
    lines.append("")
    lines.append(f"> {payload.get('disclaimer') or DISCLAIMER}")
    return "\n".join(lines) + "\n"
