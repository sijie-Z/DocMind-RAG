"""Execution Observability Layer — structured trace recording for the Multi-Agent OS.

接线状态（#242 核实）：**dev-only** —— 写入者为评测 harness 路径（orchestrator /
executor_agent，均无生产调用者；后者还有一处零读者的 `_pending_step` 悬挂写）；
生产 run 不写本 store。运行时轨迹由 Run Report / `execution_context` SSE /
ExecutionContext（Redis 7 天 → benchmark/mining 离线消费）承担。

What this provides:
    ExecutionTrace       — full lineage of one user request (plan → steps → tool calls → results)
    TraceStore           — in-memory ring buffer with query/filter/aggregate
    failure taxonomy     — classify failures by type, tool, semantic annotation
    semantic effectiveness — measure whether retry_safe / parallel_group actually help

Why this matters:
    Without observability, you can't know if your semantic annotations work,
    which tools fail most, or whether retry strategies actually reduce failures.
    This layer turns the system from "black box" into "analyzable system".

Usage:
    store = TraceStore()
    trace = ExecutionTrace(trace_id="abc", query="查询飞书审批")
    store.record(trace)

    # Later analysis:
    stats = store.get_failure_stats()
    eff = store.get_semantic_effectiveness()
    tool_rank = store.get_tool_failure_ranking()
"""

import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# 1.  Data Models
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolCallRecord:
    """A single tool invocation during execution."""
    tool_name: str
    args_summary: str                     # truncated args for readability
    success: bool
    error_code: str | None = None         # token_expired | rate_limited | timeout | api_error | ...
    error_message: str | None = None
    latency_ms: float = 0.0
    retry_attempt: int = 0                # 0 = first try, 1+ = retry
    fallback_used: bool = False           # was this a fallback tool call?


@dataclass
class StepExecutionRecord:
    """Record of one plan step's full execution lifecycle."""
    step_id: str
    description: str
    tool_hint: str | None = None
    semantic_type: str = "read_only"      # read_only | mutating | long_running
    retry_strategy: str = "auto_retry"
    risk_level: str = "low"
    parallel_group: int | None = None

    status: str = "pending"               # completed | failed | skipped
    duration_ms: float = 0.0
    retry_count: int = 0
    fallback_used: bool = False

    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    error: str | None = None


@dataclass
class ExecutionTrace:
    """Full trace of one user request through the Multi-Agent OS.

    Captures the complete lineage:
        query → plan → steps → tool calls → results

    This is the primary unit of observability.
    """
    trace_id: str
    query: str
    created_at: float = field(default_factory=time.time)

    plan_goal: str = ""
    plan_reasoning: str = ""
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0

    steps: list[StepExecutionRecord] = field(default_factory=list)
    total_duration_ms: float = 0.0
    overall_success: bool = True

    # Tags for filtering / search
    tools_used: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)  # feishu, dingtalk, search, ...

    # Planner-Executor diff tracking
    # How many tools the planner suggested vs how many actually got called
    planner_tool_hints: int = 0
    executor_tool_calls: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "query": self.query[:200],
            "created_at": datetime.fromtimestamp(self.created_at).isoformat(),
            "plan_goal": self.plan_goal[:100],
            "total_steps": self.total_steps,
            "completed_steps": self.completed_steps,
            "failed_steps": self.failed_steps,
            "skipped_steps": self.skipped_steps,
            "total_duration_ms": round(self.total_duration_ms, 1),
            "overall_success": self.overall_success,
            "tools_used": self.tools_used,
            "categories": list(set(self.categories)),
            "planner_tool_hints": self.planner_tool_hints,
            "executor_tool_calls": self.executor_tool_calls,
            "steps": [asdict(s) for s in self.steps],
        }

    def summary(self) -> str:
        """One-line summary for quick debugging."""
        status = "✅" if self.overall_success else "❌"
        tools = ", ".join(sorted(set(self.tools_used))) if self.tools_used else "(none)"
        return (
            f"{status} [{self.trace_id[:8]}] {self.query[:60]} "
            f"| {self.completed_steps}/{self.total_steps} steps "
            f"in {self.total_duration_ms:.0f}ms "
            f"| tools: {tools}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# 2.  Trace Store
# ──────────────────────────────────────────────────────────────────────────────


class TraceStore:
    """In-memory ring buffer of execution traces.

    Stores the last N traces for analysis. Oldest traces are evicted
    when the buffer is full. All analytics methods operate on the
    current buffer contents.

    Thread-safe for single-threaded async use (not designed for
    concurrent writers).
    """

    def __init__(self, max_traces: int = 2000):
        self._traces: list[ExecutionTrace] = []
        self._max = max_traces

    # ── Write ────────────────────────────────────────────────────────────

    def record(self, trace: ExecutionTrace) -> None:
        """Store a completed trace."""
        self._traces.append(trace)
        if len(self._traces) > self._max:
            self._traces.pop(0)

    def clear(self) -> None:
        self._traces.clear()

    # ── Read / Query ─────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return len(self._traces)

    def get_all(self) -> list[ExecutionTrace]:
        """Return all traces (oldest first)."""
        return list(self._traces)

    def get_recent(self, n: int = 20) -> list[ExecutionTrace]:
        """Return the most recent N traces."""
        return list(self._traces[-n:])

    def get_by_id(self, trace_id: str) -> ExecutionTrace | None:
        for t in reversed(self._traces):
            if t.trace_id == trace_id:
                return t
        return None

    def query(
        self,
        *,
        success: bool | None = None,
        tool: str | None = None,
        category: str | None = None,
        min_duration_ms: float = 0,
        max_duration_ms: float = float("inf"),
        limit: int = 100,
    ) -> list[ExecutionTrace]:
        """Query traces by filters. All filters are optional."""
        results = []
        for t in reversed(self._traces):
            if success is not None and t.overall_success != success:
                continue
            if tool and tool not in t.tools_used:
                continue
            if category and category not in t.categories:
                continue
            if t.total_duration_ms < min_duration_ms or t.total_duration_ms > max_duration_ms:
                continue
            results.append(t)
            if len(results) >= limit:
                break
        return results

    # ── Analytics ────────────────────────────────────────────────────────

    def get_failure_stats(self) -> dict[str, Any]:
        """Aggregate failure statistics across all stored traces.

        Returns:
            {
                "total_traces": int,
                "failed_traces": int,
                "failure_rate": float,
                "total_tool_calls": int,
                "failed_tool_calls": int,
                "by_error_code": {"token_expired": 5, "timeout": 3, ...},
                "by_tool": {"feishu_bitable_query": {"total": 10, "failed": 2, "error_codes": {...}}},
            }
        """
        total = len(self._traces)
        failed_traces = sum(1 for t in self._traces if not t.overall_success)

        total_tool_calls = 0
        failed_tool_calls = 0
        error_code_counts: dict[str, int] = defaultdict(int)
        tool_failures: dict[str, dict] = defaultdict(lambda: {"total": 0, "failed": 0, "error_codes": defaultdict(int)})

        for trace in self._traces:
            for step in trace.steps:
                for tc in step.tool_calls:
                    total_tool_calls += 1
                    tname = tc.tool_name
                    tool_failures[tname]["total"] += 1
                    if not tc.success:
                        failed_tool_calls += 1
                        code = tc.error_code or "unknown"
                        error_code_counts[code] += 1
                        tool_failures[tname]["failed"] += 1
                        tool_failures[tname]["error_codes"][code] += 1

        return {
            "total_traces": total,
            "failed_traces": failed_traces,
            "failure_rate": round(failed_traces / total, 3) if total else 0.0,
            "total_tool_calls": total_tool_calls,
            "failed_tool_calls": failed_tool_calls,
            "tool_call_failure_rate": round(failed_tool_calls / total_tool_calls, 3) if total_tool_calls else 0.0,
            "by_error_code": dict(error_code_counts),
            "by_tool": {
                tn: {
                    "total": v["total"],
                    "failed": v["failed"],
                    "error_codes": dict(v["error_codes"]),
                }
                for tn, v in sorted(tool_failures.items(), key=lambda x: -x[1]["total"])
            },
        }

    def get_semantic_effectiveness(self) -> dict[str, Any]:
        """Measure whether semantic annotations actually help.

        Compares:
            - retry_safe=True tools → failure recovery rate
            - parallel_group usage → throughput gain
            - fallback_tool → rescue rate

        Returns:
            {
                "retry_success_rate": 0.85,       # % of retried calls that eventually succeeded
                "retry_effectiveness": {...},       # by tool
                "fallback_success_rate": 0.60,     # % of fallbacks that rescued execution
                "fallback_by_tool": {...},
                "parallel_groups_used": 42,
            }
        """
        retried_calls = 0
        retry_success = 0
        fallback_attempts = 0
        fallback_success = 0
        parallel_groups = set()
        fallback_by_tool: dict[str, dict] = defaultdict(lambda: {"attempts": 0, "success": 0})

        for trace in self._traces:
            for step in trace.steps:
                if step.retry_count > 0:
                    retried_calls += 1
                    if step.status == "completed":
                        retry_success += 1
                if step.fallback_used:
                    fallback_attempts += 1
                    tname = step.tool_hint or "?"
                    fallback_by_tool[tname]["attempts"] += 1
                    if step.status == "completed":
                        fallback_success += 1
                        fallback_by_tool[tname]["success"] += 1
                if step.parallel_group is not None:
                    parallel_groups.add((trace.trace_id, step.parallel_group))

        return {
            "retried_steps": retried_calls,
            "retry_success": retry_success,
            "retry_success_rate": round(retry_success / retried_calls, 3) if retried_calls else 0.0,
            "fallback_attempts": fallback_attempts,
            "fallback_success": fallback_success,
            "fallback_success_rate": round(fallback_success / fallback_attempts, 3) if fallback_attempts else 0.0,
            "fallback_by_tool": {
                tn: v for tn, v in sorted(fallback_by_tool.items(), key=lambda x: -x[1]["attempts"])
            },
            "parallel_groups_total": len(parallel_groups),
        }

    def get_tool_failure_ranking(self) -> list[dict[str, Any]]:
        """Rank tools by failure rate (highest first)."""
        stats = self.get_failure_stats()
        by_tool = stats.get("by_tool", {})
        ranking = []
        for tname, data in by_tool.items():
            ranking.append({
                "tool": tname,
                "total_calls": data["total"],
                "failed": data["failed"],
                "failure_rate": round(data["failed"] / data["total"], 3) if data["total"] else 0.0,
                "top_errors": dict(sorted(
                    data["error_codes"].items(), key=lambda x: -x[1]
                )[:3]),
            })
        ranking.sort(key=lambda x: -x["failure_rate"])
        return ranking

    def get_planner_executor_diff(self) -> dict[str, Any]:
        """Measure how often the planner's intent matches execution reality.

        Planner suggests tool_hints → Executor may use different tools.
        This diff helps improve Planner accuracy.
        """
        total_steps = 0
        hint_matched = 0
        hint_mismatched = 0
        no_hint_steps = 0

        for trace in self._traces:
            for step in trace.steps:
                total_steps += 1
                if not step.tool_hint:
                    no_hint_steps += 1
                    continue
                # Check if any tool call in this step matches the hint
                tools_used = {tc.tool_name for tc in step.tool_calls}
                if step.tool_hint in tools_used:
                    hint_matched += 1
                else:
                    hint_mismatched += 1

        return {
            "total_steps": total_steps,
            "planner_hints_given": total_steps - no_hint_steps,
            "hint_matched": hint_matched,
            "hint_mismatched": hint_mismatched,
            "hint_accuracy": round(hint_matched / (hint_matched + hint_mismatched), 3)
                if (hint_matched + hint_mismatched) else 0.0,
            "steps_without_hint": no_hint_steps,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Global Singleton
# ──────────────────────────────────────────────────────────────────────────────


# Global trace store (shared across all agents in the process)
trace_store = TraceStore()


def get_trace_store() -> TraceStore:
    """Get the global TraceStore instance."""
    return trace_store
