"""Agent System Evaluation Harness — run test scenarios, collect traces, produce reports.

接线状态（#242 核实）：**dev-only harness** —— 全仓无运行时导入者，无 CLI 入口
（`quick_eval` 亦零调用者）。

Purpose:
    This is NOT a test framework in the traditional sense. It is a
    system-level evaluation harness that runs real Agent tasks through
    the Multi-Agent OS and measures:

    1. End-to-end success rate (did the system complete the task?)
    2. Tool reliability (which tools fail, how often, why?)
    3. Semantic effectiveness (do retry_safe / fallback actually help?)
    4. Planner accuracy (did the planner's tool hints match reality?)
    5. Execution efficiency (latency, retry overhead, parallelization)

Usage:
    from app.agent.evaluation import run_evaluation, load_scenarios

    scenarios = load_scenarios()
    report = await run_evaluation(scenarios)
    print(report.summary())
    report.save("eval_report.json")
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent.orchestrator import Orchestrator
from app.agent.tracing import (
    get_trace_store,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1.  Scenario Definitions
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class EvalScenario:
    """A single test scenario for the Agent system.

    Attributes:
        name:          Human-readable name (e.g. "query_feishu_approval").
        query:         The user query to send to the Agent.
        category:      Business domain ("feishu", "search", "mixed", ...).
        min_steps:     Minimum expected plan steps (for validation).
        max_steps:     Maximum expected plan steps (guard against runaway plans).
        expected_tools: Set of tools the system should ideally use.
        tags:          Free-form tags for filtering / grouping reports.
    """
    name: str
    query: str
    category: str = "general"
    min_steps: int = 1
    max_steps: int = 15
    expected_tools: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def load_scenarios() -> list[EvalScenario]:
    """Load the default set of evaluation scenarios.

    These cover the main tool categories and complexity levels.
    Add more scenarios as new tools are onboarded.
    """
    return [
        # ── Tool capability scenarios ──
        EvalScenario(
            name="feishu_query_basic",
            query="查询这个飞书多维表格 app_token=test123 table_id=tbl_demo 中的前10条记录",
            category="feishu",
            min_steps=1, max_steps=3,
            expected_tools=["feishu_bitable_query"],
            tags=["read_only", "feishu"],
        ),
        EvalScenario(
            name="feishu_query_filtered",
            query="查询飞书多维表格 app_token=test456 table_id=tbl_approval 中状态为'待审批'的记录，返回20条",
            category="feishu",
            min_steps=1, max_steps=3,
            expected_tools=["feishu_bitable_query"],
            tags=["read_only", "feishu", "filter"],
        ),
        # ── Knowledge base scenarios ──
        EvalScenario(
            name="kb_search_simple",
            query="搜索知识库中关于合同风险的内容",
            category="search",
            min_steps=1, max_steps=3,
            expected_tools=["search_knowledge_base"],
            tags=["read_only", "search", "kb"],
        ),
        EvalScenario(
            name="kb_vector_search",
            query="用语义搜索查找与'人工智能投资策略'相似的文档",
            category="search",
            min_steps=1, max_steps=3,
            expected_tools=["vector_search"],
            tags=["read_only", "search", "semantic"],
        ),
        # ── Document analysis scenarios ──
        EvalScenario(
            name="doc_summarize",
            query="总结文档 doc_001 的主要内容",
            category="analysis",
            min_steps=1, max_steps=4,
            expected_tools=["summarize_document"],
            tags=["analysis", "document"],
        ),
        EvalScenario(
            name="doc_compare",
            query="比较文档 doc_001 和 doc_002 的异同",
            category="analysis",
            min_steps=2, max_steps=6,
            expected_tools=["summarize_document", "cross_document_analysis"],
            tags=["analysis", "compare", "multi_step"],
        ),
        # ── Mixed / multi-tool scenarios ──
        EvalScenario(
            name="feishu_then_kb",
            query="先查飞书多维表格 app_token=test123 table_id=tbl_demo 的待审批记录，再搜索知识库中相关的审批流程文档",
            category="mixed",
            min_steps=2, max_steps=6,
            expected_tools=["feishu_bitable_query", "search_knowledge_base"],
            tags=["multi_step", "mixed", "cross_system"],
        ),
        EvalScenario(
            name="multi_query_parallel",
            query="同时查询飞书多维表格 app_token=test_a table_id=tbl_1 中的项目和 app_token=test_b table_id=tbl_2 中的任务",
            category="mixed",
            min_steps=2, max_steps=6,
            expected_tools=["feishu_bitable_query"],
            tags=["multi_step", "parallel", "mixed"],
        ),
        # ── Edge cases ──
        EvalScenario(
            name="empty_query",
            query="你好",
            category="general",
            min_steps=0, max_steps=2,
            expected_tools=[],
            tags=["edge", "simple"],
        ),
        EvalScenario(
            name="complex_analysis",
            query="分析 doc_001 中提到的风险因素，搜索知识库中关于风险管理的资料，汇总一份风险评估报告",
            category="analysis",
            min_steps=3, max_steps=8,
            expected_tools=["extract_insights", "search_knowledge_base", "generate_report"],
            tags=["complex", "multi_step", "analysis"],
        ),
    ]


# ──────────────────────────────────────────────────────────────────────────────
# 2.  Evaluation Report
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ScenarioResult:
    """Result of running a single scenario."""
    scenario_name: str
    category: str
    query: str
    passed: bool = False
    trace_id: str = ""
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    duration_ms: float = 0.0
    tools_used: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario_name,
            "category": self.category,
            "passed": self.passed,
            "trace_id": self.trace_id,
            "steps": f"{self.completed_steps}/{self.total_steps}",
            "duration_ms": round(self.duration_ms, 1),
            "tools_used": self.tools_used,
            "failures": self.failures[:3],
        }


@dataclass
class EvalReport:
    """Full evaluation report — aggregate metrics + per-scenario results.

    This is the primary output of the evaluation harness.
    """
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_scenarios: int = 0
    passed: int = 0
    failed: int = 0
    scenarios: list[ScenarioResult] = field(default_factory=list)

    # Aggregate metrics (computed after all scenarios run)
    overall_success_rate: float = 0.0
    avg_duration_ms: float = 0.0
    total_steps_planned: int = 0
    total_steps_completed: int = 0

    # Per-category breakdown
    by_category: dict[str, dict[str, float | int]] = field(default_factory=dict)

    # Tool-level metrics
    tool_metrics: dict[str, dict[str, float | int]] = field(default_factory=dict)

    # Semantic effectiveness (from TraceStore)
    semantic_effectiveness: dict[str, Any] = field(default_factory=dict)

    # Planner accuracy (from TraceStore)
    planner_accuracy: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        """One-page text summary for quick review."""
        lines = [
            f"{'='*60}",
            "  Agent System Evaluation Report",
            f"  {self.timestamp}",
            f"{'='*60}",
            f"  Overall: {self.passed}/{self.total_scenarios} passed ({self.overall_success_rate:.0%})",
            f"  Avg duration: {self.avg_duration_ms:.0f}ms",
            f"  Steps: {self.total_steps_completed}/{self.total_steps_planned} completed",
            "",
        ]

        # Per-category
        if self.by_category:
            lines.append("  ── By Category ──")
            for cat, m in sorted(self.by_category.items()):
                lines.append(f"    {cat}: {m['passed']}/{m['total']} ({m['success_rate']:.0%})")

        # Tool metrics
        if self.tool_metrics:
            lines.append("")
            lines.append("  ── Tool Reliability ──")
            for tname, m in sorted(self.tool_metrics.items()):
                lines.append(f"    {tname}: {m['success_rate']:.0%} success ({m['total_calls']} calls, {m['failed_calls']} failed)")

        # Semantic effectiveness
        if self.semantic_effectiveness:
            lines.append("")
            lines.append("  ── Semantic Effectiveness ──")
            se = self.semantic_effectiveness
            lines.append(f"    Retry success rate: {se.get('retry_success_rate', 'N/A')}")
            lines.append(f"    Fallback success rate: {se.get('fallback_success_rate', 'N/A')}")

        # Planner accuracy
        if self.planner_accuracy:
            lines.append("")
            lines.append("  ── Planner Accuracy ──")
            pa = self.planner_accuracy
            lines.append(f"    Tool hint accuracy: {pa.get('hint_accuracy', 'N/A')}")

        # Failures
        failed_scenarios = [s for s in self.scenarios if not s.passed]
        if failed_scenarios:
            lines.append("")
            lines.append(f"  ── Failures ({len(failed_scenarios)}) ──")
            for s in failed_scenarios[:5]:
                fail_reasons = "; ".join(s.failures[:2]) if s.failures else "unknown"
                lines.append(f"    [{s.scenario_name}] {fail_reasons}")

        lines.append(f"{'='*60}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_scenarios": self.total_scenarios,
            "passed": self.passed,
            "failed": self.failed,
            "overall_success_rate": round(self.overall_success_rate, 3),
            "avg_duration_ms": round(self.avg_duration_ms, 1),
            "total_steps_planned": self.total_steps_planned,
            "total_steps_completed": self.total_steps_completed,
            "by_category": self.by_category,
            "tool_metrics": self.tool_metrics,
            "semantic_effectiveness": self.semantic_effectiveness,
            "planner_accuracy": self.planner_accuracy,
            "scenarios": [s.to_dict() for s in self.scenarios],
        }

    def save(self, path: str = "eval_report.json") -> str:
        """Save report to JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info("Evaluation report saved to %s", path)
        return path


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Evaluation Runner
# ──────────────────────────────────────────────────────────────────────────────


async def run_evaluation(
    scenarios: list[EvalScenario] | None = None,
    orchestrator: Orchestrator | None = None,
    clear_traces: bool = True,
) -> EvalReport:
    """Run evaluation scenarios and produce a report.

    Args:
        scenarios:   List of scenarios to run. Defaults to load_scenarios().
        orchestrator: Orchestrator instance. Creates one if not provided.
        clear_traces: If True, clears TraceStore before running.

    Returns:
        EvalReport with per-scenario results + aggregate metrics.
    """
    if scenarios is None:
        scenarios = load_scenarios()

    trace_store = get_trace_store()
    if clear_traces:
        trace_store.clear()

    report = EvalReport(total_scenarios=len(scenarios))

    for i, scenario in enumerate(scenarios):
        logger.info("Running scenario %d/%d: %s", i + 1, len(scenarios), scenario.name)
        result = await _run_single_scenario(scenario, orchestrator)
        report.scenarios.append(result)

        if result.passed:
            report.passed += 1
        else:
            report.failed += 1

        report.total_steps_planned += result.total_steps
        report.total_steps_completed += result.completed_steps

    # ── Compute aggregate metrics ──
    report.overall_success_rate = report.passed / report.total_scenarios if report.total_scenarios else 0.0
    durations = [s.duration_ms for s in report.scenarios]
    report.avg_duration_ms = sum(durations) / len(durations) if durations else 0.0

    # Per-category breakdown
    cat_map: dict[str, dict] = {}
    for s in report.scenarios:
        if s.category not in cat_map:
            cat_map[s.category] = {"total": 0, "passed": 0, "failed": 0, "total_duration": 0.0}
        cat_map[s.category]["total"] += 1
        cat_map[s.category]["passed"] += 1 if s.passed else 0
        cat_map[s.category]["failed"] += 1 if not s.passed else 0
        cat_map[s.category]["total_duration"] += s.duration_ms

    report.by_category = {
        cat: {
            "total": m["total"],
            "passed": m["passed"],
            "failed": m["failed"],
            "success_rate": round(m["passed"] / m["total"], 3) if m["total"] else 0.0,
            "avg_duration_ms": round(m["total_duration"] / m["total"], 1) if m["total"] else 0.0,
        }
        for cat, m in sorted(cat_map.items())
    }

    # Tool-level metrics from TraceStore
    tool_stats = trace_store.get_failure_stats()
    if tool_stats and tool_stats.get("total_tool_calls", 0) > 0:
        by_tool = tool_stats.get("by_tool", {})
        report.tool_metrics = {
            tname: {
                "total_calls": m["total"],
                "failed_calls": m["failed"],
                "success_rate": round(1 - (m["failed"] / m["total"]), 3) if m["total"] else 0.0,
                "top_errors": dict(sorted(m["error_codes"].items(), key=lambda x: -x[1])[:3])
                    if m.get("error_codes") else {},
            }
            for tname, m in sorted(by_tool.items())
        }

    # Semantic effectiveness
    report.semantic_effectiveness = trace_store.get_semantic_effectiveness()

    # Planner accuracy
    report.planner_accuracy = trace_store.get_planner_executor_diff()

    return report


def _has_api_key() -> bool:
    """Check if any LLM API key is available for planning."""
    import os
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_ADMIN_KEY"))


TOOL_KEYWORDS: dict[str, str] = {
    "多维表格": "feishu_bitable_query",
    "飞书": "feishu_bitable_query",
    "搜索": "search_knowledge_base",
    "语义搜索": "vector_search",
    "向量搜索": "vector_search",
    "相似": "vector_search",
    "摘要": "summarize_document",
    "总结": "summarize_document",
    "对比": "cross_document_analysis",
    "比较": "cross_document_analysis",
    "异同": "cross_document_analysis",
    "分析": "extract_insights",
    "风险": "extract_insights",
    "报告": "generate_report",
    "时间": "get_current_time",
    "天气": "get_system_status",
}


class MockPlanner:
    """Planner that generates deterministic plans from query text, no LLM needed.

    Matches query keywords to registered tools using TOOL_KEYWORDS.
    Produces multiple steps for queries containing multiple keywords.

    Used by the evaluation harness when no API key is available.
    """
    def __init__(self, config=None):
        self.config = config or type("Cfg", (), {"tool_tags": [], "disabled_tools": []})()
        self.client = None

    async def plan(self, query: str = "", history=None, ctx=None):
        """Yields plan_step events with tool_hints derived from query keywords."""
        from app.agent.events import AgentEvent

        yield AgentEvent(type="plan_start", plan_id="mock", content="Mock plan")

        # Tokenize query for keyword matching (Chinese + English)
        # Simple approach: match known keywords
        matched_tools = []
        q_lower = query.lower()
        for kw, tool in TOOL_KEYWORDS.items():
            if kw.lower() in q_lower or kw in query:
                if tool not in matched_tools:
                    matched_tools.append(tool)

        # If no tools matched, create a single generic step
        if not matched_tools:
            yield AgentEvent(
                type="plan_step", plan_id="mock", plan_step_id="s1",
                content=query[:100],
                dependencies=[], tool_hint="",
                plan_progress=0.0,
            )
        else:
            for i, tool_name in enumerate(matched_tools):
                yield AgentEvent(
                    type="plan_step", plan_id="mock",
                    plan_step_id=f"s{i + 1}",
                    content=f"使用 {tool_name} 完成: {query[:60]}",
                    dependencies=[],
                    tool_hint=tool_name,
                    plan_progress=(i + 1) / len(matched_tools),
                )

        yield AgentEvent(
            type="plan_complete", plan_id="mock",
            content=f"Mock plan: {len(matched_tools) or 1} step(s)",
            plan_progress=1.0,
        )


async def _run_single_scenario(
    scenario: EvalScenario,
    orchestrator: Orchestrator | None = None,
) -> ScenarioResult:
    """Run one scenario through the system and return the result."""
    import uuid

    result = ScenarioResult(
        scenario_name=scenario.name,
        category=scenario.category,
        query=scenario.query,
        tags=scenario.tags,
        trace_id=uuid.uuid4().hex[:12],
    )
    start = time.perf_counter()

    try:
        if orchestrator is None:
            from openai import AsyncOpenAI

            from app.agent.config import AgentConfig
            from app.agent.planner import Planner

            # Minimal config for evaluation
            config = AgentConfig(
                enable_planning=True,
                enable_reflection=False,
                enable_memory=False,
                enable_tools=True,
                max_plan_steps=10,
                max_retries_per_step=2,
                max_tool_calls_per_turn=3,
            )

            # Use MockPlanner if no API key available
            if _has_api_key():
                client = AsyncOpenAI()
                planner: Planner | MockPlanner = Planner(openai_client=client, config=config)
            else:
                planner = MockPlanner(config=config)

            orch = Orchestrator(
                planner=planner,
                config=config,
                organization_id=1,
                user_id=0,
            )
        else:
            orch = orchestrator

        # Run the scenario
        final_output = ""
        async for event in orch.run(scenario.query, trace_id=result.trace_id):
            if event.type == "error":
                result.failures.append(event.content[:200])
            elif event.type == "chunk":
                final_output += event.content

        elapsed = (time.perf_counter() - start) * 1000
        result.duration_ms = elapsed

        # Collect trace data
        trace_store = get_trace_store()
        traces = trace_store.get_recent(5)
        our_trace = None
        for t in traces:
            if result.trace_id in (t.trace_id, "") and t.query == scenario.query[:100]:
                our_trace = t
                break

        if our_trace:
            result.trace_id = our_trace.trace_id
            result.total_steps = our_trace.total_steps
            result.completed_steps = our_trace.completed_steps
            result.failed_steps = our_trace.failed_steps
            result.tools_used = our_trace.tools_used
        else:
            # Use scenario defaults
            pass

        # Determine pass/fail
        result.passed = _evaluate_scenario(scenario, result)

    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        result.duration_ms = elapsed
        result.failures.append(f"Exception: {e}")
        result.passed = False

    return result


def _evaluate_scenario(scenario: EvalScenario, result: ScenarioResult) -> bool:
    """Evaluate whether a scenario result meets expectations.

    Criteria:
        - Step count within [min_steps, max_steps]
        - No unexpected failures (tool errors count as failures)
        - Expected tools were used (if specified)
    """
    # Step count check
    if result.total_steps < scenario.min_steps:
        result.failures.append(
            f"Too few steps: {result.total_steps} < min {scenario.min_steps}"
        )
        return False

    if result.total_steps > scenario.max_steps:
        result.failures.append(
            f"Too many steps: {result.total_steps} > max {scenario.max_steps}"
        )
        return False

    # Failure check
    if result.failed_steps > 0:
        result.failures.append(
            f"{result.failed_steps} step(s) failed"
        )
        return False

    # Expected tools check
    if scenario.expected_tools:
        used_set = set(result.tools_used)
        expected_set = set(scenario.expected_tools)
        missing = expected_set - used_set
        if missing:
            result.failures.append(
                f"Missing expected tools: {missing}"
            )
            return False

    return True


# ──────────────────────────────────────────────────────────────────────────────
# 4.  Utility: Quick Run
# ──────────────────────────────────────────────────────────────────────────────


async def quick_eval() -> EvalReport:
    """Run a quick evaluation with all default scenarios and print summary."""
    report = await run_evaluation()
    print(report.summary())
    return report
