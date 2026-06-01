"""Quality Gate — post-execution result validation before final output.

Inspired by TradingAgents' data quality gate that sits between analysts and
debaters. This module checks tool execution results for common failure modes
before they reach the user.

Two-layer design:
  Layer 1: Hard checks (code rules, zero LLM cost)
  Layer 2: LLM review (optional, one call, only if hard checks pass)
"""

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# ── Failure markers that indicate a tool call produced no useful data ──
FAILURE_MARKERS: list[str] = [
    "无法获取",
    "没有找到",
    "未找到",
    "无相关文档",
    "工具调用失败",
    "Error:",
    "SQL Error:",
    "timed out",
    "timeout",
    "Connection refused",
    "Internal Server Error",
]

# ── Hard check thresholds ──
MIN_RESULT_LENGTH: int = 10
MAX_CONSECUTIVE_FAILURES: int = 2


def _hard_check(result: str, tool_name: str) -> tuple[str, str]:
    """Run hard checks on a single tool result.

    Returns (grade, detail):
        A — clean result with substantive content
        C — result exists but has quality concerns
        F — result is empty, error, or entirely failure markers
    """
    if not result or not result.strip():
        return ("F", f"[{tool_name}] 结果为空")

    text = result.strip()
    length = len(text)

    if length < MIN_RESULT_LENGTH:
        return ("C", f"[{tool_name}] 结果过短 ({length} chars)")

    # Count failure markers
    failure_count = 0
    for marker in FAILURE_MARKERS:
        if marker in text:
            failure_count += 1

    if failure_count >= 1:
        # If result starts with an Error marker, it's fatal
        if any(text.startswith(m) for m in FAILURE_MARKERS if m):
            return ("F", f"[{tool_name}] 以失败标记开头: {text[:80]}")
        # Multiple failure markers → fatal
        if failure_count >= 2:
            return ("F", f"[{tool_name}] 包含 {failure_count} 处失败标记")
        # Single embedded failure marker → concern
        return ("C", f"[{tool_name}] 包含失败标记")

    # Check for empty data structures
    if tool_name in ("search_knowledge_base", "vector_search"):
        if "No relevant documents" in text or "未找到" in text:
            return ("C", f"[{tool_name}] 检索无结果")

    if tool_name in ("execute_sql",):
        if '"rows": 0' in text or '"data": []' in text:
            return ("C", f"[{tool_name}] SQL 查询返回空结果")

    return ("A", "")


def quality_gate_check(
    step_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Run quality gate on all step execution results.

    Args:
        step_results: Dict of step_id → {description, result, status, tool_hint}

    Returns:
        {
            "passed": bool,
            "summary": str,         # human-readable summary
            "grades": {step_id: (grade, detail)},
            "fatal_issues": [str],   # issues that should block final output
        }
    """
    grades: dict[str, tuple[str, str]] = {}
    passed_all = True
    fatal_issues: list[str] = []

    for step_id, data in step_results.items():
        if not isinstance(data, dict):
            grades[step_id] = ("C", f"[{step_id}] 非结构化结果")
            continue

        status = data.get("status", "")
        result = data.get("result") or ""
        tool_hint = data.get("tool_hint") or data.get("tool_name", "unknown")

        # Failures from executor
        if status == "failed":
            grades[step_id] = ("F", f"[{tool_hint}] 步骤执行失败")
            fatal_issues.append(f"步骤 {step_id} 执行失败: {data.get('error_context', '')[:100]}")
            passed_all = False
            continue

        grade, detail = _hard_check(result, tool_hint)
        grades[step_id] = (grade, detail)

        if grade == "F":
            passed_all = False
            fatal_issues.append(detail or f"步骤 {step_id} 未通过质量检查")
        elif grade == "C":
            passed_all = False

    # Build summary
    total = len(grades)
    passed = sum(1 for g, _ in grades.values() if g == "A")
    concerns = sum(1 for g, _ in grades.values() if g == "C")
    failed = sum(1 for g, _ in grades.values() if g == "F")

    summary_lines = [
        f"## 质量门控结果\n",
        f"**总计**: {total} 步 | ✅ {passed} 通过 | ⚠️ {concerns} 需关注 | ❌ {failed} 未通过\n",
    ]
    for step_id, (grade, detail) in grades.items():
        icon = {"A": "✅", "C": "⚠️", "F": "❌"}.get(grade, "❓")
        desc = step_results.get(step_id, {}).get("description", step_id) if isinstance(step_results.get(step_id), dict) else step_id
        summary_lines.append(f"- {icon} **{desc}**: [{grade}] {detail}")

    return {
        "passed": passed_all,
        "summary": "\n".join(summary_lines),
        "grades": grades,
        "fatal_issues": fatal_issues,
    }
