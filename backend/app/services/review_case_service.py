"""核验案件、核验项与证据快照（合同核验场景 Batch 2）。

口径（与 ``_scratch/orchestration/audit_contract_review_gap_20261027.md`` 对齐）：

- 建案只接受同组织、角色正确、同版本族且版本序递增的三份文档；任一份
  readiness 不是 ``reviewable``（legacy/未索引/解析失败/缺核验身份）时案件以
  ``blocked`` 落库并带原因码；``blocked`` 不允许选择证据，更不可能完成；
- 证据快照只从 ``DocumentChunk.original_text`` 抓取（写入方标记必须是
  ``parser_original``），行内固化 quote / quote_sha256 / document_sha256 与
  clause/page/字符区间；文档 rebuild 后按实时正文哈希比对标注
  ``live_source_stale``，绝不用新正文悄悄替换快照；
- 建案时的候选证据是确定性关键词发现，不是条款抽取/对齐（Batch 3 才做），
  也不是法律判断；人工改选/确认在后续批次的工作台完成；
- 本模块不依赖 ES/LLM，全部从 DB 分块行读取。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_user_org_id
from app.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.document import Document, DocumentChunk
from app.models.review import (
    REVIEW_CASE_STATUSES,
    REVIEW_ITEM_KEYS,
    REVIEW_SIDES,
    ReviewCase,
    ReviewEvidence,
    ReviewItem,
)
from app.models.user import User
from app.rag.source_provenance import PARSER_ORIGINAL, SourceProvenanceUnavailableError
from app.services.clause_refs import extract_clause_fields
from app.services.review_document_service import (
    MAX_EVIDENCE_SCAN_CHUNKS,
    READINESS_REVIEWABLE,
    chunk_original_text,
    compute_readiness,
    get_review_meta,
    iter_chunks,
    meta_to_dict,
)

CHECKLIST_VERSIONS = ("v1",)
# 允许选择/改选证据的案件状态；blocked 必须先把文档/身份问题修好。
SELECTABLE_CASE_STATUSES = ("ready", "in_review")
# 每个（核验项, 文档侧）建案时最多抓多少条候选证据：候选只是发现线索，
# 人工选择由后续工作台完成；上限保证建案扫描有界。
EVIDENCE_CANDIDATES_PER_ITEM_SIDE = 3
# 人工可选择/改选的证据角色（candidate 只由建案抓取产生）。
HUMAN_EVIDENCE_ROLES = ("main", "condition", "exception", "cross_ref")
# 关键词是「候选发现」而非条款抽取：宁多勿漏，只做原文 contains 匹配，
# 命中结果一律以 candidate 身份落库，不宣称为某个条款的定论。
ITEM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "payment_term": ("付款", "支付", "账期", "价款", "货款", "结算"),
    "start_condition": ("起算", "起计", "自收到", "收到发票", "对账单", "验收合格"),
    "acceptance_invoice": ("验收", "发票", "对账"),
    "exception": ("例外", "暂缓", "除外", "另行约定", "补充协议", "不可抗力"),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _quote_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _case_document_id(case: ReviewCase, side: str) -> str:
    return {
        "policy": case.policy_document_id,
        "base": case.base_document_id,
        "revised": case.revised_document_id,
    }[side]


async def get_case_for_user(session: AsyncSession, current_user: User, case_id: str) -> ReviewCase:
    """按 id 加载案件并做组织隔离；非超管跨组织一律 403（与文档口径一致）。"""
    case = await session.get(ReviewCase, case_id)
    if case is None:
        raise NotFoundError("核验案件不存在", resource="review_case", resource_id=case_id)
    if not getattr(current_user, "is_superuser", False):
        org_id = get_user_org_id(current_user)
        if org_id is None or case.organization_id != org_id:
            raise AuthorizationError("无权访问该核验案件")
    return case


async def create_case(
    session: AsyncSession,
    *,
    docs: dict[str, Document],
    title: str,
    checklist_version: str,
    created_by: int,
    assignee_id: int | None = None,
) -> ReviewCase:
    """建案：校验三份文档身份/readiness，生成 4 个固定核验项并快照候选证据。

    校验失败不让请求失败，而是把案件以 ``blocked`` 落库并记录原因码——
    用户需要看到「为什么不能核验」，而不是一个报错后什么都没有。
    """
    if not title or not title.strip():
        raise ValidationError("案件标题不能为空", field="title")
    if checklist_version not in CHECKLIST_VERSIONS:
        raise ValidationError(
            f"未知核验清单版本: {checklist_version!r}",
            field="checklist_version",
        )
    if set(docs) != set(REVIEW_SIDES):
        raise ValidationError("必须同时提供 policy/base/revised 三份文档")
    document_ids = [docs[side].id for side in REVIEW_SIDES]
    if len(set(document_ids)) != len(REVIEW_SIDES):
        raise ValidationError("政策/基准/修订三份文档必须互不相同")
    org_id = docs["base"].organization_id
    if any(doc.organization_id != org_id for doc in docs.values()):
        raise ValidationError("三份文档必须属于同一组织")
    if assignee_id is not None and await session.get(User, assignee_id) is None:
        raise ValidationError("assignee_id 指向的用户不存在", field="assignee_id")

    metas = {side: await get_review_meta(session, docs[side].id) for side in REVIEW_SIDES}
    readiness = {side: await compute_readiness(session, docs[side]) for side in REVIEW_SIDES}

    blocking_reasons = _collect_blocking_reasons(metas, readiness)
    snapshot = {
        side: _document_snapshot(docs[side], metas[side], readiness[side]) for side in REVIEW_SIDES
    }

    case = ReviewCase(
        id=str(uuid.uuid4()),
        organization_id=org_id,
        title=title.strip(),
        policy_document_id=docs["policy"].id,
        base_document_id=docs["base"].id,
        revised_document_id=docs["revised"].id,
        checklist_version=checklist_version,
        status="blocked" if blocking_reasons else "ready",
        blocking_reasons=blocking_reasons,
        readiness_snapshot=snapshot,
        created_by=created_by,
        assignee_id=assignee_id,
        lock_version=1,
    )
    session.add(case)
    items = {}
    for ordinal, item_key in enumerate(REVIEW_ITEM_KEYS, start=1):
        item = ReviewItem(
            id=str(uuid.uuid4()),
            case_id=case.id,
            item_key=item_key,
            ordinal=ordinal,
            status="pending",
        )
        session.add(item)
        items[item_key] = item
    if case.status == "ready":
        await _capture_candidates(session, case, items, docs, metas)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("核验案件创建冲突，请重试", error_code="REVIEW_CASE_CONFLICT") from exc
    await session.refresh(case)
    return case


def _collect_blocking_reasons(metas: dict[str, Any], readiness: dict[str, Any]) -> list[str]:
    """把身份/readiness 问题统一成 ``side:reason`` 原因码（保持顺序去重）。"""
    reasons: list[str] = []

    def _add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    for side in REVIEW_SIDES:
        meta = metas[side]
        if meta is None:
            _add(f"{side}:missing_version_meta")
        else:
            allowed_roles = ("policy", "template") if side == "policy" else ("contract",)
            if meta.doc_role not in allowed_roles:
                _add(f"{side}:wrong_role")
        if readiness[side].status != READINESS_REVIEWABLE:
            for reason in readiness[side].blocking_reasons:
                _add(f"{side}:{reason}")

    base_meta, revised_meta = metas["base"], metas["revised"]
    if (
        base_meta is not None
        and revised_meta is not None
        and base_meta.doc_role == "contract"
        and revised_meta.doc_role == "contract"
    ):
        if base_meta.family_key != revised_meta.family_key:
            _add("family_mismatch")
        elif (
            base_meta.version_seq is not None
            and revised_meta.version_seq is not None
            and base_meta.version_seq >= revised_meta.version_seq
        ):
            _add("version_order")
    return reasons


def _document_snapshot(doc: Document, meta: Any, readiness: Any) -> dict[str, Any]:
    meta_dict = meta_to_dict(meta)
    return {
        "document_id": doc.id,
        "filename": doc.filename,
        "doc_role": meta_dict["doc_role"] if meta_dict else None,
        "family_key": meta_dict["family_key"] if meta_dict else None,
        "version_label": meta_dict["version_label"] if meta_dict else None,
        "version_seq": meta_dict["version_seq"] if meta_dict else None,
        "effective_from": meta_dict["effective_from"] if meta_dict else None,
        "effective_to": meta_dict["effective_to"] if meta_dict else None,
        "content_sha256": meta_dict["content_sha256"] if meta_dict else None,
        "readiness": {
            "status": readiness.status,
            "blocking_reasons": readiness.blocking_reasons,
            "warnings": readiness.warnings,
            "total_chunks": readiness.total_chunks,
            "original_text_chunks": readiness.original_text_chunks,
            "page_number_chunks": readiness.page_number_chunks,
            "clause_candidate_chunks": readiness.clause_candidate_chunks,
            "legacy_chunk_count": readiness.legacy_chunk_count,
        },
    }


def _new_evidence_row(
    *,
    case: ReviewCase,
    item_id: str,
    side: str,
    role: str,
    selected: bool,
    document_id: str,
    meta: Any,
    chunk_id: str,
    chunk_index: int,
    quote: str,
    page_number: int | None,
    char_start: int | None,
    char_end: int | None,
) -> ReviewEvidence:
    fields = extract_clause_fields(quote)
    return ReviewEvidence(
        id=str(uuid.uuid4()),
        case_id=case.id,
        item_id=item_id,
        side=side,
        role=role,
        selected=selected,
        document_id=document_id,
        doc_role=meta.doc_role,
        doc_version_label=meta.version_label,
        doc_version_seq=meta.version_seq,
        document_sha256=meta.content_sha256,
        chunk_id_at_capture=chunk_id,
        chunk_index=chunk_index,
        clause_no=fields["clause_no"],
        section_no=fields["section_no"],
        page_number=page_number,
        char_start=char_start,
        char_end=char_end,
        quote_text=quote,
        quote_sha256=_quote_sha256(quote),
        text_provenance=PARSER_ORIGINAL,
        captured_at=_now(),
    )


async def _capture_candidates(
    session: AsyncSession,
    case: ReviewCase,
    items: dict[str, ReviewItem],
    docs: dict[str, Document],
    metas: dict[str, Any],
) -> None:
    """建案时做一次有界的关键词发现，把命中原文快照成 candidate 证据行。

    只对 reviewable 的三份文档执行（ready 案件才走到这里），每份文档一次
    顺序扫描，命中数达到上限或扫到硬上限即停。
    """

    async def _scan_side(side: str) -> None:
        counters = {key: EVIDENCE_CANDIDATES_PER_ITEM_SIDE for key in REVIEW_ITEM_KEYS}
        async for chunk in iter_chunks(session, docs[side].id, max_chunks=MAX_EVIDENCE_SCAN_CHUNKS):
            quote = chunk_original_text(chunk)
            if quote is None:
                continue
            lowered = quote.lower()
            for item_key in REVIEW_ITEM_KEYS:
                if counters[item_key] <= 0:
                    continue
                if not any(keyword in lowered for keyword in ITEM_KEYWORDS[item_key]):
                    continue
                counters[item_key] -= 1
                session.add(
                    _new_evidence_row(
                        case=case,
                        item_id=items[item_key].id,
                        side=side,
                        role="candidate",
                        selected=False,
                        document_id=docs[side].id,
                        meta=metas[side],
                        chunk_id=chunk.id,
                        chunk_index=chunk.chunk_index,
                        quote=quote,
                        page_number=chunk.page_number,
                        char_start=chunk.start_pos,
                        char_end=chunk.end_pos,
                    )
                )
            if all(value <= 0 for value in counters.values()):
                return

    # 顺序执行：SQLite 单连接/网关场景下并发扫描没有收益，还会让 ORM 会话
    # 的加分页游标相互穿插。三份文档的扫描规模由 MAX_EVIDENCE_SCAN_CHUNKS 限定。
    for side in REVIEW_SIDES:
        await _scan_side(side)


async def select_evidence(
    session: AsyncSession,
    case: ReviewCase,
    item: ReviewItem,
    *,
    side: str,
    chunk_id: str,
    role: str,
    selected: bool,
) -> ReviewEvidence:
    """人工选择/改选某条证据：从 realtime 分块抓原文并快照（legacy 显式拒绝）。"""
    if case.status not in SELECTABLE_CASE_STATUSES:
        raise ConflictError(
            "当前案件状态不允许选择证据",
            error_code="REVIEW_CASE_NOT_READY",
            detail={"status": case.status},
        )
    if side not in REVIEW_SIDES:
        raise ValidationError(f"未知证据侧: {side!r}", field="side")
    if role not in HUMAN_EVIDENCE_ROLES:
        raise ValidationError(f"未知证据角色: {role!r}", field="role")

    document_id = _case_document_id(case, side)
    document = await session.get(Document, document_id)
    if document is None or document.organization_id != case.organization_id:
        raise ConflictError(
            "案件引用的文档已不存在，无法再选择证据",
            error_code="REVIEW_DOCUMENT_UNAVAILABLE",
            detail={"document_id": document_id, "side": side},
        )
    chunk = await session.get(DocumentChunk, chunk_id)
    if chunk is None or chunk.document_id != document_id:
        raise NotFoundError("文档分块不存在", resource="document_chunk", resource_id=chunk_id)
    quote = chunk_original_text(chunk)
    if quote is None:
        raise SourceProvenanceUnavailableError([document_id], unverified_count=1)
    meta = await get_review_meta(session, document_id)
    if meta is None:
        raise ConflictError(
            "文档缺少核验身份，无法快照证据",
            error_code="REVIEW_META_MISSING",
            detail={"document_id": document_id},
        )

    existing = (
        await session.execute(
            select(ReviewEvidence).where(
                ReviewEvidence.item_id == item.id,
                ReviewEvidence.side == side,
                ReviewEvidence.chunk_id_at_capture == chunk_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.selected = selected
        if selected:
            existing.role = role
        existing.updated_at = _now()
        row = existing
    else:
        row = _new_evidence_row(
            case=case,
            item_id=item.id,
            side=side,
            role=role,
            selected=selected,
            document_id=document_id,
            meta=meta,
            chunk_id=chunk.id,
            chunk_index=chunk.chunk_index,
            quote=quote,
            page_number=chunk.page_number,
            char_start=chunk.start_pos,
            char_end=chunk.end_pos,
        )
        session.add(row)
    try:
        await session.commit()
    except IntegrityError as exc:
        # 并发请求同时选择同一分块：DB 唯一约束是最后防线，映射为 409。
        await session.rollback()
        raise ConflictError("证据选择冲突，请重试", error_code="REVIEW_EVIDENCE_CONFLICT") from exc
    await session.refresh(row)
    return row


async def get_item_for_case(session: AsyncSession, case: ReviewCase, item_id: str) -> ReviewItem:
    item = await session.get(ReviewItem, item_id)
    if item is None or item.case_id != case.id:
        raise NotFoundError("核验项不存在", resource="review_item", resource_id=item_id)
    return item


async def list_cases(
    session: AsyncSession,
    *,
    organization_id: int,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    if status is not None and status not in REVIEW_CASE_STATUSES:
        raise ValidationError(f"未知案件状态: {status!r}", field="status")
    conditions = [ReviewCase.organization_id == organization_id]
    if status is not None:
        conditions.append(ReviewCase.status == status)
    total = int(
        (await session.execute(select(func.count(ReviewCase.id)).where(*conditions))).scalar() or 0
    )
    rows = (
        (
            await session.execute(
                select(ReviewCase)
                .where(*conditions)
                .order_by(ReviewCase.created_at.desc(), ReviewCase.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [_case_summary(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _case_summary(case: ReviewCase) -> dict[str, Any]:
    snapshot = case.readiness_snapshot or {}
    return {
        "id": case.id,
        "title": case.title,
        "status": case.status,
        "checklist_version": case.checklist_version,
        "policy_document_id": case.policy_document_id,
        "base_document_id": case.base_document_id,
        "revised_document_id": case.revised_document_id,
        "base_version_label": (snapshot.get("base") or {}).get("version_label"),
        "revised_version_label": (snapshot.get("revised") or {}).get("version_label"),
        "blocking_reasons": case.blocking_reasons or [],
        "created_by": case.created_by,
        "assignee_id": case.assignee_id,
        "created_at": _iso(case.created_at),
        "completed_at": _iso(case.completed_at),
    }


async def case_detail(session: AsyncSession, case: ReviewCase) -> dict[str, Any]:
    """案件详情：文档快照/readiness + 4 个核验项 + 证据快照 + 实时漂移标记。"""
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
    evidence_rows = (
        (
            await session.execute(
                select(ReviewEvidence)
                .where(ReviewEvidence.case_id == case.id)
                .order_by(ReviewEvidence.chunk_index.asc(), ReviewEvidence.id.asc())
            )
        )
        .scalars()
        .all()
    )

    stale_by_evidence = await _stale_evidence_ids(session, evidence_rows)
    documents = await _document_states(session, case)

    evidence_by_item: dict[str, dict[str, list[dict[str, Any]]]] = {
        item.id: {side: [] for side in REVIEW_SIDES} for item in items
    }
    for row in evidence_rows:
        bucket = evidence_by_item.get(row.item_id)
        if bucket is None or row.side not in bucket:
            continue
        bucket[row.side].append(evidence_to_dict(row, stale=row.id in stale_by_evidence))

    live_source_stale = bool(stale_by_evidence) or any(
        entry["missing"] for entry in documents.values()
    )
    return {
        "id": case.id,
        "title": case.title,
        "status": case.status,
        "checklist_version": case.checklist_version,
        "organization_id": case.organization_id,
        "blocking_reasons": case.blocking_reasons or [],
        "created_by": case.created_by,
        "assignee_id": case.assignee_id,
        "created_at": _iso(case.created_at),
        "updated_at": _iso(case.updated_at),
        "completed_at": _iso(case.completed_at),
        "completed_by": case.completed_by,
        "lock_version": case.lock_version,
        "documents": documents,
        "items": [_item_to_dict(item, evidence_by_item.get(item.id, {})) for item in items],
        "live_source_stale": live_source_stale,
        "stale_evidence_count": len(stale_by_evidence),
    }


async def _document_states(session: AsyncSession, case: ReviewCase) -> dict[str, dict[str, Any]]:
    snapshot = case.readiness_snapshot or {}
    states: dict[str, dict[str, Any]] = {}
    for side in REVIEW_SIDES:
        entry = dict(snapshot.get(side) or {})
        document_id = _case_document_id(case, side)
        entry["document_id"] = document_id
        document = await session.get(Document, document_id)
        entry["missing"] = document is None
        entry["live_status"] = document.status.value if document is not None else None
        if document is not None:
            entry["live_filename"] = document.filename
        states[side] = entry
    return states


async def _stale_evidence_ids(
    session: AsyncSession, evidence_rows: list[ReviewEvidence]
) -> set[str]:
    """实时正文与快照不一致的证据 id 集合。

    判定：捕获时的 chunk 行已不存在（rebuild/删除会重新生成 chunk id）、
    原文为空、或原文的 sha256 与 quote_sha256 不等。快照本身永不改写。
    """
    if not evidence_rows:
        return set()
    chunk_ids = {row.chunk_id_at_capture for row in evidence_rows}
    live = {
        row.id: row.original_text
        for row in (
            await session.execute(
                select(DocumentChunk.id, DocumentChunk.original_text).where(
                    DocumentChunk.id.in_(chunk_ids)
                )
            )
        ).all()
    }
    stale: set[str] = set()
    for row in evidence_rows:
        text = live.get(row.chunk_id_at_capture)
        if text is None or _quote_sha256(text) != row.quote_sha256:
            stale.add(row.id)
    return stale


def evidence_to_dict(row: ReviewEvidence, *, stale: bool) -> dict[str, Any]:
    return {
        "id": row.id,
        "item_id": row.item_id,
        "side": row.side,
        "role": row.role,
        "selected": row.selected,
        "document_id": row.document_id,
        "doc_role": row.doc_role,
        "doc_version_label": row.doc_version_label,
        "doc_version_seq": row.doc_version_seq,
        "document_sha256": row.document_sha256,
        "chunk_id_at_capture": row.chunk_id_at_capture,
        "chunk_index": row.chunk_index,
        "clause_no": row.clause_no,
        "section_no": row.section_no,
        "page_number": row.page_number,
        "char_start": row.char_start,
        "char_end": row.char_end,
        "quote": row.quote_text,
        "quote_sha256": row.quote_sha256,
        "text_provenance": row.text_provenance,
        "captured_at": _iso(row.captured_at),
        "stale": stale,
    }


def _item_to_dict(item: ReviewItem, evidence: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {
        "id": item.id,
        "item_key": item.item_key,
        "ordinal": item.ordinal,
        "change_type": item.change_type,
        "status": item.status,
        "facts": item.facts,
        "decided_by": item.decided_by,
        "decided_at": _iso(item.decided_at),
        "decision_note": item.decision_note,
        "evidence_count": sum(len(rows) for rows in evidence.values()),
        "selected_evidence_count": sum(
            1 for rows in evidence.values() for row in rows if row["selected"]
        ),
        "evidence": evidence,
    }
