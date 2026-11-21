"""ReviewItem 风险等级与可解释性：nullable 列 + 确定性回填。

竞品对标（Ironclad High/Medium/Low + 推理来源，Icertis Red/Amber/Green +
可点击引用）只在核验项上补充**派生**展示信息：severity 与 explanation。

- 两列均可空；旧行显示时按 ``app.services.review_explanation_service`` 的
  同一套规则回落，因此回填不是唯一真源；
- 回填只读 ``facts[*].reason`` / ``change_type`` / ``item_key`` 与证据 id，
  不改 facts、决策、quote_sha256/document_sha256 或 stale_reasons 语义；
- explanation 只含 rule_id(item_key)、evidence_id(证据行 id) 与 reason_code，
  不含 quote/正文/prompt；
- SQLite/MySQL 均可升级/降级；downgrade 仅删列，不触碰业务数据。
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa

from alembic import op

revision = "031_review_severity"
down_revision = "029_chat_session_share_token"
branch_labels = None
depends_on = None

TABLE = "review_item"

_HIGH_REASON_CODES = frozenset({"multiple_durations", "reference_unresolved"})
_MEDIUM_REASON_CODES = frozenset({"unit_unclear", "no_duration", "no_terms"})


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}


def _reason_codes(facts: Any) -> tuple[str, ...]:
    if isinstance(facts, str):
        try:
            facts = json.loads(facts)
        except (TypeError, ValueError):
            return ()
    if not isinstance(facts, dict):
        return ()
    codes: list[str] = []
    for side in ("base", "revised", "policy"):
        fact = facts.get(side)
        if not isinstance(fact, dict):
            continue
        reason = fact.get("reason")
        if not isinstance(reason, str):
            continue
        for part in reason.split("+"):
            code = part.strip()
            if code and code not in codes:
                codes.append(code)
    return tuple(codes)


def _severity(facts: Any, change_type: Any) -> str:
    codes = _reason_codes(facts)
    if any(code in _HIGH_REASON_CODES for code in codes):
        return "high"
    if any(code in _MEDIUM_REASON_CODES for code in codes):
        return "medium"
    if isinstance(change_type, str) and change_type in (
        "modified",
        "added",
        "removed",
        "renumbered",
        "unidentified",
    ):
        return "medium"
    return "low"


def _backfill() -> None:
    """Python 侧确定性回填，避免方言相关 SQL/JSON 函数。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            f"SELECT id, item_key, facts, change_type FROM {TABLE} "
            "WHERE severity IS NULL OR explanation IS NULL"
        )
    ).mappings()
    evidence_ids: dict[str, str] = {}
    for row in bind.execute(
        sa.text(
            "SELECT item_id, id FROM review_evidence "
            "WHERE selected = 1 AND role = 'main' ORDER BY id"
        )
    ):
        evidence_ids.setdefault(row[0], row[1])
    for row in bind.execute(
        sa.text("SELECT item_id, id FROM review_evidence ORDER BY id")
    ):
        evidence_ids.setdefault(row[0], row[1])

    updates: list[dict[str, Any]] = []
    for row in rows:
        codes = _reason_codes(row["facts"])
        explanation: dict[str, Any] | None = None
        evidence_id = evidence_ids.get(row["id"])
        if row["item_key"] or evidence_id or codes:
            explanation = {
                "rule_id": row["item_key"] or None,
                "evidence_id": evidence_id,
                "reason_code": "+".join(codes) or None,
            }
        updates.append(
            {
                "id": row["id"],
                "severity": _severity(row["facts"], row["change_type"]),
                "explanation": json.dumps(explanation, ensure_ascii=False) if explanation else None,
            }
        )
    if updates:
        bind.execute(
            sa.text(
                f"UPDATE {TABLE} SET severity = :severity, explanation = :explanation "
                "WHERE id = :id"
            ),
            updates,
        )


def upgrade() -> None:
    existing = _columns()
    if "severity" not in existing:
        op.add_column(
            TABLE,
            sa.Column(
                "severity", sa.String(length=16), nullable=True, comment="风险等级 high/medium/low"
            ),
        )
    if "explanation" not in existing:
        op.add_column(
            TABLE,
            sa.Column(
                "explanation",
                sa.JSON(),
                nullable=True,
                comment="可解释引用 rule_id/evidence_id/reason_code",
            ),
        )
    _backfill()


def downgrade() -> None:
    existing = _columns()
    if "explanation" in existing:
        op.drop_column(TABLE, "explanation")
    if "severity" in existing:
        op.drop_column(TABLE, "severity")
