"""Failure Extractor — automatically mines lessons from benchmark failures.

Two extraction modes:
    1. Rule-based (default, zero LLM cost): matches known failure patterns
       via heuristics over category / keywords_missed / actual_answer.
    2. LLM-based (optional, higher quality): uses DeepSeek to analyse
       the full trace and generate nuanced lessons.

Usage:
    # Batch extract all current benchmark failures
    count = await extract_all_from_benchmark()
    print(f"Extracted {count} experiences")

    # Extract a single failure case
    with open("benchmark/cases/failure/L1-FRAME-01.json") as f:
        case = json.load(f)
    exp = await extract_from_benchmark_failure(case)
    if exp:
        await get_experience_store().add(exp)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agent.experience.models import Experience
from app.agent.experience.store import get_experience_store

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Rule Templates
# ═══════════════════════════════════════════════════════════════════════
# Keyed by (scenario, symptom_pattern) → actionable lesson string.
# Each lesson MUST be:
#   - Specific enough for the Planner to act on
#   - Generic enough to cover all cases in that category
# ═══════════════════════════════════════════════════════════════════════

SCENARIO_RULES: dict[str, list[dict[str, str]]] = {
    "cross_document": [
        {
            "symptom": "only_one_document_used",
            "lesson": "跨文档对比必须逐一查询每个目标文档，收集所有比较对象的证据，再进行综合分析。",
        },
        {
            "symptom": "no_comparison_made",
            "lesson": "跨文档分析的核心是「比较」，必须列出各文档在相同维度上的数据，再用对比句式呈现差异。",
        },
        {
            "symptom": "keywords_missing",
            "lesson": "跨文档任务需要覆盖所有预期关键词，包括对比、差异、共同点等。检索时使用多关键词。",
        },
    ],
    "framework": [
        {
            "symptom": "keywords_missing_swot",
            "lesson": "使用 SWOT 框架时，必须输出优势(Strengths)、劣势(Weaknesses)、机会(Opportunities)、威胁(Threats)四个维度的结构化分析，每个维度至少 2-3 个要点。",
        },
        {
            "symptom": "keywords_missing_dupont",
            "lesson": "杜邦分析必须拆解 ROE = 净利率 × 资产周转率 × 权益乘数，三个驱动因素缺一不可，每个都需要具体数值支撑。",
        },
        {
            "symptom": "framework_not_executed",
            "lesson": "用户明确要求使用某个分析框架时，直接执行该框架的结构化分析步骤，不需要先询问用户「是否继续」。",
        },
    ],
    "web_search": [
        {
            "symptom": "no_web_results",
            "lesson": "联网搜索无结果时，先尝试更换同义词或英文关键词重试，仍无结果才如实告知用户。",
        },
        {
            "symptom": "keywords_missing",
            "lesson": "联网搜索任务需要明确列出各搜索结果的具体数据（公司名、金额、日期），不能停留在概括性结论。",
        },
    ],
    "tool_recovery": [
        {
            "symptom": "not_found_no_alternative",
            "lesson": "搜索目标文档无结果时，主动列出知识库中所有可用文档清单，引导用户选择替代方案。",
        },
        {
            "symptom": "keywords_missing",
            "lesson": "工具出错时仍要输出已有结果的部分答案，不能直接返回错误信息。让部分结果可见。",
        },
    ],
    "edge_case": [
        {
            "symptom": "over_answers_simple_query",
            "lesson": "用户询问「知识库有什么」这类元问题时，直接列出文档清单即可，不需要进一步分析或总结。",
        },
        {
            "symptom": "keywords_missing",
            "lesson": "边界问题：对知识库能力范围外的问题（如天气），直接说明无法回答，不要尝试用知识库内容推测。",
        },
    ],
    "ambiguity": [
        {
            "symptom": "vague_query_no_clarification",
            "lesson": "用户提问包含模糊指代（如「分析苹果」）时，主动追问具体分析维度，而不是假设用户意图或输出泛泛而谈的内容。",
        },
    ],
    "tool_reliability": [
        {
            "symptom": "api_connection_failure",
            "lesson": "API 连接失败时，自动启用指数退避重试（最多 3 次），并降级到备用工具。",
        },
        {
            "symptom": "timeout",
            "lesson": "耗时操作（搜索、分析）需要足够超时配置（至少 30s），超时后降级而非直接报错。",
        },
        {
            "symptom": "redis_not_initialized",
            "lesson": "Redis 客户端采用懒初始化，首次使用时自动连接，不阻塞 Agent 启动。",
        },
    ],
}

# ═══════════════════════════════════════════════════════════════════════
# Symptom Detection
# ═══════════════════════════════════════════════════════════════════════


def _detect_scenario(category: str) -> str:
    """Map benchmark category to experience scenario name."""
    mapping = {
        "single_doc": "single_document",
        "cross_doc": "cross_document",
        "framework": "framework",
        "multi_step": "multi_step",
        "web_search": "web_search",
        "tool_recovery": "tool_recovery",
        "edge_case": "edge_case",
        "ambiguity": "ambiguity",
    }
    return mapping.get(category, category)


def _detect_symptom(case: dict) -> str:
    """Detect the specific failure symptom from case data using heuristics.

    Returns a symptom key that maps to a rule template in SCENARIO_RULES.
    """
    category = case.get("category", "")
    question = (case.get("question", "") or "").lower()
    answer = (case.get("actual_answer", "") or "").lower()
    keywords_missed = case.get("keywords_missed", [])

    # ── Cross-document ──
    if category == "cross_doc":
        if any(kw in answer for kw in ["未找到", "只找到", "缺少"]):
            return "only_one_document_used"
        if all(kw not in answer for kw in ["对比", "比较", "高于", "低于"]):
            return "no_comparison_made"
        if keywords_missed:
            return "keywords_missing"

    # ── Framework ──
    if category == "framework":
        framework_map = {
            "swot": ["优势", "劣势", "机会", "威胁"],
            "dupont": ["净利率", "资产周转率", "权益乘数"],
            "pest": ["政治", "经济", "社会", "技术"],
        }
        for fw_name, expected in framework_map.items():
            if fw_name in question:
                # All framework keywords are missing → framework not executed
                all_missing = all(kw in keywords_missed for kw in expected)
                if all_missing:
                    return f"keywords_missing_{fw_name}"
                # Some missing → incomplete framework
                if any(kw in keywords_missed for kw in expected):
                    return f"keywords_missing_{fw_name}"
                return "keywords_missing"  # fallback
        # Framework category but no specific framework detected in question
        return "framework_not_executed"

    # ── Web search ──
    if category == "web_search":
        if any(kw in answer for kw in ["未找到", "没有结果", "无法获取"]):
            return "no_web_results"
        if keywords_missed:
            return "keywords_missing"

    # ── Tool recovery ──
    if category == "tool_recovery":
        if "不存在" in answer and "文档清单" not in answer:
            return "not_found_no_alternative"
        if keywords_missed:
            return "keywords_missing"

    # ── Edge case ──
    if category == "edge_case":
        if "知识库" in question and len(answer or "") > 200:
            return "over_answers_simple_query"
        if keywords_missed:
            return "keywords_missing"

    # ── Ambiguity ──
    if category == "ambiguity":
        # Check if the answer is long and detailed without clarifying
        if len(answer or "") > 100 and "请问" not in answer and "哪个方面" not in answer:
            return "vague_query_no_clarification"
        return "vague_query_no_clarification"

    # ── Generic ──
    if keywords_missed:
        return "keywords_missing"
    return "unknown"


def _build_lesson(scenario: str, symptom: str, case: dict) -> str:
    """Build a lesson string. Prefers rule templates; falls back to generic."""
    # Check rule templates
    rules = SCENARIO_RULES.get(scenario, [])
    for rule in rules:
        if rule["symptom"] == symptom:
            return rule["lesson"]

    # Generic fallback: mention missing keywords
    keywords_missed = case.get("keywords_missed", [])
    if keywords_missed:
        kws = "、".join(keywords_missed[:5])
        return f"此类任务必须覆盖以下关键要素: {kws}。"

    question = case.get("question", "")[:60]
    return f"处理「{question}」这类问题时，需要输出更完整的内容，不能停留在中间步骤。"


def _compute_confidence(case: dict) -> float:
    """Compute initial confidence based on failure severity.

    Full failure (coverage < 0.4): confidence 0.6–0.95
    Partial (0.4 <= coverage < 0.8): confidence 0.4–0.7
    """
    coverage = case.get("keyword_coverage", 0.0)
    if coverage < 0.4:
        # Full failure: high confidence in the lesson
        base = max(0.6, min(0.95, 1.0 - coverage))
    else:
        # Partial: lower confidence (lesson may not fully apply)
        base = max(0.4, min(0.7, 1.0 - coverage))
    # Bonus for high-value categories
    category = case.get("category", "")
    if category in ("framework", "cross_doc", "multi_step"):
        base = min(0.95, base + 0.1)
    return round(base, 2)


def _build_tags(case: dict, scenario: str) -> list[str]:
    """Build tag list for traceability."""
    tags = [scenario]
    difficulty = case.get("difficulty", "")
    if difficulty:
        tags.append(f"difficulty:{difficulty}")
    return tags


# ═══════════════════════════════════════════════════════════════════════
# Negative Transfer Protection — Metadata Rules
# ═══════════════════════════════════════════════════════════════════════
# Each rule defines:
#   - applicable_to: scenarios this experience HELPS (empty = everywhere)
#   - avoid_for:     scenarios this experience HARMS (prevent injection)
#
# Rules map (scenario, symptom) → (applicable_to, avoid_for)
# ═══════════════════════════════════════════════════════════════════════

# Default: no restrictions — applicable everywhere, avoid nowhere
_DEFAULT_META = ([], [])

# Specific rules for high-risk experiences
_APPLICABILITY_RULES: dict[str, dict[str, tuple[list[str], list[str]]]] = {
    "edge_case": {
        "over_answers_simple_query": (
            ["edge_case_simple"],
            ["multi_step_analysis", "framework_analysis", "cross_document", "web_search"],
        ),
        "keywords_missing": (
            [],
            [],
        ),
    },
    "tool_recovery": {
        "not_found_no_alternative": (
            ["tool_recovery", "search_failure"],
            ["multi_step_analysis", "framework_analysis"],
        ),
        "keywords_missing": (
            [],
            [],
        ),
    },
    "ambiguity": {
        "vague_query_no_clarification": (
            ["ambiguity"],
            [],
        ),
    },
    "framework": {
        "keywords_missing_swot": (
            ["framework_analysis"],
            [],
        ),
        "keywords_missing_dupont": (
            ["framework_analysis"],
            [],
        ),
        "framework_not_executed": (
            ["framework_analysis"],
            [],
        ),
    },
    "cross_document": {
        "only_one_document_used": (
            ["cross_document"],
            ["edge_case_simple"],
        ),
        "no_comparison_made": (
            ["cross_document"],
            ["edge_case_simple"],
        ),
        "keywords_missing": (
            ["cross_document"],
            ["edge_case_simple"],
        ),
    },
    "web_search": {
        "no_web_results": (
            ["web_search"],
            [],
        ),
        "keywords_missing": (
            ["web_search"],
            [],
        ),
    },
    "tool_reliability": {
        "api_connection_failure": (
            [],
            [],
        ),
        "timeout": (
            [],
            [],
        ),
        "redis_not_initialized": (
            [],
            [],
        ),
    },
}


def _compute_applicability(scenario: str, symptom: str) -> tuple[list[str], list[str]]:
    """Compute (applicable_to, avoid_for) for a given scenario+symptom.

    Falls back to _DEFAULT_META if no specific rule exists.
    """
    scenario_rules = _APPLICABILITY_RULES.get(scenario, {})
    return scenario_rules.get(symptom, _DEFAULT_META)


# ═══════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════


async def extract_from_benchmark_failure(case: dict) -> Experience | None:
    """Extract a single Experience from a benchmark failure case.

    Args:
        case: A dict from benchmark/cases/failure/*.json

    Returns:
        An Experience if extraction succeeded AND the case is a clear
        failure (grade == 'failure' or coverage < 0.4).
        None for partial successes or insufficient data.
    """
    coverage = case.get("keyword_coverage", 1.0)

    # Only skip true successes (coverage >= 0.8)
    # Partial failures (0.4 <= coverage < 0.8) still have valuable lessons
    # Full failures (coverage < 0.4) are highest priority
    if coverage >= 0.8:
        return None  # Clean success; skip

    scenario = _detect_scenario(case.get("category", ""))
    symptom = _detect_symptom(case)
    lesson = _build_lesson(scenario, symptom, case)
    question_id = case.get("question_id", "")

    if not lesson:
        return None

    applicable_to, avoid_for = _compute_applicability(scenario, symptom)

    return Experience(
        scenario=scenario,
        symptom=symptom,
        lesson=lesson,
        confidence=_compute_confidence(case),
        source_benchmark_case=question_id,
        tags=_build_tags(case, scenario),
        applicable_to=applicable_to,
        avoid_for=avoid_for,
    )


async def extract_all_from_benchmark(
    failures_dir: str = "benchmark/cases/failure",
) -> int:
    """Scan both failure and partial benchmark case directories and extract experiences.

    Args:
        failures_dir: Path to the directory containing failure JSON files.
            The sibling directory 'partial' is also scanned.

    Returns:
        The number of experiences extracted and stored.
    """
    store = get_experience_store()
    scanned_dirs = [Path(failures_dir), Path(failures_dir).parent / "partial"]

    total = 0
    extracted = 0
    for dir_path in scanned_dirs:
        if not dir_path.exists():
            logger.debug("Skipping non-existent directory: %s", dir_path)
            continue
        for f in sorted(dir_path.glob("*.json")):
            total += 1
            try:
                case = json.loads(f.read_text(encoding="utf-8"))
                exp = await extract_from_benchmark_failure(case)
                if exp:
                    await store.add(exp)
                    extracted += 1
                    logger.info("Extracted: %s", exp.short_summary())
            except Exception as e:
                logger.warning("Failed to extract from %s: %s", f.name, e)

    logger.info(
        "Benchmark extraction complete: %d/%d failures → experiences",
        extracted,
        total,
    )
    return extracted


async def extract_from_runtime_error(
    error: str,
    query: str = "",
    trace_id: str = "",
) -> Experience | None:
    """Extract an experience from a runtime error during normal execution.

    This is called when a tool fails or the agent encounters an exception
    during production use.
    """
    error_lower = error.lower()

    if "apiconnection" in error_lower or "connection refused" in error_lower:
        return Experience(
            scenario="tool_reliability",
            symptom="api_connection_failure",
            lesson="API 连接失败时，自动启用指数退避重试（最多 3 次），并降级到备用工具。",
            confidence=0.8,
            source_trace_id=trace_id,
            tags=["tool_reliability", "auto_extracted"],
        )

    if "timeout" in error_lower:
        return Experience(
            scenario="tool_reliability",
            symptom="timeout",
            lesson="耗时操作（搜索、分析）需要足够超时配置（至少 30s），超时后降级而非直接报错。",
            confidence=0.7,
            source_trace_id=trace_id,
            tags=["tool_reliability", "auto_extracted"],
        )

    if "redis" in error_lower:
        return Experience(
            scenario="tool_reliability",
            symptom="redis_not_initialized",
            lesson="Redis 客户端采用懒初始化，首次使用时自动连接，不阻塞 Agent 启动。",
            confidence=0.75,
            source_trace_id=trace_id,
            tags=["tool_reliability", "auto_extracted"],
        )

    return None


async def extract_from_runtime_success(
    query: str,
    category: str,
    tool_sequence: list[str],
    result_quality: float,
) -> Experience | None:
    """Extract a positive experience from a successful execution.

    This lets the system learn what WORKS, not just what fails.
    High-quality executions are memorised as positive examples.
    """
    if result_quality < 0.8 or not tool_sequence:
        return None

    # Only keep patterns that appear frequently (called externally)
    return Experience(
        scenario=category,
        symptom="successful_pattern",
        lesson=f"对于「{query[:40]}」这类任务，有效的工具调用序列是: {' → '.join(tool_sequence)}。",
        confidence=0.6,  # starts lower; increases with verification
        tags=[category, "positive", "auto_extracted"],
    )
