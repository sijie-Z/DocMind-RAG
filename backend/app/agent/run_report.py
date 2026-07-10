"""Runtime Run Report — MVP.

Observe-only. Produces a human-readable summary of a single agent run,
assembled entirely from data the run loop already records into
``ExecutionContext``. This module changes NO runtime behavior: it does
not trigger retry / replan / approval / memory / experience. It only
reads and renders.

Purpose (see ADR-1 / strategic discussion): let DocMind answer, after a
run, the three questions — *what did it do*, *why*, *how did it turn out*
— from facts the code actually has, without inventing reasons via an
extra LLM call. If even the developer finds this useless on a real bug,
the reliability/observability direction is falsified cheaply.
"""

from __future__ import annotations

from typing import Any

from app.agent.exec_context import ExecutionContext


def _decision_lines(ctx: ExecutionContext, phase: str) -> list[str]:
    """Pull reasoning for one phase that the run already recorded."""
    out: list[str] = []
    for d in ctx.decisions:
        if d.phase != phase or not d.reasoning:
            continue
        out.append(f"[{d.action}] {d.reasoning[:300]}")
    return out


def build_run_report(ctx: ExecutionContext, final_output: str = "") -> dict[str, Any]:
    """Assemble a read-only report dict from an execution context.

    No side effects, no LLM calls, no behavior change. Pure projection of
    whatever the planner/executor/reflector already wrote into ``ctx``
    during the run.
    """
    plan_reasons = _decision_lines(ctx, "plan")
    exec_reasons = _decision_lines(ctx, "execute")
    reflect_reasons = _decision_lines(ctx, "reflect")

    # Why each tool was called — pulled from facts the code has:
    # the plan step's description + which step it belongs to. We do NOT
    # ask the model to justify its tool choice after the fact; that would
    # be narrative, not observation.
    tool_calls = []
    for step in ctx.completed_steps:
        tool_calls.append({
            "step_id": step.step_id,
            "description": step.description,
            "tool": step.tool_used,
            "status": step.status,
            "duration_ms": round(step.duration_ms, 1),
            "error": step.error,
            "result_preview": step.result_summary[:200] if step.result_summary else "",
        })

    # Evaluation outcome — reflected in the reflect-phase decision the
    # run already recorded (pass / retry / replan). We surface it, we do
    # not act on it.
    evaluation = {
        "reflect_decisions": reflect_reasons or ["(no reflection recorded)"],
        "failures": ctx.failures or [],
        "grade_hint": _grade_hint(ctx),
    }

    return {
        "task_id": ctx.task_id,
        "query": ctx.query,
        "goal": ctx.goal,
        "plan_summary": ctx.plan_summary,
        "plan_reasoning": plan_reasons or ["(planner recorded no structured reasoning)"],
        "execution_reasoning": exec_reasons or ["(executor recorded no per-step reasoning)"],
        "steps": [
            {
                "step_id": s.step_id,
                "description": s.description,
                "tool": s.tool_used,
                "status": s.status,
                "duration_ms": round(s.duration_ms, 1),
            }
            for s in ctx.completed_steps
        ],
        "tool_calls": tool_calls,
        "evaluation": evaluation,
        "final_output_preview": (final_output or "")[:400],
        "duration_ms": round(ctx.duration_ms, 1),
    }


def _grade_hint(ctx: ExecutionContext) -> str:
    """A coarse, fact-derived quality hint. Not a verdict that acts on anything."""
    if ctx.failures:
        return "has-failures"
    if not ctx.completed_steps:
        return "no-steps"
    statuses = {s.status for s in ctx.completed_steps}
    if statuses <= {"completed", "skipped"}:
        return "ok"
    return "mixed"


def render_run_report_text(report: dict[str, Any]) -> str:
    """Render the report dict as plain text for logs / debugging."""
    lines: list[str] = []
    lines.append(f"=== Run Report [{report['task_id']}] ===")
    lines.append(f"Query: {report['query']}")
    lines.append(f"Goal:  {report['goal'] or '(unset)'}")
    lines.append(f"Duration: {report['duration_ms']} ms")
    lines.append("")
    lines.append("## Plan reasoning")
    lines.extend(f"  - {r}" for r in report["plan_reasoning"])
    lines.append("")
    lines.append("## Steps")
    for s in report["steps"]:
        lines.append(f"  - [{s['status']}] {s['description']} (tool={s['tool']}, {s['duration_ms']}ms)")
    lines.append("")
    lines.append("## Tool calls")
    for t in report["tool_calls"]:
        err = f"  ERROR={t['error']}" if t["error"] else ""
        lines.append(f"  - {t['tool']} ({t['step_id']}){err}")
        if t["result_preview"]:
            lines.append(f"      -> {t['result_preview']}")
    lines.append("")
    lines.append("## Evaluation")
    ev = report["evaluation"]
    lines.append(f"  grade_hint: {ev['grade_hint']}")
    for f in ev["failures"]:
        lines.append(f"  FAILURE: {f}")
    for r in ev["reflect_decisions"]:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("## Final output (preview)")
    lines.append(f"  {report['final_output_preview']}")
    lines.append("=== end report ===")
    return "\n".join(lines)
