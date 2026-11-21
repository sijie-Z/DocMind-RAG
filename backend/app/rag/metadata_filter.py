"""把知识检索的标签/元数据过滤收口在 DB 预筛，避免改 ES mapping 或 reindex。

背景：ES mapping 虽声明 ``tags`` keyword，但索引写入路径并未写该字段；
直接把 tag filter 塞进 ES 会静默把合法查询过滤成空。这里改为在 DB 上按
白名单字段预筛出候选 document_id，再复用检索器既有的 ``document_id``
terms filter——查询体结构与旧实现逐字一致。

本模块只做「收窄」，不会放宽任何范围：organization 由 pipeline 传入，
ACL blocked_ids 由 document_acl 单独计算并始终 append 到 ES filters。
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentType

# API 暴露的字段名 → DB 列名（白名单即安全边界；未知字段在 schema 阶段拒绝）。
METADATA_FILTER_FIELDS: frozenset[str] = frozenset({"tags", "file_type"})
# 只允许常见的标签/类型文本值，控制注入面与查询代价。
_SAFE_VALUE_RE = re.compile(r"^[\w.\-/一-鿿]+$", re.UNICODE)
MAX_METADATA_FILTER_VALUES = 20
# 组织内被扫的文档上限：超限时拒绝检索，不能悄悄返回「部分匹配」把范围放大。
MAX_METADATA_FILTER_SCAN = 10_000


def normalize_metadata_filters(
    value: dict[str, str | list[str]] | None,
) -> dict[str, list[str]] | None:
    """校验并归一化 metadata_filters；非法输入抛 ValueError（端点返回 422）。

    单个字符串等价于单元素列表（AND 语义）；数组表示 OR。多个字段间为 AND。
    """
    if not value:
        return None
    normalized: dict[str, list[str]] = {}
    for field, raw in value.items():
        if field not in METADATA_FILTER_FIELDS:
            raise ValueError(f"metadata_filters 不支持字段: {field!r}")
        candidates = [raw] if isinstance(raw, str) else raw
        if not isinstance(candidates, list) or not candidates:
            raise ValueError(f"metadata_filters.{field} 必须是非空字符串或字符串数组")
        if len(candidates) > MAX_METADATA_FILTER_VALUES:
            raise ValueError(f"metadata_filters.{field} 最多 {MAX_METADATA_FILTER_VALUES} 个值")
        values: list[str] = []
        for item in candidates:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"metadata_filters.{field} 不能包含空值")
            text = item.strip()
            if len(text) > 100 or not _SAFE_VALUE_RE.fullmatch(text):
                raise ValueError(f"metadata_filters.{field} 含非法字符: {text!r}")
            if text not in values:
                values.append(text)
        normalized[field] = values
    return normalized or None


def _type_values(values: list[str]) -> list[DocumentType]:
    allowed: list[DocumentType] = []
    for value in values:
        try:
            member = DocumentType(value.lower())
        except ValueError:
            continue
        if member not in allowed:
            allowed.append(member)
    return allowed


async def resolve_metadata_filter_document_ids(
    db: AsyncSession,
    organization_id: int,
    metadata_filters: dict[str, list[str]] | None,
) -> list[str] | None:
    """按白名单字段从 DB 预筛文档 ID；无过滤时返回 None（保持旧查询体）。

    - tags 对 Document.keywords（JSON 数组）做精确值匹配（OR 语义）。
      MySQL JSON 列没有跨方言统一的「包含元素」运算符，故取回组织内候选
      后在 Python 侧精确判定；组织过滤始终先落 SQL，不能靠进程侧补救。
    - file_type 对 Document.file_type（Enum）做精确值匹配。
    - 与显式 document_ids 的交集由调用方完成；ACL 不在这里碰，绝不放宽。
    - DB 异常必须 fail-closed：宁可拒绝检索，不能静默返回全组织结果。
    """
    if not metadata_filters:
        return None
    # 纵深防御：API 入口已校验；内部调用方传入未归一化/非法字段时同样拒绝，
    # 不能让未知 key 被静默忽略而退回「不限定」全组织检索。
    metadata_filters = normalize_metadata_filters(metadata_filters) or {}

    conditions: list[Any] = [Document.organization_id == organization_id]
    columns = [Document.id]
    tag_values = metadata_filters.get("tags")
    if tag_values:
        columns.append(Document.keywords)
    if "file_type" in metadata_filters:
        allowed = _type_values(metadata_filters["file_type"])
        # 全部取值不在枚举内 → 恒假，返回空集合（fail-closed，不进入全库检索）。
        conditions.append(Document.file_type.in_(allowed) if allowed else false())

    try:
        # 有界扫描：超限时显式拒绝，不能静默返回不完整候选（那会放大检索范围）。
        stmt = select(*columns).where(*conditions).limit(MAX_METADATA_FILTER_SCAN + 1)
        rows = (await db.execute(stmt)).all()
    except Exception as exc:  # noqa: BLE001 — fail closed，不让过滤失效变成越权检索
        from app.rag.errors import RetrievalUnavailableError

        raise RetrievalUnavailableError("标签过滤查询失败，检索已拒绝") from exc

    if len(rows) > MAX_METADATA_FILTER_SCAN:
        from app.rag.errors import RetrievalUnavailableError

        raise RetrievalUnavailableError(
            f"标签过滤候选超过 {MAX_METADATA_FILTER_SCAN} 篇，检索已拒绝；请缩小标签范围"
        )

    matched: list[str] = []
    for row in rows:
        if tag_values:
            keywords = row[1] if len(row) > 1 else None
            if not isinstance(keywords, list) or not (
                {str(value) for value in keywords} & set(tag_values)
            ):
                continue
        matched.append(str(row[0]))
    return matched


_NO_MATCH = object()


def intersect_document_ids(
    explicit_ids: list[str] | None, filtered_ids: list[str] | None
) -> list[str] | object:
    """元数据预筛与显式 document_ids 取交集。

    ``filtered_ids is None``（无过滤）返回原 explicit_ids；空列表是「标签匹配
    为零」，必须返回哨兵而非 ``[]`` —— 检索器把空列表当作「不限定」，直接
    传 [] 会静默退回全组织检索。哨兵在 pipeline 中转成早退空结果。
    """
    if filtered_ids is None:
        return explicit_ids
    if not filtered_ids:
        return _NO_MATCH
    if not explicit_ids:
        return filtered_ids
    allow = set(filtered_ids)
    narrowed = [doc_id for doc_id in explicit_ids if doc_id in allow]
    # 零交集与「标签零命中」同形：返回 [] 会被检索器当成「不限定」→ 全组织检索。
    return narrowed or _NO_MATCH
