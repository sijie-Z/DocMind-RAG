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
    # run already recorded (pass / retry / replan). pass/retry are surfaced
    # here; replan is now acted on by the loop itself (#106 §5a). The
    # structured outcome fields below (fatal_issues / review_recommendation
    # / clean_success, #106 §5b/§5c) are projected from ctx.artifacts, which
    # the loop writes; this module itself still acts on nothing.
    #
    # §5b/§5c 语义：clean_success=False 表示本次 run 不得被呈现为一次干净的
    # 成功 —— 质检有 fatal_issues（硬性失败）或对抗审查判定 reject。
    # revise（建议修改）与质检的 C 档同级：只记录/暴露，不翻转 clean_success。
    # error（审查未完成，issue #301 #9）同理：没有证据说 run 坏，只是本轮
    # 质量覆盖有缺口——不翻转 clean_success，但 review_recommendation 行里
    # 如实显示，不得静默按 pass 呈现。
    # issue #293 ②：ctx.failures 非空（重试耗尽/预算熔断等终态失败已被记账）同样
    # 必须翻转 —— 报告里明明有 FAILURE 行却 clean_success=True 是自相矛盾的全绿。
    #
    # #319/F-CRIT-1 消费侧防线：闸门（exec_context._normalize_artifact_values，
    # 读写同源）保证常态下这里拿到的是契约形状；本防线再兜一层——对**绕过
    # 闸门**直接构造的 ctx 同样不炸（修前直证：review="boom" → `"boom".get`
    # AttributeError；fatal_issues=123 → `list(123)` TypeError）。降级语义与
    # 闸门一致："无记录"（非映射 ≡ 缺键 ≡ `or {}`；非列表 ≡ 缺键 ≡ `or []`）。
    qg_artifact = ctx.artifacts.get("quality_gate")
    review_artifact = ctx.artifacts.get("review")
    qg_artifact = qg_artifact if isinstance(qg_artifact, dict) else {}
    review_artifact = review_artifact if isinstance(review_artifact, dict) else {}
    fatal_raw = qg_artifact.get("fatal_issues")
    fatal_issues = list(fatal_raw) if isinstance(fatal_raw, list) else []
    review_recommendation = review_artifact.get("recommendation")
    clean_success = (
        not fatal_issues
        and review_recommendation != "reject"
        and not ctx.failures
    )

    evaluation = {
        "reflect_decisions": reflect_reasons or ["(no reflection recorded)"],
        "failures": ctx.failures or [],
        "grade_hint": _grade_hint(ctx),
        "fatal_issues": fatal_issues,
        "review_recommendation": review_recommendation,
        "clean_success": clean_success,
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
        # issue #202 评审 minor-8：人读报告此前答不出"花了多少 token"
        "total_tokens": ctx.total_tokens,
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
    lines.append(f"Tokens:   {report.get('total_tokens', 0)}")
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
    lines.append(f"  clean_success: {ev['clean_success']}")
    if ev["review_recommendation"]:
        lines.append(f"  review_recommendation: {ev['review_recommendation']}")
    for issue in ev["fatal_issues"]:
        lines.append(f"  FATAL: {issue}")
    for f in ev["failures"]:
        lines.append(f"  FAILURE: {f}")
    for r in ev["reflect_decisions"]:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append("## Final output (preview)")
    lines.append(f"  {report['final_output_preview']}")
    lines.append("=== end report ===")
    return "\n".join(lines)
