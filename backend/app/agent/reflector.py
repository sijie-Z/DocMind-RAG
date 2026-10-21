"""Reflector — post-execution self-evaluation and correction.

After all plan steps have been executed, the Reflector assesses whether
the task was truly completed. It can decide to:
- pass: Everything is good, proceed to final answer
- retry: A specific step needs to be retried
- replan: The plan was wrong, needs regeneration
- escalate: The task is beyond current capabilities

Degraded states (issue #301 #7/#8) — the evaluator itself is what failed,
so no verdict exists:
- unavailable: the reflection LLM call failed (service down/timeout)
- unparsed: the LLM answered, but its output carried no usable decision
Neither may be reported as "pass" (fake success), and neither may be
reported as a run failure (a good run must not be downgraded because the
evaluator was absent). See ``reflect()`` for the three-state handling.
"""

import json
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.planner import Plan
from app.agent.usage import record_llm_usage
from app.core.log_safety import log_safe
from app.core.prometheus import AGENT_REFLECTION_DECISIONS

logger = logging.getLogger(__name__)

# ── Reflection statistics (module-level counters for benchmark) ──
_reflection_stats = {
    "quick_pass_count": 0,
    "llm_reflection_count": 0,
}


def get_reflection_stats() -> dict:
    """Return current reflection statistics for benchmark analysis."""
    return dict(_reflection_stats)


def reset_reflection_stats() -> None:
    """Reset reflection statistics (e.g. between benchmark runs)."""
    _reflection_stats["quick_pass_count"] = 0
    _reflection_stats["llm_reflection_count"] = 0


REFLECTION_SYSTEM_PROMPT = """你是一个质量评估专家。你需要评估 Agent 的任务执行结果。

## 原始目标
{goal}

## 执行计划
{plan_summary}

## 执行结果
{results_summary}

## 评估标准
1. **目标达成度**: 0-100%，原始目标被满足了多少？
2. **信息完整性**: 是否有缺失的关键信息？
3. **质量**: 结果是否准确、清晰、有用？

## 决策
根据评估，选择以下之一：
- "pass": 任务已完成，结果满意
- "retry": 某个步骤需要重新执行（指定 step_id）
- "replan": 计划需要重新制定

## 输出格式
```json
{{
    "achievement": 85,
    "gaps": ["缺失的信息1", "缺失的信息2"],
    "quality_issues": ["质量问题"],
    "decision": "pass",
    "retry_step_id": null,
    "reasoning": "评估理由（1-2句话）"
}}
```
"""


@dataclass
class ReflectionResult:
    """The result of a reflection pass."""

    achievement: int | None = None  # 0-100；None = 无判定（降级态），不是 0 分
    gaps: list[str] = field(default_factory=list)
    quality_issues: list[str] = field(default_factory=list)
    decision: str = "pass"  # pass, retry, replan, escalate, unavailable, unparsed
    retry_step_id: str | None = None
    reasoning: str = ""


class Reflector:
    """Post-execution reflection and self-correction."""

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        config: AgentConfig,
        memory_bridge: Any = None,  # AgentMemoryBridge
    ):
        self.client = openai_client
        self.config = config
        self.memory = memory_bridge

    async def reflect(
        self,
        plan: Plan,
        results: dict[str, Any],
        ctx=None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Evaluate execution results and decide next action.

        Yields: reflection events. The final one carries the structured verdict
        (achievement / gaps / quality issues) rendered into its `content` — an
        async generator cannot return a value, so it cannot be delivered any
        other way.
        """
        lf = None
        span = None
        try:
            from app.agent.observability import get_langfuse

            lf = get_langfuse()
            if lf:
                span = lf.span(
                    name="reflection",
                    input={
                        "plan_goal": plan.goal,
                        "step_count": len(plan.steps),
                        "completed": plan.completed_steps,
                        "failed": plan.failed_steps,
                    },
                )
        except Exception:
            pass

        # Quick check: if all steps succeeded and had meaningful output, fast-pass
        if self._quick_pass_check(plan, results):
            _reflection_stats["quick_pass_count"] += 1
            if span:
                span.end(output="quick_pass")
            await self._record_reflection(plan, "pass", "All steps completed successfully")
            yield self._summary_event(
                plan,
                ReflectionResult(
                    achievement=100,
                    decision="pass",
                    reasoning="All steps completed successfully",
                ),
            )
            return

        # Build reflection prompt
        plan_summary = self._build_plan_summary(plan)
        results_summary = self._build_results_summary(results)

        system_prompt = REFLECTION_SYSTEM_PROMPT.format(
            goal=plan.goal,
            plan_summary=plan_summary,
            results_summary=results_summary,
        )

        try:
            _reflection_stats["llm_reflection_count"] += 1
            response = await self.client.chat.completions.create(
                model=self.config.get_deep_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请评估以上执行结果。"},
                ],
                temperature=0.0,
                max_tokens=self.config.reflection_max_tokens,
                timeout=30.0,
            )
            record_llm_usage(response, ctx)

            content = response.choices[0].message.content or ""
            reflection_data = self._extract_json(content)

        except Exception as e:
            # W0-5：这是异常原文在反思链路的唯一保留点（decision/事件已固定文案），
            # 日志值过 log_safe（防换行伪造日志行）。
            logger.error("Reflection LLM call failed: %s", log_safe(e))
            if span:
                span.end(output=f"error: {e}", level="ERROR")
            # issue #301 #7（三态设计：降级 ≠ 失败但 ≠ 假 pass）：
            # 反思服务不可用时【判定无法做出】。旧行为判 pass 并捏造
            # achievement=80，把"没评估"伪装成"评估通过"；反向若判失败，
            # 又会基于"什么都没观察到"把好 run 打成坏 run（触发重试/重规划）。
            # 正解是第三态 unavailable：run 照常收尾（不阻断、不重试），但
            # 结构化面（事件 + ctx 决策记录 + 指标）如实标注，且不填任何
            # achievement/gaps —— 无判定不是通过，也不是失败。
            AGENT_REFLECTION_DECISIONS.labels(decision="unavailable").inc()
            if ctx:
                # W0-5：决策 reasoning 会进入 run_report/execution_context 外发，
                # 异常原文只进日志（上方 logger.error 已记录），不得插值。
                ctx.record_decision(
                    "reflect",
                    "unavailable",
                    "反思评估跳过（评估服务异常），本轮未获得完成度判定",
                )
            yield AgentEvent(
                type="reflection",
                reflection_result="unavailable",
                content="反思评估跳过（评估服务异常），本轮未获得完成度判定",
                plan_id=plan.id,
                plan_progress=plan.progress,
            )
            return

        decision = self._normalize_decision(reflection_data)
        if decision is None:
            # issue #301 #8：解析失败 / 未知 decision 不再"宽容"地一律 pass。
            # 旧行为把「我觉得还不错」这类非 JSON 文本（或未知 decision 值）
            # 直接当 pass 决策，还以 success=True 写入经验库。历史里没有注释
            # 或用例承诺这种宽容是有意的（唯一的宽容注释在 LLM 不可用路径，
            # 已被 #7 推翻）——模型答了，但输出的不是判定，与 unavailable
            # 同列为第三态 unparsed：不判 pass（假绿）、不判失败（好 run 不
            # 因评估缺席被打坏），只如实记录。
            raw_preview = str(content)[:200]
            logger.warning("Reflection output carried no usable decision: %r", raw_preview)
            if span:
                span.end(output=f"unparsed: {raw_preview[:120]}", level="WARNING")
            AGENT_REFLECTION_DECISIONS.labels(decision="unparsed").inc()
            if ctx:
                ctx.record_decision(
                    "reflect",
                    "unparsed",
                    "反思输出无法解析（模型输出格式漂移或截断），本轮未获得完成度判定",
                )
            yield AgentEvent(
                type="reflection",
                reflection_result="unparsed",
                content="反思输出无法解析（模型输出格式漂移或截断），本轮未获得完成度判定",
                plan_id=plan.id,
                plan_progress=plan.progress,
            )
            return

        reasoning = reflection_data.get("reasoning", "")
        AGENT_REFLECTION_DECISIONS.labels(decision=decision).inc()

        if ctx:
            ctx.record_decision("reflect", decision, reasoning[:200])

        # Yield reflection event
        yield AgentEvent(
            type="reflection",
            reflection_result=decision,  # type: ignore
            content=reasoning,
            plan_id=plan.id,
            plan_step_id=reflection_data.get("retry_step_id") or "",
            plan_progress=plan.progress,
        )

        # Record insight
        await self._record_reflection(plan, decision, reasoning)

        if span:
            span.end(
                output=f"decision={decision}, achievement={reflection_data.get('achievement', '?')}"
            )

        yield self._summary_event(
            plan,
            ReflectionResult(
                # 模型没给 achievement 就留 None —— 不再回退到编造的 85（#301 #7）。
                achievement=reflection_data.get("achievement"),
                gaps=reflection_data.get("gaps", []),
                quality_issues=reflection_data.get("quality_issues", []),
                decision=decision,
                retry_step_id=reflection_data.get("retry_step_id"),
                reasoning=reasoning,
            ),
        )

    def _summary_event(self, plan: Plan, result: ReflectionResult) -> AgentEvent:
        """Render the structured reflection verdict as an event.

        An async generator cannot `return` a value, so the verdict travels as the
        `content` of a final reflection event. Content is empty when the run was
        clean, which keeps the stream byte-identical to a no-issues run.
        """
        parts: list[str] = []
        if result.gaps:
            parts.append("发现的缺口:\n" + "\n".join(f"- {g}" for g in result.gaps))
        if result.quality_issues:
            parts.append("质量问题:\n" + "\n".join(f"- {q}" for q in result.quality_issues))
        content = ""
        if parts:
            if result.achievement is not None:
                content = f"完成度 {result.achievement}\n"
            content += "\n".join(parts)
        return AgentEvent(
            type="reflection",
            # issue #301 #8：未知 decision 不得再被静默改写为 "pass"（假绿兜底）。
            # 正常路径只会传入已验证的判定；此处兜底降级为 "unparsed"（无判定）。
            reflection_result=result.decision
            if result.decision in ("pass", "retry", "replan", "escalate")
            else "unparsed",
            content=content,
            plan_id=plan.id,
            plan_progress=plan.progress,
        )

    def _quick_pass_check(self, plan: Plan, results: dict[str, Any]) -> bool:
        """Check if we can fast-pass without an LLM call."""
        all_completed = all(s.status in ("completed", "skipped") for s in plan.steps)
        has_results = any(v.get("result") for v in results.values() if isinstance(v, dict))
        no_failures = all(s.status != "failed" for s in plan.steps)
        return all_completed and has_results and no_failures and plan.total_steps <= 3

    def _build_plan_summary(self, plan: Plan) -> str:
        lines = []
        for step in plan.steps:
            icon = {
                "completed": "✅",
                "failed": "❌",
                "skipped": "⬜",
                "pending": "⏳",
                "running": "🔄",
            }
            icon_str = icon.get(step.status, "❓")
            lines.append(f"{icon_str} {step.id}: {step.description}")
        return "\n".join(lines)

    def _build_results_summary(self, results: dict[str, Any]) -> str:
        if not results:
            return "(无执行结果)"
        lines = []
        for step_id, data in results.items():
            if isinstance(data, dict):
                desc = data.get("description", step_id)
                result = data.get("result", "(无结果)")
                lines.append(f"- [{step_id}] {desc}: {str(result)[:300]}")
            else:
                lines.append(f"- [{step_id}]: {str(data)[:300]}")
        return "\n".join(lines) if lines else "(无执行结果)"

    def _extract_json(self, text: str) -> dict[str, Any] | None:
        """Extract JSON from LLM response. None = 没有任何可解析的 JSON 对象。

        issue #301 #8：旧行为在全部解析尝试失败后返回
        `{"decision": "pass"}` 兜底 —— 输出格式漂移被静默翻译成"评估通过"
        （还会以 success=True 进经验库）。解析失败就是没有判定，由调用方按
        unparsed 第三态处理。
        """
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        try:
            brace_match = re.search(r"\{[\s\S]*\}", text)
            if brace_match:
                return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
        return None

    @staticmethod
    def _normalize_decision(data: dict[str, Any] | None) -> str | None:
        """Validated decision from parsed reflection output.

        Returns the decision only when it names one of the real verdicts;
        None for unparseable output or unknown/missing decision values —
        the caller then treats it as the degraded "unparsed" state
        (issue #301 #8: no silent pass).
        """
        if not isinstance(data, dict):
            return None
        decision = str(data.get("decision") or "").strip().lower()
        if decision in ("pass", "retry", "replan", "escalate"):
            return decision
        return None

    async def _record_reflection(self, plan: Plan, decision: str, reasoning: str) -> None:
        """Record reflection outcome in memory."""
        if not self.memory:
            return
        try:
            action = f"Plan: {plan.goal[:80]}"
            result_text = f"Decision: {decision} — {reasoning[:200]}"
            await self.memory.record_experience(
                success=(decision == "pass"),
                action=action,
                result=result_text,
            )
            if decision != "pass":
                await self.memory.record_lesson(
                    lesson=reasoning[:200],
                    trigger=f"Plan execution for: {plan.goal[:80]}",
                    solution="Retry or replan needed",
                )
        except Exception as e:
            logger.warning(f"Failed to record reflection: {e}")
