"""Reviewer — adversarial post-execution quality assurance.

The Reviewer plays the role of a skeptical adversary, actively looking for
gaps, unsupported claims, inconsistencies, and potential errors in the
Agent's execution results. This is inspired by AlphaInsight's multi-agent
debate architecture, where a "short" (做空) agent challenges the main
analyst's conclusions.

The Reviewer runs alongside the Reflector (which does self-evaluation) but
with a fundamentally different perspective: it is told to be critical and
to find problems, not to assess quality neutrally.
"""

import logging
from typing import Any

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.planner import Plan

logger = logging.getLogger(__name__)

REVIEWER_SYSTEM_PROMPT = """你是一个**对抗性质检专家**（Adversarial Reviewer）。你的唯一任务是**挑错**。

你的角色类似于金融审计中的"做空分析师"——你不是来找优点的，你是来发现问题的。

## 原始目标
{goal}

## 执行结果
{results_summary}

## 你需要检查的维度
1. **事实准确性**: 结论是否有数据支持？是否有凭空编造的内容？
2. **逻辑一致性**: 是否存在自相矛盾或逻辑跳跃？
3. **信息完整性**: 是否遗漏了关键信息？是否只选择性展示了有利的证据？
4. **来源可靠性**: 引用的来源是否可信？是否过度依赖单一来源？
5. **假设检验**: 是否做了未经验证的假设？这些假设合理吗？

## 输出格式
返回 JSON 对象：
```json
{{
    "overall_assessment": "对结果质量的总体判断：可信 / 部分可信 / 不可信",
    "issues_found": [
        {{
            "severity": "high/medium/low",
            "description": "具体问题描述",
            "evidence": "为什么这是个问题",
            "suggestion": "如何改进"
        }}
    ],
    "key_strengths": ["如果确实存在优点，列出1-2个，但要实事求是"],
    "recommendation": "pass（通过）/ revise（需修改）/ reject（不可接受）"
}}
```

⚠️ 你是故意挑剔的——即使结果看起来很好，也要至少找出1-2个潜在风险点。
"""


class Reviewer:
    """Adversarial reviewer that challenges execution results."""

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        config: AgentConfig,
    ):
        self.client = openai_client
        self.config = config

    async def review(
        self,
        plan: Plan,
        results: dict[str, Any],
    ) -> dict[str, Any]:
        """Review execution results from an adversarial perspective.

        Args:
            plan: The execution plan.
            results: Step results from memory.

        Returns:
            Structured review with issues found and recommendation.
        """
        results_summary = self._build_results_summary(plan, results)

        prompt = REVIEWER_SYSTEM_PROMPT.format(
            goal=plan.goal,
            results_summary=results_summary,
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.config.get_deep_model(),
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "请对上述执行结果进行对抗性质检。"},
                ],
                temperature=0.2,
                max_tokens=self.config.reflection_max_tokens,
                timeout=30.0,
                response_format={"type": "json_object"},
            )

            import json

            content = response.choices[0].message.content or "{}"
            review_data = json.loads(content)

            # Ensure expected keys exist
            if "issues_found" not in review_data:
                review_data["issues_found"] = []
            if "recommendation" not in review_data:
                review_data["recommendation"] = "pass"

            issues_high = sum(1 for i in review_data["issues_found"] if i.get("severity") == "high")
            logger.info(
                "Review complete: %s, %d issues (%d high), recommendation=%s",
                plan.goal[:40], len(review_data["issues_found"]), issues_high,
                review_data["recommendation"],
            )
            return review_data

        except Exception as e:
            logger.warning("Adversarial review failed, skipping: %s", e)
            return {
                "overall_assessment": "质检跳过（评估服务异常）",
                "issues_found": [],
                "key_strengths": [],
                "recommendation": "pass",
            }

    def _build_results_summary(self, plan: Plan, results: dict[str, Any]) -> str:
        """Build a concise summary of execution results for the reviewer."""
        lines = [f"## 执行计划（共 {len(plan.steps)} 步）"]

        for step in plan.steps:
            icon = {"completed": "✅", "failed": "❌", "skipped": "⬜", "pending": "⏳"}
            icon_str = icon.get(step.status, "❓")
            lines.append(f"\n{icon_str} **{step.id}: {step.description}**")

            if step.status == "completed":
                result_data = results.get(step.id, {})
                if isinstance(result_data, dict):
                    result_text = result_data.get("result", "")
                    if result_text:
                        lines.append(f"  结果: {str(result_text)[:500]}")
                else:
                    lines.append(f"  结果: {str(result_data)[:500]}")

            elif step.status == "failed":
                lines.append(f"  ❌ 执行失败: {getattr(step, 'error_context', '未知错误')}")

        return "\n".join(lines)
