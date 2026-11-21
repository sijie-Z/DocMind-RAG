"""合同审查规则/playbook 版本快照。

建案时把 ``load_checklist()`` 的当前结果固化为不可变 ``ReviewRuleSet`` 行，
案件通过 nullable FK 引用。读取/分析/导出时优先使用快照中的规则关键词，
旧案件（无快照或快照损坏）回落当前 checklist —— 与父版本行为一致。

边界：只读规则元数据，不改判定算法、payload_sha256、quote/document_sha256
或 stale_reasons；不新增 HTTP 路由。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.review import ReviewCase, ReviewRuleSet
from app.services.review_checklist import ChecklistItem, load_checklist

RULE_SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class RuleSnapshotPayload:
    """从快照行解析出的规则定义（关键词仍供通用抽取器使用）。"""

    rules: tuple[ChecklistItem, ...]
    version: str
    payload_sha256: str


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def build_snapshot_payload(
    items: tuple[ChecklistItem, ...], organization_id: int
) -> dict[str, Any]:
    """从 checklist loader 的结果派生确定性 payload（原样保留 key/keywords）。"""
    return {
        "schema_version": RULE_SNAPSHOT_SCHEMA_VERSION,
        "organization_id": organization_id,
        "rules": [
            {
                "key": item.key,
                "keywords": list(item.keywords),
                "label": item.label,
                "configurable": item.configurable,
            }
            for item in items
        ],
    }


def parse_snapshot(
    row: ReviewRuleSet | None, *, organization_id: int
) -> RuleSnapshotPayload | None:
    """校验组织归属与内容哈希；旧数据/篡改数据返回 None（回落当前 checklist）。

    组织校验做**双向**交叉核对：行级 ``organization_id`` 与 payload 内冻结的
    ``organization_id`` 都必须等于调用方组织。只改行级列或只改 payload 任一
    处都视为篡改。
    """
    if row is None or row.organization_id != organization_id:
        return None
    payload = row.payload_json
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != RULE_SNAPSHOT_SCHEMA_VERSION
    ):
        return None
    if payload.get("organization_id") != organization_id:
        return None
    raw_rules = payload.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        return None
    if _digest(payload) != row.payload_sha256:
        return None
    rules: list[ChecklistItem] = []
    for entry in raw_rules:
        if not isinstance(entry, dict):
            return None
        key = entry.get("key")
        keywords = entry.get("keywords")
        if not isinstance(key, str) or not isinstance(keywords, list):
            return None
        if any(not isinstance(word, str) for word in keywords):
            return None
        rules.append(
            ChecklistItem(
                key=key,
                keywords=tuple(keywords),
                label=str(entry.get("label") or ""),
                configurable=bool(entry.get("configurable", False)),
            )
        )
    return RuleSnapshotPayload(
        rules=tuple(rules), version=row.version, payload_sha256=row.payload_sha256
    )


async def _find_by_digest(
    session: AsyncSession, organization_id: int, digest: str
) -> ReviewRuleSet | None:
    return await session.scalar(
        select(ReviewRuleSet).where(
            ReviewRuleSet.organization_id == organization_id,
            ReviewRuleSet.payload_sha256 == digest,
        )
    )


async def attach_snapshot(
    session: AsyncSession, case: ReviewCase, checklist: tuple[ChecklistItem, ...]
) -> ReviewRuleSet:
    """建案时按 (组织, 内容哈希) 复用或新建快照行，并写入案件 FK。

    并发首次建案会同时 SELECT 未命中、同时 INSERT：唯一约束让后者抛
    IntegrityError。这里用 savepoint 只回滚插入本身，再回查对手行复用——
    正常竞态不该变成 409 让用户重试，也不能丢掉外层事务里已写入的案件。
    """
    payload = build_snapshot_payload(checklist, case.organization_id)
    digest = _digest(payload)
    existing = await _find_by_digest(session, case.organization_id, digest)
    if existing is not None:
        case.rule_snapshot_id = existing.id
        return existing

    row = ReviewRuleSet(
        id=str(uuid.uuid4()),
        organization_id=case.organization_id,
        version=f"v{RULE_SNAPSHOT_SCHEMA_VERSION}",
        payload_json=payload,
        payload_sha256=digest,
        published_at=datetime.now(UTC),
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        # savepoint 回滚后，失败的 pending 行可能仍留在 session 里，会在下一次
        # flush/commit 时重试插入再次报错；可能已被回滚摘除，故容错处理。
        if row in session:
            session.expunge(row)
        winner = await _find_by_digest(session, case.organization_id, digest)
        if winner is None:
            raise
        case.rule_snapshot_id = winner.id
        return winner
    case.rule_snapshot_id = row.id
    return row


async def load_snapshot(session: AsyncSession, case: ReviewCase) -> ReviewRuleSet | None:
    if not case.rule_snapshot_id:
        return None
    row = await session.get(ReviewRuleSet, case.rule_snapshot_id)
    if parse_snapshot(row, organization_id=case.organization_id) is None:
        return None
    return row


async def resolve_case_rules(session: AsyncSession, case: ReviewCase) -> tuple[ChecklistItem, ...]:
    """分析时的规则关键词来源：快照优先，缺快照回落当前 checklist。"""
    row = await load_snapshot(session, case)
    parsed = parse_snapshot(row, organization_id=case.organization_id)
    return parsed.rules if parsed is not None else load_checklist()


async def case_rule_metadata(
    session: AsyncSession, case: ReviewCase, items: list[dict[str, Any]]
) -> dict[str, Any]:
    """读取/导出用的规则元数据。

    命名语义（避免误读，字段名沿用 API 既有习惯）：
    - ``rule_snapshot_version``：快照 **schema** 版本标签（当前恒为 ``v1``），
      不是规则内容的业务版本号；内容身份由 ``rule_snapshot_sha256`` 承担。
    - ``triggered_rule_ids``：本案核验项覆盖到的规则 key（来自 item_key 与
      explanation.rule_id），表示「这些规则参与过本案判定」，**不等于**每条
      规则都真的命中了证据或被判定为有差异；是否命中看各项 facts/change_type。
    """
    row = await load_snapshot(session, case)
    parsed = parse_snapshot(row, organization_id=case.organization_id)
    rules = parsed.rules if parsed is not None else load_checklist()
    known_ids = {item.key for item in rules}
    triggered: list[str] = []
    for item in items:
        candidates = [item.get("item_key")]
        explanation = item.get("explanation")
        if isinstance(explanation, dict):
            candidates.append(explanation.get("rule_id"))
        for rule_id in candidates:
            if isinstance(rule_id, str) and rule_id in known_ids and rule_id not in triggered:
                triggered.append(rule_id)
    return {
        "rule_snapshot_id": case.rule_snapshot_id if parsed is not None else None,
        "rule_snapshot_version": parsed.version if parsed is not None else None,
        "rule_snapshot_sha256": parsed.payload_sha256 if parsed is not None else None,
        "triggered_rule_ids": triggered,
        "from_snapshot": parsed is not None,
    }
