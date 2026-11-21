"""核验项风险等级与可解释信息（竞品对标 High/Medium/Low + 可点击引用）。

这里是纯派生层：只读现有 ``facts`` / ``change_type`` / ``item_key`` / 证据 id，
不改判定算法、不写回事实、不引入正文或提示词。所有输出都是短机器码，
可直接进入 SQLite/MySQL 列与导出 JSON；旧行/缺字段一律安全回落到 low/None。
"""

from __future__ import annotations

from typing import Any

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
VALID_SEVERITIES = (SEVERITY_LOW, SEVERITY_MEDIUM, SEVERITY_HIGH)

# 仅编码 facts.reason 里已有的机器码；自由文本、candidates/quote 一律不参与。
_HIGH_REASON_CODES = frozenset({"multiple_durations", "reference_unresolved"})
_MEDIUM_REASON_CODES = frozenset({"unit_unclear", "no_duration", "no_terms"})


def _reason_codes(facts: Any) -> tuple[str, ...]:
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


def derive_severity(
    facts: Any,
    *,
    item_key: Any = None,
    change_type: Any = None,
    severity: Any = None,
) -> str:
    """从既有事实派生 high/medium/low；显式合法值优先（向后兼容/人工覆盖）。

    优先级：已存合法 severity → 原因码 → 差异类型。原因码按严重度取最高一档，
    组合码（如 ``unit_unclear+multiple_durations``）自然取 high。
    ``unchanged``/``None`` 回落 low，未知原因码不误报 high。
    """
    if isinstance(severity, str) and severity in VALID_SEVERITIES:
        return severity
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


def derive_explanation(
    facts: Any,
    *,
    item_key: Any,
    change_type: Any = None,
    evidence: Any = None,
    explanation: Any = None,
) -> dict[str, Any] | None:
    """结构化引用：rule_id / evidence_id / reason_code；拿不到则为 None。

    ``rule_id`` 取自 item_key（核验项即规则），``evidence_id`` 只取证据行 id，
    ``reason_code`` 只取 facts.reason 的机器码（组合码用 ``+`` 连接）。
    不读取 quote/正文/prompt/candidates，避免任何文本泄漏。
    """
    if isinstance(explanation, dict):
        existing = {
            key: explanation[key]
            for key in ("rule_id", "evidence_id", "reason_code")
            if explanation.get(key) is not None
        }
        if existing:
            return existing
    rule_id = item_key.strip() if isinstance(item_key, str) and item_key.strip() else None
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
            candidate = row.get("id")
            if row.get("selected") and row.get("role") == "main" and isinstance(candidate, str):
                evidence_id = candidate
                break
        if evidence_id is None:
            for row in flat:
                candidate = row.get("id")
                if isinstance(candidate, str):
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
        severity=item.get("severity"),
    )
    explanation = derive_explanation(
        item.get("facts"),
        item_key=item.get("item_key"),
        change_type=item.get("change_type"),
        evidence=item.get("evidence"),
        explanation=item.get("explanation"),
    )
    return severity, explanation
