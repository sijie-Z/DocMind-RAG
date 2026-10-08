"""Optimization Loop Controller — system self-improvement layer.

接线状态（#242 核实）：**dev-only** —— 全仓零导入者（依赖的 TraceStore 亦仅评测
harness 写入）。

What this provides:
    Analyzes TraceStore data and evaluation reports to find specific,
    actionable improvements to the Agent system. Tracks before/after
    metrics so you can verify that changes actually help.

How it works:
    1. Read all traces from TraceStore
    2. Compute metrics (success rate, latency, tool reliability, etc.)
    3. Compare against thresholds
    4. Generate ranked optimization suggestions
    5. Take snapshot for before/after comparison

Usage:
    ctrl = OptimizationController()
    report = ctrl.analyze()
    for s in report.suggestions:
        print(f"[{s.impact}] {s.title}: {s.action}")

    # Before making changes:
    before = ctrl.snapshot()

    # ... make changes ...

    # After changes:
    after = ctrl.snapshot()
    diff = ctrl.compare(before, after)
"""

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.agent.tracing import get_trace_store

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1.  Suggestion Model
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Suggestion:
    """A single, actionable optimization suggestion.

    Attributes:
        title:       Short description ("Optimize feishu_bitable_query retry").
        area:        Which subsystem to change: "planner_prompt" | "executor_policy" | "tool_wrapper" | "semantic_config" | "eval_scenario"
        impact:      "critical" | "high" | "medium" | "low"
        confidence:  How sure we are this will help (0.0 - 1.0)
        metric:      The metric that triggered this ("feishu_bitable_query failure rate = 0.6")
        action:      What to do, concretely
        detail:      Supporting data / evidence
    """
    title: str
    area: str
    impact: str = "medium"
    confidence: float = 0.5
    metric: str = ""
    action: str = ""
    detail: str = ""


# ──────────────────────────────────────────────────────────────────────────────
# 2.  Analysis Report
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class OptimizationReport:
    """Analysis of the current system state with improvement suggestions."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_traces_analyzed: int = 0

    # Current metrics (from TraceStore)
    overall_success_rate: float = 0.0
    avg_tool_latency_ms: float = 0.0
    total_tool_calls: int = 0
    failed_tool_calls: int = 0

    # Per-tool metrics
    tool_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Planner accuracy
    planner_hint_accuracy: float = 0.0
    planner_analyzed_steps: int = 0

    # Semantic effectiveness
    retry_success_rate: float = 0.0
    fallback_success_rate: float = 0.0
    parallel_groups_used: int = 0

    # Suggestions (ranked by impact then confidence)
    suggestions: list[Suggestion] = field(default_factory=list)

    @property
    def critical_count(self) -> int:
        return sum(1 for s in self.suggestions if s.impact == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for s in self.suggestions if s.impact == "high")

    def summary(self) -> str:
        lines = [
            f"{'='*60}",
            "  System Optimization Report",
            f"  {self.timestamp}",
            f"{'='*60}",
            f"  Traces analyzed: {self.total_traces_analyzed}",
            f"  Overall success rate: {self.overall_success_rate:.1%}",
            f"  Tool calls: {self.total_tool_calls} total, {self.failed_tool_calls} failed",
            f"  Planner hint accuracy: {self.planner_hint_accuracy:.1%}",
            f"  Retry success rate: {self.retry_success_rate:.1%}",
            f"  Fallback success rate: {self.fallback_success_rate:.1%}",
            "",
        ]
        if self.suggestions:
            lines.append(f"  Optimization Suggestions ({len(self.suggestions)}):")
            for s in self.suggestions:
                lines.append(f"    [{s.impact.upper():8s}] {s.title}")
                lines.append(f"           {s.action}")
        else:
            lines.append("  No optimization suggestions — system performing well.")

        lines.append(f"{'='*60}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "total_traces_analyzed": self.total_traces_analyzed,
            "overall_success_rate": round(self.overall_success_rate, 3),
            "avg_tool_latency_ms": round(self.avg_tool_latency_ms, 1),
            "total_tool_calls": self.total_tool_calls,
            "failed_tool_calls": self.failed_tool_calls,
            "planner_hint_accuracy": round(self.planner_hint_accuracy, 3),
            "retry_success_rate": round(self.retry_success_rate, 3),
            "fallback_success_rate": round(self.fallback_success_rate, 3),
            "suggestions": [asdict(s) for s in self.suggestions],
        }


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Snapshot for Before/After Comparison
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class Snapshot:
    """Point-in-time metrics snapshot for before/after comparison."""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    total_traces: int = 0
    success_rate: float = 0.0
    avg_latency_ms: float = 0.0
    tool_call_count: int = 0
    tool_failure_count: int = 0
    planner_accuracy: float = 0.0
    retry_success_rate: float = 0.0
    suggestions_count: int = 0


@dataclass
class DiffResult:
    """Difference between two snapshots."""
    before: Snapshot = field(default_factory=Snapshot)
    after: Snapshot = field(default_factory=Snapshot)
    success_rate_change: float = 0.0
    latency_change_ms: float = 0.0
    failure_change: int = 0
    planner_accuracy_change: float = 0.0
    retry_success_change: float = 0.0
    suggestions_resolved: int = 0
    new_suggestions: int = 0

    def summary(self) -> str:
        lines = [
            f"{'='*60}",
            "  Before/After Comparison",
            f"{'='*60}",
            f"  Success rate: {self.before.success_rate:.1%} → {self.after.success_rate:.1%} ({self.success_rate_change:+.1%})",
            f"  Avg latency: {self.before.avg_latency_ms:.0f}ms → {self.after.avg_latency_ms:.0f}ms ({self.latency_change_ms:+.0f}ms)",
            f"  Tool failures: {self.before.tool_failure_count} → {self.after.tool_failure_count} ({self.failure_change:+d})",
            f"  Planner accuracy: {self.before.planner_accuracy:.1%} → {self.after.planner_accuracy:.1%} ({self.planner_accuracy_change:+.1%})",
            f"  Suggestions: {self.suggestions_resolved} resolved, {self.new_suggestions} new",
            f"{'='*60}",
        ]
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# 4.  Optimization Controller
# ──────────────────────────────────────────────────────────────────────────────


class OptimizationController:
    """Analyzes system state and generates optimization suggestions.

    Connects to TraceStore to read real execution data, computes metrics,
    compares against thresholds, and produces ranked suggestions.
    """

    # Thresholds that trigger suggestions
    THRESHOLDS = {
        "tool_failure_rate": 0.3,            # >30% → critical
        "planner_accuracy": 0.6,             # <60% → high
        "retry_success_rate": 0.7,           # <70% → high
        "fallback_success_rate": 0.5,        # <50% → medium
        "overall_success_rate": 0.7,         # <70% → critical
    }

    def __init__(self):
        self._store = get_trace_store()

    def analyze(self) -> OptimizationReport:
        """Analyze current trace data and produce optimization suggestions."""
        stats = self._store.get_failure_stats()
        semantic = self._store.get_semantic_effectiveness()
        planner = self._store.get_planner_executor_diff()
        traces = self._store.get_all()

        report = OptimizationReport(
            total_traces_analyzed=len(traces),
            overall_success_rate=1 - (stats.get("failed_traces", 0) / max(len(traces), 1)),
            total_tool_calls=stats.get("total_tool_calls", 0),
            failed_tool_calls=stats.get("failed_tool_calls", 0),
            planner_hint_accuracy=planner.get("hint_accuracy", 0.0),
            planner_analyzed_steps=planner.get("total_steps", 0),
            retry_success_rate=semantic.get("retry_success_rate", 0.0),
            fallback_success_rate=semantic.get("fallback_success_rate", 0.0),
            parallel_groups_used=semantic.get("parallel_groups_total", 0),
            tool_metrics=stats.get("by_tool", {}),
        )

        # Compute average latency
        latencies = []
        for t in traces:
            for step in t.steps:
                for tc in step.tool_calls:
                    if tc.latency_ms > 0:
                        latencies.append(tc.latency_ms)
        report.avg_tool_latency_ms = sum(latencies) / len(latencies) if latencies else 0.0

        # Generate suggestions
        report.suggestions = self._generate_suggestions(stats, semantic, planner, report)
        report.suggestions.sort(key=lambda s: (
            {"critical": 0, "high": 1, "medium": 2, "low": 3}[s.impact], -s.confidence
        ))

        return report

    # ── Suggestion Generators ────────────────────────────────────────────

    def _generate_suggestions(self, stats, semantic, planner, report) -> list[Suggestion]:
        suggestions = []

        # 1. Per-tool failure rate
        by_tool = stats.get("by_tool", {})
        for tname, m in by_tool.items():
            total = m.get("total", 0)
            failed = m.get("failed", 0)
            if total < 2:
                continue  # not enough data
            rate = failed / total
            if rate >= self.THRESHOLDS["tool_failure_rate"]:
                top_errors = dict(sorted(m.get("error_codes", {}).items(), key=lambda x: -x[1])[:2])
                suggestions.append(Suggestion(
                    title=f"High failure rate in {tname}",
                    area="tool_wrapper" if not self._is_auth_error(top_errors) else "executor_policy",
                    impact="critical" if rate >= 0.5 else "high",
                    confidence=min(0.9, 0.5 + rate),
                    metric=f"{tname} failure rate = {rate:.0%} ({failed}/{total})",
                    action=self._suggest_tool_fix(tname, top_errors, rate),
                    detail=f"Errors: {top_errors}",
                ))

        # 2. Planner accuracy
        if report.planner_analyzed_steps >= 3:
            acc = report.planner_hint_accuracy
            if acc < self.THRESHOLDS["planner_accuracy"]:
                suggestions.append(Suggestion(
                    title="Planner tool hint accuracy below threshold",
                    area="planner_prompt",
                    impact="high",
                    confidence=min(0.8, 0.4 + (1 - acc)),
                    metric=f"Planner hint accuracy = {acc:.0%} ({report.planner_analyzed_steps} steps)",
                    action="Update planner prompt: add tool descriptions with concrete usage examples. "
                           "Ensure semantic annotations (read_only, retry_safe) are mentioned in tool descriptions.",
                    detail=f"Current accuracy: {acc:.1%}. Target: >= {self.THRESHOLDS['planner_accuracy']:.0%}",
                ))

        # 3. Retry effectiveness
        retry_rate = report.retry_success_rate
        retried = semantic.get("retried_steps", 0)
        if retried >= 2 and retry_rate < self.THRESHOLDS["retry_success_rate"]:
            suggestions.append(Suggestion(
                title="Retry strategy underperforming",
                area="executor_policy",
                impact="high",
                confidence=min(0.8, 0.4 + (1 - retry_rate)),
                metric=f"Retry success rate = {retry_rate:.0%} ({retried} retried steps)",
                action="Increase max_retries_per_step or reduce backoff delay for auto_retry tools. "
                       "Consider adding exponential backoff with jitter.",
                detail=f"Current rate: {retry_rate:.1%}. Target: >= {self.THRESHOLDS['retry_success_rate']:.0%}",
            ))

        # 4. Fallback effectiveness
        fb_rate = report.fallback_success_rate
        fb_attempts = semantic.get("fallback_attempts", 0)
        if fb_attempts >= 2 and fb_rate < self.THRESHOLDS["fallback_success_rate"]:
            suggestions.append(Suggestion(
                title="Fallback mechanism rarely succeeds",
                area="tool_wrapper",
                impact="medium",
                confidence=min(0.7, 0.3 + (1 - fb_rate)),
                metric=f"Fallback success rate = {fb_rate:.0%} ({fb_attempts} attempts)",
                action="Review fallback_tool assignments. Ensure fallback tools cover the same capability. "
                       "Consider adding semantic annotations to fallback tools.",
                detail=f"Current rate: {fb_rate:.1%}. Target: >= {self.THRESHOLDS['fallback_success_rate']:.0%}",
            ))

        # 5. Overall success rate
        if report.total_traces_analyzed >= 3:
            sr = report.overall_success_rate
            if sr < self.THRESHOLDS["overall_success_rate"]:
                suggestions.append(Suggestion(
                    title="Overall system success rate below threshold",
                    area="executor_policy",
                    impact="critical",
                    confidence=0.9,
                    metric=f"Overall success rate = {sr:.0%} ({report.total_traces_analyzed} traces)",
                    action="Systematic review needed. Start with the highest-failure tool and work down. "
                           "Check if failures are clustered in specific scenarios.",
                    detail=f"Current rate: {sr:.1%}. Target: >= {self.THRESHOLDS['overall_success_rate']:.0%}",
                ))

        # 6. Missing trace data
        if report.total_traces_analyzed < 3:
            suggestions.append(Suggestion(
                title="Insufficient trace data for analysis",
                area="eval_scenario",
                impact="medium",
                confidence=1.0,
                metric=f"Only {report.total_traces_analyzed} traces available",
                action="Run more evaluation scenarios to generate trace data. "
                       "Target at least 10 traces for meaningful optimization.",
                detail="",
            ))

        return suggestions

    def _is_auth_error(self, error_codes: dict) -> bool:
        """Check if most errors are auth-related."""
        auth_codes = {"token_expired", "auth_config_error", "auth_error", "permission_denied"}
        return any(code in auth_codes for code in error_codes)

    def _suggest_tool_fix(self, tname: str, top_errors: dict, rate: float) -> str:
        if self._is_auth_error(top_errors):
            return (
                f"Review auth_handler for {tname}: most failures are auth-related. "
                f"Check token refresh logic and cache TTL."
            )
        # Check if rate_limited is dominant
        if "rate_limited" in top_errors:
            return (
                f"Reduce rate_limit value for {tname} or add client-side rate limiting. "
                f"Current errors suggest API-level throttling."
            )
        if "timeout" in top_errors:
            return (
                f"Increase timeout for {tname} or reduce page_size. "
                f"Current errors suggest queries exceed the timeout window."
            )
        return (
            f"Investigate {tname} failure pattern. "
            f"Dominant errors: {top_errors}. "
            f"Add error-specific handling in the tool wrapper."
        )

    # ── Snapshot / Comparison ────────────────────────────────────────────

    def snapshot(self) -> Snapshot:
        """Take a point-in-time metrics snapshot."""
        stats = self._store.get_failure_stats()
        semantic = self._store.get_semantic_effectiveness()
        planner = self._store.get_planner_executor_diff()

        return Snapshot(
            total_traces=len(self._store.get_all()),
            success_rate=1 - (stats.get("failed_traces", 0) / max(len(self._store.get_all()), 1)),
            tool_call_count=stats.get("total_tool_calls", 0),
            tool_failure_count=stats.get("failed_tool_calls", 0),
            planner_accuracy=planner.get("hint_accuracy", 0.0),
            retry_success_rate=semantic.get("retry_success_rate", 0.0),
        )

    def compare(self, before: Snapshot, after: Snapshot) -> DiffResult:
        """Compare two snapshots and produce diff."""
        return DiffResult(
            before=before,
            after=after,
            success_rate_change=after.success_rate - before.success_rate,
            latency_change_ms=after.avg_latency_ms - before.avg_latency_ms,
            failure_change=after.tool_failure_count - before.tool_failure_count,
            planner_accuracy_change=after.planner_accuracy - before.planner_accuracy,
            retry_success_change=after.retry_success_rate - before.retry_success_rate,
        )


def auto_optimize() -> OptimizationReport:
    """Run optimization analysis and print suggestions."""
    ctrl = OptimizationController()
    report = ctrl.analyze()
    print(report.summary())
    return report
