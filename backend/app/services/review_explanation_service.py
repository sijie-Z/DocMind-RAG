"""核验项风险等级与可解释信息（竞品对标 High/Medium/Low + 可点击引用）。

这里是纯派生层：只读现有 ``facts`` / ``change_type`` / ``item_key`` / 证据 id，
不改判定算法、不写回事实、不引入正文或提示词。所有输出都是短机器码，
可直接进入 SQLite/MySQL 列与导出 JSON；旧行/缺字段一律安全回落到 low/None。

派生值不是事实，也不允许人工覆盖：schema 无覆盖字段，每次读取/分析都从
当前 facts 重算，避免 facts 变化后 severity/explanation 陈旧。
"""

from __future__ import annotations

import re
from typing import Any

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

# facts.reason 的生产者是 review_analysis_service 的确定性抽取器；这是它的
# 完整封闭码表。未知码（包括敌意文本、未来新增码）一律不进入 explanation/导出。
_HIGH_REASON_CODES = frozenset({"multiple_durations", "reference_unresolved"})
_MEDIUM_REASON_CODES = frozenset(
    {
        "unit_unclear",
        "no_duration",
        "no_payment_clause",
        "no_start_event",
        "no_terms",
    }
)
KNOWN_REASON_CODES = _HIGH_REASON_CODES | _MEDIUM_REASON_CODES
_MAX_REASON_FIELD_CHARS = 256
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# rule_id 是 checklist 的 item_key；与 review_checklist.py 的键格式一致。
_RULE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
# evidence_id 通常是 uuid4；也允许测试/旧数据中的短 ASCII 标识。
# 关键是阻断控制字符、空白、竖线与任意非 ASCII 文本（正文/U+202E 等）。
_EVIDENCE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_reason_codes(reason: Any) -> tuple[str, ...]:
    """把 facts.reason 原始串收敛为白名单机器码；任何敌意/超长文本都被丢弃。"""
    if not isinstance(reason, str) or len(reason) > _MAX_REASON_FIELD_CHARS:
        return ()
    return tuple(
        part
        for part in reason.split("+")
        if part in KNOWN_REASON_CODES and _REASON_CODE_RE.fullmatch(part)
    )


def _reason_codes(facts: Any) -> tuple[str, ...]:
    if not isinstance(facts, dict):
        return ()
    codes: list[str] = []
    for side in ("base", "revised", "policy"):
        fact = facts.get(side)
        if not isinstance(fact, dict):
            continue
        for code in safe_reason_codes(fact.get("reason")):
            if code not in codes:
                codes.append(code)
    return tuple(codes)


def derive_severity(
    facts: Any,
    *,
    item_key: Any = None,
    change_type: Any = None,
) -> str:
    """从当前 facts/change_type 派生 high/medium/low；旧行缺字段安全回落 low。

    原因码按严重度取最高一档，组合码（如 ``unit_unclear+multiple_durations``）
    自然取 high；未知原因码被白名单剔除，只按 change_type 回落。
    """
    codes = _reason_codes(facts)
    if any(code in _HIGH_REASON_CODES for code in codes):
        return SEVERITY_HIGH
    if any(code in _MEDIUM_REASON_CODES for code in codes):
        return SEVERITY_MEDIUM
    if isinstance(change_type, str) and change_type in (
        "modified",
        "added",
        "removed",
        "renumbered",
        "unidentified",
    ):
        return SEVERITY_MEDIUM
    return SEVERITY_LOW


def _safe_identifier(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    return value if pattern.fullmatch(value) else None


def derive_explanation(
    facts: Any,
    *,
    item_key: Any,
    change_type: Any = None,
    evidence: Any = None,
) -> dict[str, Any] | None:
    """结构化引用：rule_id / evidence_id / reason_code；拿不到则为 None。

    ``rule_id`` 取自 item_key（核验项即规则），``evidence_id`` 只取证据行 id，
    ``reason_code`` 只取 facts.reason 的白名单机器码（组合码用 ``+`` 连接）。
    所有字段都经过格式/长度校验；不读取 quote/正文/prompt/candidates。
    旧值不参与派生：每次调用都按当前输入重算。
    """
    rule_id = _safe_identifier(item_key, _RULE_ID_RE)
    reason_code = "+".join(_reason_codes(facts)) or None
    evidence_id: str | None = None
    if isinstance(evidence, dict):
        # 优先选中的主证据，其次任一侧首行；顺序稳定，保证幂等。
        flat: list[dict[str, Any]] = []
        for side in ("base", "revised", "policy"):
            rows = evidence.get(side)
            if isinstance(rows, list):
                flat.extend(row for row in rows if isinstance(row, dict))
        for row in flat:
            if not row.get("selected") or row.get("role") != "main":
                continue
            candidate = _safe_identifier(row.get("id"), _EVIDENCE_ID_RE)
            if candidate:
                evidence_id = candidate
                break
        if evidence_id is None:
            for row in flat:
                candidate = _safe_identifier(row.get("id"), _EVIDENCE_ID_RE)
                if candidate:
                    evidence_id = candidate
                    break
    result = {
        "rule_id": rule_id,
        "evidence_id": evidence_id,
        "reason_code": reason_code,
    }
    return result if any(value is not None for value in result.values()) else None


def derive_review_explanation(item: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """从单条核验项 dict（API/导出通用）派生 severity + explanation。"""
    severity = derive_severity(
        item.get("facts"),
        item_key=item.get("item_key"),
        change_type=item.get("change_type"),
    )
    explanation = derive_explanation(
        item.get("facts"),
        item_key=item.get("item_key"),
        change_type=item.get("change_type"),
        evidence=item.get("evidence"),
    )
    return severity, explanation
