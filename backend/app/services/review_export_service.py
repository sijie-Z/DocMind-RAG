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
    """表格单元转义：竖线破坏列、换行破坏行。"""
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _flatten(value: Any) -> str:
    text = "" if value is None else str(value)
    return " ".join(text.split())


def render_markdown(payload: dict[str, Any]) -> str:
    """把导出 payload 渲染为 Markdown；内容只来自快照，不引入第二套真源。"""
    case = payload["case"]
    lines = [f"# 核验单：{_flatten(case['title'])}", ""]
    lines.append(f"- 案件：{case['id']}")
    lines.append(f"- 清单版本：{case['checklist_version']}")
    lines.append(f"- 源文档在建案后发生变化：{'是' if payload['source_stale'] else '否'}")
    lines.append("")
    lines.append("## 文档与版本")
    lines.append("| 侧 | 文件 | 版本 | 内容哈希 |")
    lines.append("| --- | --- | --- | --- |")
    for side in REVIEW_SIDES:
        entry = payload["documents"].get(side) or {}
        lines.append(
            f"| {_cell(side)} | {_cell(entry.get('filename') or '')} | "
            f"{_cell(entry.get('version_label') or '')} | {_cell(entry.get('content_sha256') or '')} |"
        )
    lines.append("")
    lines.append("## 核验项")
    lines.append("| 核验项 | 差异类型 | 决策 | 决策人 | 决策时间 | 说明 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for item in payload["items"]:
        lines.append(
            f"| {_cell(item['item_key'])} | {_cell(item.get('change_type') or '')} | "
            f"{_cell(item['status'])} | {_cell(item.get('decided_by') or '')} | "
            f"{_cell(item.get('decided_at') or '')} | {_cell(item.get('decision_note') or '')} |"
        )
    lines.append("")
    lines.append("## 证据（原文引用）")
    for item in payload["items"]:
        lines.append(f"### {item['item_key']}")
        has_evidence = False
        for side in REVIEW_SIDES:
            for row in item["evidence"].get(side) or []:
                has_evidence = True
                clause = row.get("clause_no") or []
                locator = f"第{','.join(str(n) for n in clause)}条" if clause else "未识别条号"
                page = row.get("page_number")
                lines.append(
                    f"- [{side}] {locator}{' 第' + str(page) + '页' if page else ''}："
                    f"{_flatten(row['quote'])}"
                )
        if not has_evidence:
            lines.append("- （无已选证据）")
        lines.append("")
    coverage = payload["coverage"]
    lines.append("## 覆盖范围")
    lines.append(f"- 固定核验项：{', '.join(coverage['checklist_items'])}")
    lines.append(f"- 未识别项：{', '.join(coverage['unidentified_items']) or '无'}")
    lines.append(f"- 需人工处理项：{', '.join(coverage['needs_human_items']) or '无'}")
    lines.append(
        f"- 无已选证据项：{', '.join(coverage['items_without_selected_evidence']) or '无'}"
    )
    scan = coverage.get("scan") or {}
    if scan:
        truncated = any(
            (entry or {}).get("truncated") for entry in scan.values() if isinstance(entry, dict)
        )
        lines.append(f"- 分析扫描截断：{'是' if truncated else '否'}")
    lines.append("")
    lines.append(f"> {payload['disclaimer']}")
    return "\n".join(lines) + "\n"
