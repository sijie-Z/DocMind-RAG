"""Reflector — post-execution self-evaluation and correction.

After all plan steps have been executed, the Reflector assesses whether
the task was truly completed. It can decide to:
- pass: Everything is good, proceed to final answer
- retry: A specific step needs to be retried
- replan: The plan was wrong, needs regeneration
- escalate: The task is beyond current capabilities
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI

from app.agent.events import AgentEvent
from app.agent.config import AgentConfig
from app.agent.planner import Plan
from app.core.prometheus import AGENT_REFLECTION_DECISIONS

logger = logging.getLogger(__name__)

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
    achievement: int = 0  # 0-100
    gaps: List[str] = field(default_factory=list)
    quality_issues: List[str] = field(default_factory=list)
    decision: str = "pass"  # pass, retry, replan
    retry_step_id: Optional[str] = None
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
        results: Dict[str, Any],
    ) -> AsyncGenerator[AgentEvent, ReflectionResult]:
        """Evaluate execution results and decide next action.

        Yields: thinking and reflection events
        Returns: ReflectionResult (via generator return sent from caller)
        """
        # Quick check: if all steps succeeded and had meaningful output, fast-pass
        if self._quick_pass_check(plan, results):
            yield AgentEvent(
                type="reflection",
                reflection_result="pass",
                content="所有步骤已成功完成，结果完整。",
                plan_id=plan.id,
                plan_progress=1.0,
            )
            await self._record_reflection(plan, "pass", "All steps completed successfully")
            result = ReflectionResult(achievement=100, decision="pass", reasoning="All steps completed")
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
            response = await self.client.chat.completions.create(
                model=self.config.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请评估以上执行结果。"},
                ],
                temperature=0.0,
                max_tokens=self.config.reflection_max_tokens,
                timeout=30.0,
            )

            content = response.choices[0].message.content or ""
            reflection_data = self._extract_json(content)

        except Exception as e:
            logger.error(f"Reflection LLM call failed: {e}")
            # Default to pass on error (don't block the user)
            yield AgentEvent(
                type="reflection",
                reflection_result="pass",
                content="反思评估跳过（评估服务异常）",
                plan_id=plan.id,
                plan_progress=plan.progress,
            )
            result = ReflectionResult(achievement=80, decision="pass", reasoning="Reflection skipped due to error")
            return

        decision = reflection_data.get("decision", "pass")
        reasoning = reflection_data.get("reasoning", "")
        AGENT_REFLECTION_DECISIONS.labels(decision=decision).inc()

        # Yield reflection event
        yield AgentEvent(
            type="reflection",
            reflection_result=decision,  # type: ignore
            content=reasoning,
            plan_id=plan.id,
            plan_progress=plan.progress,
        )

        # Record insight
        await self._record_reflection(plan, decision, reasoning)

        result = ReflectionResult(
            achievement=reflection_data.get("achievement", 85),
            gaps=reflection_data.get("gaps", []),
            quality_issues=reflection_data.get("quality_issues", []),
            decision=decision,
            retry_step_id=reflection_data.get("retry_step_id"),
            reasoning=reasoning,
        )
        return

    def _quick_pass_check(self, plan: Plan, results: Dict[str, Any]) -> bool:
        """Check if we can fast-pass without an LLM call."""
        all_completed = all(s.status in ("completed", "skipped") for s in plan.steps)
        has_results = any(v.get("result") for v in results.values() if isinstance(v, dict))
        no_failures = all(s.status != "failed" for s in plan.steps)
        return all_completed and has_results and no_failures and plan.total_steps <= 3

    def _build_plan_summary(self, plan: Plan) -> str:
        lines = []
        for step in plan.steps:
            icon = {"completed": "✅", "failed": "❌", "skipped": "⬜", "pending": "⏳", "running": "🔄"}
            icon_str = icon.get(step.status, "❓")
            lines.append(f"{icon_str} {step.id}: {step.description}")
        return "\n".join(lines)

    def _build_results_summary(self, results: Dict[str, Any]) -> str:
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

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract JSON from LLM response."""
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
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
            brace_match = re.search(r'\{[\s\S]*\}', text)
            if brace_match:
                return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass
        return {"decision": "pass", "reasoning": text[:200]}

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
