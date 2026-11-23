"""合同审查模板/playbook 用户可编辑库 + 版本历史 + 只读 diff（竞品批次五）。

边界（与 ``review_rule_snapshot`` / ``review_case_service`` 的分工）：

- 模板只是**规则内容的可编辑容器**：每次保存新增一条不可变版本行
  （``review_template_version``），旧版本永不改写；模板头只维护名称/说明/
  状态与最新版本号。
- 建案时可指定 ``template_id`` + ``template_version_no``：规则项从该版本
  读取，并**继续**经由 ``attach_snapshot`` 固化为不可变的 ``ReviewRuleSet``
  快照。模板/版本的后续编辑、归档都不会改写任何历史案件 —— 案件读取/分析/
  导出仍以快照为唯一真源。
- 模板编辑**不触碰**确定性判定算法：版本行只保存 checklist 形状的
  （key/keywords/label/configurable），专用抽取器（付款期限/起算/验收/
  例外）的行为完全不变；模板新增项与 ``REVIEW_CHECKLIST_JSON`` 追加项同口径，
  走通用关键词抽取器。
- 不提供物理删除：只有归档（``status='archived'``）。删除策略见模型注释。

安全：所有读写按组织隔离；跨组织一律按「不存在」处理（404，不回显存在性）；
输入在服务层二次校验（schema 已做一次），并施加项数/关键词数/长度上限；
审计只记 org/模板 id/版本号/项数/哈希前缀等元数据，绝不记规则正文。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ConflictError, NotFoundError, ValidationError
from app.models.review import (
    ReviewCase,
    ReviewTemplate,
    ReviewTemplateVersion,
)
from app.services.review_checklist import (
    DEFAULT_ITEM_KEYWORDS,
    KEY_PATTERN,
    ChecklistItem,
    load_checklist,
)
from app.services.review_rule_snapshot import RULE_SNAPSHOT_SCHEMA_VERSION

# ── 上限（与 schema 的 Field 约束同值；服务层重复校验，防内部调用绕过 schema）──
MAX_ITEMS_PER_VERSION = 50
MAX_KEYWORDS_PER_ITEM = 30
MAX_KEYWORD_CHARS = 64
MAX_LABEL_CHARS = 100
MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
MAX_CHANGE_NOTE_CHARS = 200
MAX_PAYLOAD_BYTES = 64 * 1024
# schema 层前置请求上限：UTF-8 中文按 3 字节、预留 JSON 结构开销，
# 避免 schema 合法但 canonical payload 超 64 KiB 的请求先落模板头再失败。
MAX_TEMPLATE_REQUEST_KEYWORD_BYTES = 48 * 1024
MAX_VERSIONS_PER_TEMPLATE = 100
MAX_TEMPLATES_PER_ORG = 200

TEMPLATE_STATUS_ACTIVE = "active"
TEMPLATE_STATUS_ARCHIVED = "archived"

BUILTIN_EXTRACTOR_KEYS = frozenset(
    {"payment_term", "start_condition", "acceptance_invoice", "exception"}
)
"""专用抽取器覆盖的内置项。模板版本必须原样包含这四项且关键词不得改动 ——
否则模板编辑会间接改变确定性判定算法（例如把「付款」关键词替换/删除，
让付款项走不到专用抽取器或改变候选证据抓取）。模板只能在其后追加自定义项。"""


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def normalized_builtin_items() -> list[dict[str, Any]]:
    """四个内置项的规范形状（服务端唯一真源；客户端不传、也不能改）。"""
    return [
        {
            "key": key,
            "keywords": list(keywords),
            "label": "",
            "configurable": False,
        }
        for key, keywords in DEFAULT_ITEM_KEYWORDS.items()
    ]


def normalize_custom_items(
    raw_items: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> list[dict]:
    """校验并规范化**自定义追加项**（内置 4 项由服务端自动补齐）。

    规则来自 ``REVIEW_CHECKLIST_JSON`` 的既有约束（key 格式、非空关键词），
    外加项数/关键词数/长度上限；内置 key 不允许出现在自定义项里 —— 内置项
    的关键词由确定性抽取器固定，模板不能改。空列表合法（模板=内置基线）。
    """
    normalized = _normalize_entries(raw_items or [])
    if len(normalized) + len(BUILTIN_EXTRACTOR_KEYS) > MAX_ITEMS_PER_VERSION:
        raise ValidationError(
            f"模板版本最多 {MAX_ITEMS_PER_VERSION} 个核验项（含内置 {len(BUILTIN_EXTRACTOR_KEYS)} 项）",
            field="items",
            expected=MAX_ITEMS_PER_VERSION,
            found=len(normalized) + len(BUILTIN_EXTRACTOR_KEYS),
        )
    builtin_hit = [entry["key"] for entry in normalized if entry["key"] in BUILTIN_EXTRACTOR_KEYS]
    if builtin_hit:
        raise ValidationError(
            "内置核验项由确定性抽取器固定，不能作为自定义项提交",
            field="items.key",
            keys=builtin_hit,
        )
    return normalized


def compose_version_items(
    custom_items: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
) -> list[dict[str, Any]]:
    """最终版本内容 = 内置基线（原样） + 自定义项。"""
    return [*normalized_builtin_items(), *normalize_custom_items(custom_items)]


def normalize_items(raw_items: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict]:
    """校验**完整**规则项列表（内置项必须原样存在，供 bootstrap/内部使用）。

    输出形状与 ``review_rule_snapshot.build_snapshot_payload`` 的 rules 一致
    （key/keywords/label/configurable），因此模板版本可以直接派生快照。

    不变量：版本必须原样包含四个内置项（key、关键词集合与默认值一致，
    label 为空），自定义项只能追加在内置项之后。这样模板永远无法删改确定性
    判定算法覆盖的固定项，模板编辑的语义被限制为「追加要看的项」。
    """
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        raise ValidationError("模板版本至少需要一个核验项", field="items")
    if len(raw_items) > MAX_ITEMS_PER_VERSION:
        raise ValidationError(
            f"模板版本最多 {MAX_ITEMS_PER_VERSION} 个核验项",
            field="items",
            expected=MAX_ITEMS_PER_VERSION,
            found=len(raw_items),
        )
    normalized = _normalize_entries(raw_items)
    seen = {entry["key"] for entry in normalized}
    builtin_hit = seen & BUILTIN_EXTRACTOR_KEYS
    if builtin_hit:
        # 重新核对内置项的默认关键词/label：任何改动都视为绕过判定算法。
        by_key = {entry["key"]: entry for entry in normalized if entry["key"] in builtin_hit}
        for key in sorted(builtin_hit):
            entry = by_key[key]
            if set(entry["keywords"]) != set(DEFAULT_ITEM_KEYWORDS[key]):
                raise ValidationError(
                    f"内置核验项 {key!r} 的关键词由确定性抽取器固定，模板不能修改",
                    field="items.keywords",
                )
            if entry["label"]:
                raise ValidationError(
                    f"内置核验项 {key!r} 的显示名由界面词条维护，模板不能设置 label",
                    field="items.label",
                )
    for entry in normalized:
        entry["configurable"] = entry["key"] not in BUILTIN_EXTRACTOR_KEYS
    missing = BUILTIN_EXTRACTOR_KEYS - seen
    if missing:
        raise ValidationError(
            "模板版本必须包含全部内置核验项（可追加自定义项，但不能删除内置项）",
            field="items.key",
            missing=sorted(missing),
        )
    return normalized


def _normalize_entries(raw_items) -> list[dict]:
    """逐项校验 key/keywords/label 与各项上限（完整列表与自定义项共用）。"""
    terms: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in raw_items:
        if not isinstance(entry, dict):
            raise ValidationError("核验项必须是对象", field="items")
        key = str(entry.get("key") or "").strip()
        if not KEY_PATTERN.match(key):
            raise ValidationError(
                f"非法核验项 key: {key!r}（需匹配 {KEY_PATTERN.pattern}）",
                field="items.key",
            )
        if key in seen:
            raise ValidationError(f"核验项 key 重复: {key!r}", field="items.key")
        seen.add(key)
        raw_keywords = entry.get("keywords")
        if not isinstance(raw_keywords, (list, tuple)) or not raw_keywords:
            raise ValidationError(f"{key!r} 的 keywords 必须是非空数组", field="items.keywords")
        if len(raw_keywords) > MAX_KEYWORDS_PER_ITEM:
            raise ValidationError(
                f"{key!r} 最多 {MAX_KEYWORDS_PER_ITEM} 个关键词",
                field="items.keywords",
                found=len(raw_keywords),
            )
        keywords: list[str] = []
        for word in raw_keywords:
            if not isinstance(word, str):
                raise ValidationError(f"{key!r} 的关键词必须是字符串", field="items.keywords")
            cleaned = word.strip()
            if not cleaned:
                continue
            if len(cleaned) > MAX_KEYWORD_CHARS:
                raise ValidationError(
                    f"{key!r} 的关键词不能超过 {MAX_KEYWORD_CHARS} 字符",
                    field="items.keywords",
                )
            if cleaned not in keywords:
                keywords.append(cleaned)
        if not keywords:
            raise ValidationError(f"{key!r} 的关键词全是空串", field="items.keywords")
        label = str(entry.get("label") or "").strip()
        if len(label) > MAX_LABEL_CHARS:
            raise ValidationError(
                f"{key!r} 的名称不能超过 {MAX_LABEL_CHARS} 字符", field="items.label"
            )
        terms.append(
            {
                "key": key,
                "keywords": keywords,
                "label": label,
                "configurable": True,
            }
        )
    return terms


def items_from_checklist(items: tuple[ChecklistItem, ...]) -> list[dict[str, Any]]:
    """把 loader 结果转成模板版本形状（内置项 configurable=False，追加项 True）。"""
    return [
        {
            "key": item.key,
            "keywords": list(item.keywords),
            "label": item.label,
            "configurable": item.configurable,
        }
        for item in items
    ]


def version_items(row: ReviewTemplateVersion) -> list[dict[str, Any]] | None:
    """读取版本行规则项；哈希不符/形状损坏返回 None（调用方必须拒绝使用）。"""
    payload = row.items_json
    if not isinstance(payload, list) or not payload:
        return None
    if _digest(payload) != row.payload_sha256:
        return None
    if len(payload) != int(row.item_count or 0):
        return None
    return payload


def items_to_checklist(items: list[dict[str, Any]]) -> tuple[ChecklistItem, ...]:
    """版本规则项 → ChecklistItem（供 attachments/候选证据抓取使用）。"""
    return tuple(
        ChecklistItem(
            key=entry["key"],
            keywords=tuple(entry["keywords"]),
            label=entry.get("label") or "",
            configurable=bool(entry.get("configurable", True)),
        )
        for entry in items
    )


def _check_name(name: str) -> str:
    stripped = (name or "").strip()
    if not stripped:
        raise ValidationError("模板名称不能为空", field="name")
    if len(stripped) > MAX_NAME_CHARS:
        raise ValidationError(f"模板名称不能超过 {MAX_NAME_CHARS} 字符", field="name")
    return stripped


def _check_optional_text(value: str | None, *, field: str, limit: int) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > limit:
        raise ValidationError(f"{field} 不能超过 {limit} 字符", field=field)
    return stripped


def template_to_dict(row: ReviewTemplate) -> dict[str, Any]:
    return {
        "id": row.id,
        "organization_id": row.organization_id,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "latest_version": int(row.latest_version or 0),
        "created_by": row.created_by,
        "updated_by": row.updated_by,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
        "archived_by": row.archived_by,
        "lock_version": row.lock_version,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def version_to_dict(row: ReviewTemplateVersion, *, include_items: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": row.id,
        "template_id": row.template_id,
        "version_no": int(row.version_no),
        "version_label": row.version_label,
        "item_count": int(row.item_count),
        "payload_sha256": row.payload_sha256,
        "source": row.source,
        "change_note": row.change_note,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
    if include_items:
        data["items"] = row.items_json
        data["intact"] = version_items(row) is not None
    return data


async def get_template(
    session: AsyncSession, *, organization_id: int, template_id: str
) -> ReviewTemplate:
    """组织内加载模板；不存在/跨组织一律 404（不回显存在性）。"""
    row = await session.get(ReviewTemplate, template_id)
    if row is None or row.organization_id != organization_id:
        raise NotFoundError("核验模板不存在", resource="review_template", resource_id=template_id)
    return row


async def get_template_version(
    session: AsyncSession,
    *,
    organization_id: int,
    template: ReviewTemplate,
    version_no: int,
) -> ReviewTemplateVersion:
    row = (
        await session.execute(
            select(ReviewTemplateVersion).where(
                ReviewTemplateVersion.template_id == template.id,
                ReviewTemplateVersion.version_no == version_no,
                ReviewTemplateVersion.organization_id == organization_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError(
            "核验模板版本不存在",
            resource="review_template_version",
            resource_id=f"{template.id}:{version_no}",
        )
    return row


async def list_templates(
    session: AsyncSession,
    *,
    organization_id: int,
    status: str | None = "active",
    q: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    if status not in (None, TEMPLATE_STATUS_ACTIVE, TEMPLATE_STATUS_ARCHIVED, "all"):
        raise ValidationError(f"未知模板状态: {status!r}", field="status")
    conditions = [ReviewTemplate.organization_id == organization_id]
    if status == TEMPLATE_STATUS_ACTIVE:
        conditions.append(ReviewTemplate.status == TEMPLATE_STATUS_ACTIVE)
    elif status == TEMPLATE_STATUS_ARCHIVED:
        conditions.append(ReviewTemplate.status == TEMPLATE_STATUS_ARCHIVED)
    query_text = (q or "").strip()
    if len(query_text) > MAX_NAME_CHARS:
        raise ValidationError(f"搜索关键词不能超过 {MAX_NAME_CHARS} 个字符", field="q")
    if query_text:
        conditions.append(ReviewTemplate.name.like(f"%{_escape_like(query_text)}%", escape="\\"))
    total = int(
        (await session.execute(select(func.count(ReviewTemplate.id)).where(*conditions))).scalar()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(ReviewTemplate)
                .where(*conditions)
                .order_by(ReviewTemplate.created_at.desc(), ReviewTemplate.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [template_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _next_version_no(session: AsyncSession, template_id: str) -> int:
    current = (
        await session.execute(
            select(func.max(ReviewTemplateVersion.version_no)).where(
                ReviewTemplateVersion.template_id == template_id
            )
        )
    ).scalar()
    return int(current or 0) + 1


async def _ensure_version_budget(session: AsyncSession, template_id: str) -> None:
    count = int(
        (
            await session.execute(
                select(func.count(ReviewTemplateVersion.id)).where(
                    ReviewTemplateVersion.template_id == template_id
                )
            )
        ).scalar()
        or 0
    )
    if count >= MAX_VERSIONS_PER_TEMPLATE:
        raise ValidationError(
            f"单个模板最多保留 {MAX_VERSIONS_PER_TEMPLATE} 个版本",
            field="template_id",
            found=count,
        )


async def create_template(
    session: AsyncSession,
    *,
    organization_id: int,
    created_by: int,
    name: str,
    description: str | None = None,
    commit: bool = True,
) -> ReviewTemplate:
    """创建空模板头（尚无版本）；首版通过 ``create_version`` 写入。

    ``commit=False`` 供「模板头 + 首版」同事务写入使用：调用方必须在同一
    session 上显式 commit，失败时回滚两边，避免留下 latest_version=0 的
    孤立模板。
    """
    name = _check_name(name)
    description = _check_optional_text(
        description, field="description", limit=MAX_DESCRIPTION_CHARS
    )
    budget = int(
        (
            await session.execute(
                select(func.count(ReviewTemplate.id)).where(
                    ReviewTemplate.organization_id == organization_id,
                    ReviewTemplate.status == TEMPLATE_STATUS_ACTIVE,
                )
            )
        ).scalar()
        or 0
    )
    if budget >= MAX_TEMPLATES_PER_ORG:
        raise ValidationError(
            f"每个组织最多 {MAX_TEMPLATES_PER_ORG} 个活跃模板",
            field="name",
            found=budget,
        )
    row = ReviewTemplate(
        id=str(uuid.uuid4()),
        organization_id=organization_id,
        name=name,
        description=description,
        status=TEMPLATE_STATUS_ACTIVE,
        latest_version=0,
        created_by=created_by,
        lock_version=1,
    )
    session.add(row)
    if commit:
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError(
                "同名模板已存在", error_code="REVIEW_TEMPLATE_NAME_CONFLICT"
            ) from exc
        await session.refresh(row)
    return row


async def update_template(
    session: AsyncSession,
    *,
    template: ReviewTemplate,
    name: str | None = None,
    description: str | None = None,
    clear_description: bool = False,
    updated_by: int,
) -> ReviewTemplate:
    """更新模板头（名称/说明）。归档模板只读；名称冲突映射为 409。"""
    if template.status == TEMPLATE_STATUS_ARCHIVED:
        raise ConflictError(
            "模板已归档（只读）",
            error_code="REVIEW_TEMPLATE_ARCHIVED",
            detail={"template_id": template.id},
        )
    changed = False
    if name is not None:
        cleaned = _check_name(name)
        if cleaned != template.name:
            template.name = cleaned
            changed = True
    if clear_description:
        template.description = None
        changed = True
    elif description is not None:
        cleaned = _check_optional_text(
            description, field="description", limit=MAX_DESCRIPTION_CHARS
        )
        template.description = cleaned
        changed = True
    if changed:
        template.updated_by = updated_by
        template.lock_version = int(template.lock_version or 1) + 1
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("同名模板已存在", error_code="REVIEW_TEMPLATE_NAME_CONFLICT") from exc
    await session.refresh(template)
    return template


async def create_version(
    session: AsyncSession,
    *,
    template: ReviewTemplate,
    items: list[dict[str, Any]] | None,
    created_by: int,
    change_note: str | None = None,
    source: str = "manual",
    full_items: bool = False,
    commit: bool = True,
) -> ReviewTemplateVersion:
    """新增一个不可变版本；版本号由 (template_id, max+1) 分配。

    ``items`` 默认是**自定义追加项**（内置 4 项由服务端原样补齐）；``full_items=True``
    时按完整列表校验（bootstrap 的内部调用），内置项必须原样存在于列表中。

    并发保存同一模板时唯一约束兜底，后到者 409（由用户重试，绝不静默覆盖）。
    """
    if template.status == TEMPLATE_STATUS_ARCHIVED:
        raise ConflictError(
            "模板已归档：不能再新增版本",
            error_code="REVIEW_TEMPLATE_ARCHIVED",
            detail={"template_id": template.id},
        )
    normalized = normalize_items(items) if full_items else compose_version_items(items)
    if len(_canonical_json(normalized).encode("utf-8")) > MAX_PAYLOAD_BYTES:
        raise ValidationError("模板版本内容超过大小上限", field="items")
    change_note = _check_optional_text(
        change_note, field="change_note", limit=MAX_CHANGE_NOTE_CHARS
    )
    await _ensure_version_budget(session, template.id)
    version_no = await _next_version_no(session, template.id)
    row = ReviewTemplateVersion(
        id=str(uuid.uuid4()),
        template_id=template.id,
        organization_id=template.organization_id,
        version_no=version_no,
        version_label=f"v{version_no}",
        items_json=normalized,
        item_count=len(normalized),
        payload_sha256=_digest(normalized),
        source=source,
        change_note=change_note,
        created_by=created_by,
    )
    session.add(row)
    template.latest_version = version_no
    template.updated_by = created_by
    template.lock_version = int(template.lock_version or 1) + 1
    if commit:
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError(
                "模板版本并发冲突，请重试", error_code="REVIEW_TEMPLATE_VERSION_CONFLICT"
            ) from exc
        await session.refresh(row)
        await session.refresh(template)
    return row


async def bootstrap_from_checklist(
    session: AsyncSession,
    *,
    organization_id: int,
    created_by: int,
    name: str = "默认核验清单",
) -> tuple[ReviewTemplate, ReviewTemplateVersion]:
    """从进程级 checklist 初始化一个模板版本（存量部署的平滑入口）。

    内容严格来自 ``load_checklist()``（默认 4 项 + ``REVIEW_CHECKLIST_JSON``
    追加项），不改变任何现行行为；已经是「把环境变量里的规则固化成一个可
    版本化的起点」。
    """
    items = items_from_checklist(load_checklist())
    try:
        template = await create_template(
            session,
            organization_id=organization_id,
            created_by=created_by,
            name=name,
            description="从进程级核验清单（REVIEW_CHECKLIST_JSON）初始化",
            commit=False,
        )
        version = await create_version(
            session,
            template=template,
            items=items,
            created_by=created_by,
            change_note="bootstrap from REVIEW_CHECKLIST_JSON",
            source="bootstrap",
            full_items=True,
            commit=False,
        )
        await session.commit()
    except IntegrityError as exc:
        # create_version 的 SELECT 会 autoflush 待提交的模板头；重名冲突可能
        # 早于显式 commit 抛出，必须在整个原子写入块外统一映射为 409。
        await session.rollback()
        raise ConflictError("同名模板已存在", error_code="REVIEW_TEMPLATE_NAME_CONFLICT") from exc
    await session.refresh(template)
    await session.refresh(version)
    return template, version


async def list_versions(
    session: AsyncSession,
    *,
    organization_id: int,
    template: ReviewTemplate,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    conditions = [
        ReviewTemplateVersion.template_id == template.id,
        ReviewTemplateVersion.organization_id == organization_id,
    ]
    total = int(
        (
            await session.execute(select(func.count(ReviewTemplateVersion.id)).where(*conditions))
        ).scalar()
        or 0
    )
    rows = (
        (
            await session.execute(
                select(ReviewTemplateVersion)
                .where(*conditions)
                .order_by(ReviewTemplateVersion.version_no.desc(), ReviewTemplateVersion.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )
    return {
        "items": [version_to_dict(row, include_items=False) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "latest_version": int(template.latest_version or 0),
    }


# ── 只读 diff ────────────────────────────────────────────────────────────────

_DIFF_SIDES = ("base", "target")


def _custom_items(row: ReviewTemplateVersion) -> list[dict[str, Any]]:
    """diff 只比较自定义追加项：内置 4 项由服务端固定，呈现为差异只会制造噪音。"""
    payload = row.items_json
    if not isinstance(payload, list):
        return []
    return [
        entry
        for entry in payload
        if isinstance(entry, dict) and entry.get("key") not in BUILTIN_EXTRACTOR_KEYS
    ]


def diff_versions(
    base_row: ReviewTemplateVersion, target_row: ReviewTemplateVersion
) -> dict[str, Any]:
    """两个版本之间的**只读**规则差异（按 item key 对齐，关键词按集合语义）。

    口径：只比较自定义追加项 —— 内置 4 项由服务端固定且两版必然相同，纳入
    对比只会制造「什么都没改也有一堆差异」的噪音。输出为纯数据（可直接 JSON
    序列化/进前端 diff 视图）：

    - ``added``：target 有、base 无的自定义项（完整规则项）；
    - ``removed``：base 有、target 无的自定义项；
    - ``changed``：同 key 但关键词/名称有差异的项，逐字段列出两侧值与新增/
      删除的关键词；
    - ``unchanged_keys``：完全相同的自定义项 key；
    - ``summary``：计数（供前端结论横幅，不用颜色单独表达状态）。

    diff 不改写任何行，也不产生任何快照 —— 它不参与建案与判定。
    """
    base_items = _custom_items(base_row)
    target_items = _custom_items(target_row)
    base_by_key = {entry.get("key"): entry for entry in base_items}
    target_by_key = {entry.get("key"): entry for entry in target_items}

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for key, target in target_by_key.items():
        base = base_by_key.get(key)
        if base is None:
            added.append(target)
            continue
        field_changes: dict[str, Any] = {}
        base_keywords = list(base.get("keywords") or [])
        target_keywords = list(target.get("keywords") or [])
        added_keywords = [word for word in target_keywords if word not in base_keywords]
        removed_keywords = [word for word in base_keywords if word not in target_keywords]
        # 集合语义：顺序不同不是差异；增删任一关键词才是。
        if added_keywords or removed_keywords:
            field_changes["keywords"] = {
                side: list((base if side == "base" else target).get("keywords") or [])
                for side in _DIFF_SIDES
            }
            field_changes["keywords"]["added"] = added_keywords
            field_changes["keywords"]["removed"] = removed_keywords
        if (base.get("label") or "") != (target.get("label") or ""):
            field_changes["label"] = {
                "base": base.get("label") or "",
                "target": target.get("label") or "",
            }
        if field_changes:
            changed.append({"key": key, "fields": field_changes})
        else:
            unchanged.append(key)
    for key, base in base_by_key.items():
        if key not in target_by_key:
            removed.append(base)

    # 自定义项无差异即「规则等价」：内置基线固定，不需要再比整行哈希。
    identical = not added and not removed and not changed
    return {
        "base": {
            "id": base_row.id,
            "version_no": int(base_row.version_no),
            "version_label": base_row.version_label,
            "payload_sha256": base_row.payload_sha256,
            "item_count": int(base_row.item_count),
        },
        "target": {
            "id": target_row.id,
            "version_no": int(target_row.version_no),
            "version_label": target_row.version_label,
            "payload_sha256": target_row.payload_sha256,
            "item_count": int(target_row.item_count),
        },
        "identical": identical,
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged_keys": sorted(unchanged),
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "unchanged": len(unchanged),
        },
    }


# ── 建案集成 ────────────────────────────────────────────────────────────────


async def resolve_template_items(
    session: AsyncSession,
    *,
    organization_id: int,
    template_id: str,
    version_no: int | None,
) -> tuple[ReviewTemplate, ReviewTemplateVersion, tuple[ChecklistItem, ...]]:
    """建案时解析模板版本规则；模板必须 active、版本必须存在且未损坏。

    ``version_no`` 为空时取模板最新版本；归档模板不允许再被新案件选择。
    """
    template = await get_template(session, organization_id=organization_id, template_id=template_id)
    if template.status != TEMPLATE_STATUS_ACTIVE:
        raise ConflictError(
            "模板已归档：不能再用于新建案件",
            error_code="REVIEW_TEMPLATE_ARCHIVED",
            detail={"template_id": template.id},
        )
    target_no = version_no if version_no is not None else int(template.latest_version or 0)
    if target_no <= 0:
        raise ValidationError("该模板还没有任何版本，不能用于建案", field="template_version_no")
    version = await get_template_version(
        session,
        organization_id=organization_id,
        template=template,
        version_no=target_no,
    )
    items = version_items(version)
    if items is None:
        # 内容哈希不符 = 数据损坏/被篡改；绝不把不可信规则当作建案依据。
        raise ConflictError(
            "模板版本内容校验失败，拒绝使用",
            error_code="REVIEW_TEMPLATE_VERSION_CORRUPT",
            detail={"template_id": template.id, "version_no": target_no},
        )
    return template, version, items_to_checklist(items)


def template_metadata_for_case(case: ReviewCase) -> dict[str, Any] | None:
    """案件的模板溯源元数据（无模板建案返回 None）。

    只读两条可空列；规则内容真源仍是 ``rule_snapshot_id``，这里不参与判定。
    """
    if not case.template_id:
        return None
    return {
        "template_id": case.template_id,
        "template_version_no": case.template_version_no,
    }


async def archive_template(
    session: AsyncSession, *, template: ReviewTemplate, user_id: int
) -> ReviewTemplate:
    """归档模板（只读终态）。重复归档 409；不删除任何行、不改写任何案件/快照。

    归档后：不可新增版本、不可改名、不可再被新案件选择；历史案件的
    ``rule_snapshot_id`` 快照与 ``template_id`` 溯源指针保持原样可读。
    """
    if template.status == TEMPLATE_STATUS_ARCHIVED:
        raise ConflictError(
            "模板已归档",
            error_code="REVIEW_TEMPLATE_ALREADY_ARCHIVED",
            detail={"template_id": template.id},
        )
    template.status = TEMPLATE_STATUS_ARCHIVED
    template.archived_at = _now()
    template.archived_by = user_id
    template.updated_by = user_id
    template.lock_version = int(template.lock_version or 1) + 1
    await session.commit()
    await session.refresh(template)
    return template


# 版本派生快照时使用的 schema 版本标签与规则快照保持一致（审计可对照）。
SNAPSHOT_SCHEMA_VERSION = RULE_SNAPSHOT_SCHEMA_VERSION
