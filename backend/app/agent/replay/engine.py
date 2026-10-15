"""ReplayEngine — formats saved ExecutionContext snapshots for human-readable playback.

Usage:
    engine = ReplayEngine()
    ctx = await engine.load("B017")
    engine.print_replay(ctx)
    engine.print_summary(ctx)

    # Diff two runs
    engine.print_diff(ctx_a, ctx_b)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from app.core.log_safety import log_safe

logger = logging.getLogger(__name__)

REPLAY_DIR = Path("benchmark/replay")

# task_id 进文件名前的白名单（与 exec_context._SAFE_FILE_STEM 同款约束）：
# 读侧 task_id 来自 HTTP 路径参数（不可信）——'/'、'\\'、'..' 一律拒绝拼接，
# 绝不把原始值带进路径；再叠加 abspath 规范化 + startswith 前缀校验两道
# （见 _local_path；与写侧 _replay_file_for 同款 CodeQL 可识别防线）。
_SAFE_TASK_ID = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")

# 结构化 timeline 的字段契约：与 ExecutionContext.to_dict()（写侧）逐字对齐。
# 只投影这些键——改名/增删都会让测试红（见 tests/unit/test_agent_replay_engine.py）。
_TIMELINE_STEP_FIELDS = (
    "step_id", "description", "tool_used", "status",
    "result_summary", "error", "duration_ms", "tokens_used",
)
_TIMELINE_FINDING_FIELDS = ("source_step", "content", "source_tool", "confidence")
_TIMELINE_DECISION_FIELDS = ("phase", "action", "reasoning", "triggered_by")


class ReplayEngine:
    """Loads and formats ExecutionContext snapshots for replay."""

    # ── Load ──────────────────────────────────────────────────────────────

    @staticmethod
    def _local_path(task_id: str) -> Path | None:
        """由 task_id 构造本地 replay 文件落点；不满足约束 → None（拒绝拼接）。

        读侧 task_id 来自 HTTP 路径参数（不可信），与写侧（exec_context.
        _replay_file_for）同款三重防线：① 白名单字符集（见 _SAFE_TASK_ID）；
        ② os.path.abspath 规范化（CodeQL 识别为 PathNormalization）；
        ③ 规范化后用 startswith 校验前缀仍在基准目录（CodeQL 识别为
        SafeAccessCheck）。非法即拒绝，绝不"消毒"后拼接。
        """
        if not _SAFE_TASK_ID.fullmatch(str(task_id)):
            return None
        base = os.path.abspath(REPLAY_DIR)
        target = os.path.abspath(os.path.join(base, f"{task_id}.json"))
        if not target.startswith(base + os.sep):
            return None
        return Path(target)

    @staticmethod
    def _is_valid_replay_ctx(ctx: Any) -> bool:
        """结构校验（F1）：回放载荷必须是写侧 to_dict() 形状的 dict——
        `task_id` 非空字符串 + `steps` 为列表。

        合法 JSON 但形状不符（list / 字符串 / 缺键 dict）一律视为损坏，按
        "无回放数据"处理：读 API 落诚实 404、has_replay 标否——**绝不裸抛 500**，
        也绝不把畸形载荷喂给 format_timeline/format_replay 的 `.get` 调用。
        """
        if not isinstance(ctx, dict):
            return False
        task_id = ctx.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            return False
        return isinstance(ctx.get("steps"), list)

    async def load(self, task_id: str, *, exact: bool = False) -> dict[str, Any] | None:
        """Load a saved execution context by task_id.

        Supports partial ID matching (e.g. "abc123" matches "abc123def456")——
        CLI 便利路径，默认保留。

        exact=True（HTTP 消费面用）：本地只认精确文件名，不做前缀匹配——
        按 task_id 读取回放的调用方（已过归属校验）绝不允许前缀命中到
        **另一个任务**的文件。

        Tries Redis first, then local benchmark/replay/ directory.
        """
        if not _SAFE_TASK_ID.fullmatch(str(task_id)):
            # 非法 task_id 不可能对应真实运行（写侧同款白名单）；统一按无数据处理，
            # 杜绝任何形式的路径拼接（读侧 CodeQL 防线）。
            return None

        # Try Redis
        try:
            from app.agent.exec_context import ExecutionContext
            ctx = await ExecutionContext.load(task_id)
            if ctx is not None:
                if self._is_valid_replay_ctx(ctx):
                    return ctx
                # 合法 JSON 但形状畸形（F1）：留 warning 行迹，按无数据降级（继续试本地兜底）
                logger.warning(
                    "回放载荷结构畸形（按无数据降级）(source=redis, task=%s, type=%s)",
                    log_safe(task_id),
                    type(ctx).__name__,
                )
        except Exception:
            pass

        # Try local file — exact match first, then partial prefix match
        p = self._local_path(task_id)
        if p is not None and p.exists():
            return self._read_ctx_file(p)

        # Partial prefix match（仅非 exact 模式）
        if not exact and REPLAY_DIR.exists():
            for f in REPLAY_DIR.glob("*.json"):
                if f.stem.startswith(task_id):
                    return self._read_ctx_file(f)

        return None

    @staticmethod
    def _read_ctx_file(p: Path) -> dict[str, Any] | None:
        """容错读本地回放文件：读取/解析失败或**结构畸形** → None（按无回放数据
        安全降级，绝不 500 裸抛；与 checkpoint 的毒丸防线同纪律）。"""
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("本地回放文件读取失败（按无数据降级）: %s: %s", log_safe(str(p)), log_safe(e))
            return None
        if not ReplayEngine._is_valid_replay_ctx(data):
            logger.warning(
                "本地回放文件结构畸形（按无数据降级）: %s (type=%s)",
                log_safe(str(p)),
                type(data).__name__,
            )
            return None
        return data

    async def has_replay(self, task_id: str) -> bool:
        """该任务是否有**可读**回放数据（Redis 优先，本地精确文件兜底）。

        与 load(exact=True) 同一查找链；F3：不只做存在性判定——载荷经结构校验
        （_is_valid_replay_ctx）后才标真；损坏（parse 失败 / 形状畸形）如实视为
        "无回放"（UI 绝不亮一个必然打不开的入口）。任何后端故障 → 按"无"处理
        （诚实：宁可少标，不虚标）。
        """
        if not task_id:
            return False
        try:
            # 调用时导入（与 exec_context.load 同款）：测试/进程内替换
            # app.core.redis.redis_client 后即刻生效。
            from app.core.redis import redis_client
            if redis_client:
                raw = await redis_client.get(f"agent:replay:{task_id}")
                if raw:
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = None
                    if parsed is not None and self._is_valid_replay_ctx(parsed):
                        return True
                    logger.warning(
                        "回放载荷结构畸形，has_replay 如实标否 (task=%s)", log_safe(task_id)
                    )
        except Exception:
            pass
        try:
            p = self._local_path(task_id)
            if p is not None and p.exists():
                return self._read_ctx_file(p) is not None
        except Exception:
            return False
        return False

    def load_sync(self, path: str) -> dict[str, Any] | None:
        """Synchronous load from a local JSON file."""
        p = Path(path)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return None

    # ── Formatting ────────────────────────────────────────────────────────

    def format_replay(self, ctx: dict[str, Any]) -> str:
        """Format a full execution replay as a human-readable string."""
        lines: list[str] = []
        lines.append("=" * 60)
        lines.append(f"  Execution Replay: {ctx.get('task_id', 'unknown')}")
        lines.append("=" * 60)
        lines.append(f"  Query:   {ctx.get('query', '')[:100]}")
        lines.append(f"  Goal:    {ctx.get('goal', '')[:80]}")
        lines.append(f"  Plan:    {ctx.get('plan_summary', '')}")
        lines.append(f"  Elapsed: {ctx.get('duration_ms', 0) / 1000:.1f}s")
        lines.append(f"  Tokens:  {ctx.get('total_tokens', 0)}")
        lines.append(f"  Steps:   {ctx.get('steps_completed', 0)}")
        lines.append(f"  Findings:{len(ctx.get('findings', []))}")
        lines.append(f"  Failures:{len(ctx.get('failures', []))}")
        lines.append("")

        # Steps
        steps = ctx.get("steps", [])
        if not steps:
            lines.append("  (no step data recorded)")
        else:
            lines.append("  ── Execution Steps ──")
            for i, step in enumerate(steps, 1):
                lines.append(self._format_step(i, step))

        # Findings
        findings = ctx.get("findings", [])
        if findings:
            lines.append("")
            lines.append("  ── Key Findings ──")
            for f in findings[:5]:
                lines.append(f"    [{f['source_step']}] {f.get('content', '')[:120]}")
            if len(findings) > 5:
                lines.append(f"    ... and {len(findings) - 5} more findings")

        # Decisions
        decisions = ctx.get("decisions", [])
        if decisions:
            lines.append("")
            lines.append("  ── Key Decisions ──")
            for d in decisions:
                lines.append(f"    [{d['phase']}] {d['action']}: {d.get('reasoning', '')[:100]}")

        # Failures
        failures = ctx.get("failures", [])
        if failures:
            lines.append("")
            lines.append(f"  ── Failures ({len(failures)}) ──")
            for f in failures:
                lines.append(f"    ❌ {f[:150]}")

        lines.append("")
        lines.append("=" * 60)
        return "\n".join(lines)

    def format_summary(self, ctx: dict[str, Any]) -> str:
        """One-line summary for listing multiple executions."""
        task_id = ctx.get("task_id", "????")[:8]
        query = ctx.get("query", "")[:40]
        steps = ctx.get("steps_completed", 0)
        duration = ctx.get("duration_ms", 0) / 1000
        fails = len(ctx.get("failures", []))
        findings = len(ctx.get("findings", []))
        status = "❌" if fails > 0 else "✅"
        return f"  {status} [{task_id}] {query:<40s} {steps} steps, {duration:.1f}s, {findings} findings, {fails} failures"

    # ── Structured export（UI 消费面）─────────────────────────────────────

    def format_timeline(self, ctx: dict[str, Any]) -> dict[str, Any]:
        """结构化 timeline 出口：给前端渲染的 JSON（字段与写侧 to_dict() 逐字对齐）。

        诚实边界：
        - 键名一律照抄 ExecutionContext.to_dict() 的写侧契约，禁止改名/换口径；
        - 容器形状不符（非 list/dict）→ 空；坏行（非 dict）丢弃——宁可少，
          绝不编造轨迹数据；缺键用 get 默认，不虚构值。
        """
        raw_steps = ctx.get("steps")
        steps = [
            {k: s.get(k) for k in _TIMELINE_STEP_FIELDS}
            for s in (raw_steps if isinstance(raw_steps, list) else [])
            if isinstance(s, dict)
        ]
        raw_findings = ctx.get("findings")
        findings = [
            {k: f.get(k) for k in _TIMELINE_FINDING_FIELDS}
            for f in (raw_findings if isinstance(raw_findings, list) else [])
            if isinstance(f, dict)
        ]
        raw_decisions = ctx.get("decisions")
        decisions = [
            {k: d.get(k) for k in _TIMELINE_DECISION_FIELDS}
            for d in (raw_decisions if isinstance(raw_decisions, list) else [])
            if isinstance(d, dict)
        ]
        raw_failures = ctx.get("failures")
        failures = [f for f in (raw_failures if isinstance(raw_failures, list) else [])]

        return {
            "task_id": ctx.get("task_id", ""),
            "query": ctx.get("query", ""),
            "goal": ctx.get("goal", ""),
            "plan_summary": ctx.get("plan_summary", ""),
            "steps_completed": ctx.get("steps_completed", len(steps)),
            "steps": steps,
            "findings": findings,
            "decisions": decisions,
            "failures": failures,
            "duration_ms": ctx.get("duration_ms", 0.0),
            "total_tokens": ctx.get("total_tokens", 0),
            "progress": ctx.get("progress", 0.0),
            "start_time": ctx.get("start_time", 0.0),
        }

    # ── Diff ──────────────────────────────────────────────────────────────

    def format_diff(self, ctx_a: dict[str, Any], ctx_b: dict[str, Any]) -> str:
        """Side-by-side comparison of two execution contexts."""
        lines: list[str] = []
        lines.append("=" * 70)
        lines.append(f"  Execution Diff: {ctx_a.get('task_id', 'A')[:8]} vs {ctx_b.get('task_id', 'B')[:8]}")
        lines.append("=" * 70)

        # Overall comparison
        dur_a = ctx_a.get("duration_ms", 0) / 1000
        dur_b = ctx_b.get("duration_ms", 0) / 1000
        steps_a = ctx_a.get("steps_completed", 0)
        steps_b = ctx_b.get("steps_completed", 0)
        fails_a = len(ctx_a.get("failures", []))
        fails_b = len(ctx_b.get("failures", []))
        find_a = len(ctx_a.get("findings", []))
        find_b = len(ctx_b.get("findings", []))

        lines.append(f"  {'Metric':<20} {'Run A':<15} {'Run B':<15} {'Delta':<10}")
        lines.append(f"  {'-'*20} {'-'*15} {'-'*15} {'-'*10}")
        lines.append(f"  {'Duration':<20} {dur_a:<14.1f}s {dur_b:<14.1f}s {dur_b - dur_a:+8.1f}s")
        lines.append(f"  {'Steps':<20} {steps_a:<15d} {steps_b:<15d} {steps_b - steps_a:+8d}")
        lines.append(f"  {'Findings':<20} {find_a:<15d} {find_b:<15d} {find_b - find_a:+8d}")
        lines.append(f"  {'Failures':<20} {fails_a:<15d} {fails_b:<15d} {fails_b - fails_a:+8d}")
        lines.append("")

        # Step-by-step comparison
        steps_list_a = ctx_a.get("steps", [])
        steps_list_b = ctx_b.get("steps", [])
        max_steps = max(len(steps_list_a), len(steps_list_b))

        lines.append("  ── Step Comparison ──")
        for i in range(max_steps):
            step_a = steps_list_a[i] if i < len(steps_list_a) else None
            step_b = steps_list_b[i] if i < len(steps_list_b) else None
            label_a = self._step_label(step_a) if step_a else "(no step)"
            label_b = self._step_label(step_b) if step_b else "(no step)"
            dur_a_s = f"{step_a['duration_ms']:.1f}s" if step_a else "-"
            dur_b_s = f"{step_b['duration_ms']:.1f}s" if step_b else "-"
            status_a = self._step_status_icon(step_a) if step_a else " "
            status_b = self._step_status_icon(step_b) if step_b else " "

            marker = "  " if step_a and step_b else " ←" if step_b else " →"
            lines.append(
                f"  Step {i+1}: {status_a} {label_a:<35s} {dur_a_s:>7s}  |  "
                f"{status_b} {label_b:<35s} {dur_b_s:>7s}{marker}"
            )

        # Show added / removed steps
        if steps_list_b and steps_list_a:
            tools_a = {s["tool_used"] for s in steps_list_a if s.get("tool_used")}
            tools_b = {s["tool_used"] for s in steps_list_b if s.get("tool_used")}
            added_tools = tools_b - tools_a
            removed_tools = tools_a - tools_b
            if added_tools:
                lines.append(f"\n  ➕ New tools: {', '.join(sorted(added_tools))}")
            if removed_tools:
                lines.append(f"  ➖ Removed tools: {', '.join(sorted(removed_tools))}")

        lines.append("")
        lines.append("=" * 70)
        return "\n".join(lines)

    # ── Internal helpers ──

    @staticmethod
    def _format_step(i: int, step: dict) -> str:
        tool = step.get("tool_used") or "—"
        status = step.get("status", "?")
        duration = step.get("duration_ms", 0)
        result = (step.get("result_summary") or "")[:100]
        error = step.get("error")

        icon = {"completed": "✅", "failed": "❌", "skipped": "⏭️", "pending": "⏳"}.get(status, "❓")
        duration_sec = duration / 1000.0
        line = f"  {icon} Step {i}: {step.get('description', '?')[:50]}"
        line += f"\n      Tool: {tool:<20s} Status: {status:<10s} Time: {duration_sec:.1f}s"

        if result:
            line += f"\n      Result: {result}"
        if error:
            line += f"\n      Error:  {error[:120]}"
        return line

    @staticmethod
    def _step_label(step: dict) -> str:
        desc = step.get("description", "")[:30]
        tool = step.get("tool_used") or "—"
        return f"{desc} ({tool})"

    @staticmethod
    def _step_status_icon(step: dict) -> str:
        status = step.get("status", "")
        return {"completed": "✅", "failed": "❌", "skipped": "⏭️"}.get(status, "❓")
