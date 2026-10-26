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

import json
import logging
import time
from typing import Any

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.final_synthesis import FinalInputPacket
from app.agent.planner import Plan
from app.agent.usage import record_llm_call_error, record_llm_usage

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


FINAL_REVIEWER_SYSTEM_PROMPT = """你是最终答案的对抗性质检专家。审查对象是最终草稿，
事实材料是与 FinalSynthesizer 完全相同的原始 query、步骤终态和最终 attempt 证据。

只挑错并返回结构化 verdict，不得重写、补写或返回修改后的 assistant 正文。重点检查：
草稿是否回答原始请求、是否被局部结论带偏、是否遗漏失败/skipped 账目、是否存在
证据不支持的断言、是否混淆相同 index 但不同 tool_call_id 的来源。

审查不可用或输出不可解析都不是 pass。高严重度问题也只进入 issues_found，由调用方
记录和暴露，绝不能追加进草稿。输出 JSON：overall_assessment、issues_found、
key_strengths、recommendation（pass / revise / reject）。每个 issue 包含 severity、
description、evidence、suggestion。"""


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
        ctx=None,
    ) -> dict[str, Any]:
        """Review execution results from an adversarial perspective.

        Args:
            plan: The execution plan.
            results: Step results from memory.

        Returns:
            Structured review with issues found and recommendation.

        "error" 判定按 error_kind 细分（issue #301 家族尾巴）：
          - ``no_verdict``   —— 模型有响应但未给出可用的判定（JSON 缺
            recommendation 键 / 输出无法解析为非空对象）。调用侧文案：
            「模型未给出判定」。
          - ``service_error`` —— 审查调用本身失败（异常/超时/连接类）。
            调用侧文案：「审查服务异常」。
        两种情形此前在 SSE/决策里都表述为"审查服务异常"，把"模型未给判定"
        与"审查服务崩了"合并了。
        """
        results_summary = self._build_results_summary(plan, results)

        prompt = REVIEWER_SYSTEM_PROMPT.format(
            goal=plan.goal,
            results_summary=results_summary,
        )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请对上述执行结果进行对抗性质检。"},
        ]
        return await self._review_messages(messages, goal=plan.goal, ctx=ctx)

    async def _review_messages(
        self,
        messages: list[dict[str, str]],
        *,
        goal: str,
        ctx=None,
    ) -> dict[str, Any]:
        """Common provider/parse path for legacy result review and final-draft review."""
        llm_started = time.perf_counter()
        llm_recorded = False
        try:
            response = await self.client.chat.completions.create(
                model=self.config.get_deep_model(),
                messages=messages,
                temperature=0.2,
                max_tokens=self.config.reflection_max_tokens,
                timeout=30.0,
                response_format={"type": "json_object"},
            )
            # issue #510：调用级 JSONL（延迟 + 提示词摘要，不落正文）。
            record_llm_usage(
                response,
                ctx,
                latency_ms=(time.perf_counter() - llm_started) * 1000,
                messages=messages,
            )
            llm_recorded = True
            content = response.choices[0].message.content or "{}"
        except Exception as e:
            logger.warning("Adversarial review failed, skipping (error_kind=service_error): %s", e)
            if not llm_recorded:
                record_llm_call_error(
                    ctx,
                    model=self.config.get_deep_model(),
                    error=e,
                    latency_ms=(time.perf_counter() - llm_started) * 1000,
                    messages=messages,
                )
            # issue #301 #9：审查崩溃不再返回 recommendation="pass" ——
            # 无结论（审查没跑成）与真通过（审查跑了且认可）在报告里必须
            # 可区分。返回 "error"，由 loop 收敛并如实记录（SSE + 决策 +
            # run_report 的 review_recommendation 行）；不翻转 clean_success：
            # 没有证据说明 run 有问题，只是本轮没审查到（与 #7 反思降级
            # 同一三态原则：降级 ≠ 失败但 ≠ 假 pass）。
            return {
                "overall_assessment": "质检跳过（评估服务异常）",
                "issues_found": [],
                "key_strengths": [],
                "recommendation": "error",
                "error_kind": "service_error",
                "error": str(e)[:200],
            }

        try:
            review_data = json.loads(content)
        except (json.JSONDecodeError, ValueError) as e:
            # 模型有响应但输出不是 JSON —— 模型侧没给出判定（不是服务崩了）。
            logger.warning(
                "Adversarial review: unparseable model output recorded as error "
                "(error_kind=no_verdict): %s",
                str(e)[:120],
            )
            return {
                "overall_assessment": "质检未完成（模型未给出判定）",
                "issues_found": [],
                "key_strengths": [],
                "recommendation": "error",
                "error_kind": "no_verdict",
                "error": f"模型输出非 JSON: {str(e)[:120]}",
            }

        if not isinstance(review_data, dict):
            # JSON 合法但不是对象（如列表/标量）→ 同样没有可用的判定。
            logger.warning(
                "Adversarial review: model output is not a JSON object recorded as error "
                "(error_kind=no_verdict): %s",
                type(review_data).__name__,
            )
            return {
                "overall_assessment": "质检未完成（模型未给出判定）",
                "issues_found": [],
                "key_strengths": [],
                "recommendation": "error",
                "error_kind": "no_verdict",
                "error": "模型输出不是 JSON 对象",
            }

        # Ensure expected keys exist
        if "issues_found" not in review_data:
            review_data["issues_found"] = []
        if "recommendation" not in review_data:
            # issue #301 #9：模型给了 JSON 却没给判定 → 无判定 ≠ 通过。
            # 旧行为默认 "pass"，与真 pass 在报告里不可区分；与崩溃路径
            # 同列 "error"（由 loop 收敛并如实记录），并按 error_kind 细分
            # 为 no_verdict —— 调用侧文案「模型未给出判定」，与"审查服务
            # 异常"（service_error）各自可辨识（issue #301 家族尾巴）。
            review_data["recommendation"] = "error"
            review_data["error_kind"] = "no_verdict"
            review_data["error"] = "模型返回的 JSON 缺少 recommendation 判定"
            logger.warning(
                "Adversarial review: model output missing recommendation recorded as error "
                "(error_kind=no_verdict), goal=%s",
                goal[:40],
            )

        # issue #301 #9（评审 m3 尾款）：判定必须是**可用的非空字符串**。
        # 缺键（上方块已归置）、null、空串/空白串、非字符串（数字/布尔/对象/
        # 数组）同语义 —— 无判定 ≠ 通过（旧行为：这些值经 loop 的 `or "pass"`
        # 宽容归一为 pass，与真 pass 不可区分）。与崩溃路径同列 "error"（由
        # loop 收敛并如实记录）；error_kind=no_verdict 对齐三态约定（loop 侧
        # 据此走「模型未给出判定」文案，与「审查服务异常」各自可辨识）。
        recommendation_value = review_data.get("recommendation")
        if not isinstance(recommendation_value, str) or not recommendation_value.strip():
            review_data["recommendation"] = "error"
            review_data["error_kind"] = "no_verdict"
            review_data["error"] = (
                "模型返回的 JSON 未给出可用判定"
                f"（recommendation 缺失或为不可用值：{type(recommendation_value).__name__}）"
            )
            logger.warning(
                "Adversarial review: model gave no usable recommendation "
                "(error_kind=no_verdict, type=%s), goal=%s",
                type(recommendation_value).__name__,
                goal[:40],
            )

        issues_high = sum(1 for i in review_data["issues_found"] if i.get("severity") == "high")
        logger.info(
            "Review complete: %s, %d issues (%d high), recommendation=%s",
            goal[:40],
            len(review_data["issues_found"]),
            issues_high,
            review_data["recommendation"],
        )
        return review_data

    async def review_final(
        self,
        packet: FinalInputPacket,
        draft: str,
        ctx=None,
        *,
        synthesis_messages: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Review a final draft against the same packet/prompt evidence as synthesis.

        The returned mapping is diagnostic only.  No code in this method mutates
        ``draft`` or appends issues to it, including reject/revise/error outcomes.
        """
        if synthesis_messages:
            evidence_prompt = "\n".join(
                str(message.get("content", ""))
                for message in synthesis_messages
                if message.get("role") == "user"
            )
        else:
            # Standalone callers still share the packet itself.  The loop passes
            # synthesis_messages so trimming/accounting is byte-for-byte identical.
            evidence_prompt = json.dumps(
                {
                    "query": packet.query,
                    "plan_id": packet.plan_id,
                    "goal": packet.goal,
                    "steps": packet.steps,
                    "context": packet.context,
                    "history": packet.history,
                    "legacy": packet.legacy,
                    "degraded": packet.degraded,
                },
                ensure_ascii=False,
                default=str,
            )
        messages = [
            {"role": "system", "content": FINAL_REVIEWER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "## 原始用户请求\n"
                    f"{packet.query}\n\n"
                    "## 与最终综合相同的输入\n"
                    f"{evidence_prompt}\n\n"
                    "## 待审查最终草稿（不得修改）\n"
                    f"{draft}"
                ),
            },
        ]
        return await self._review_messages(messages, goal=packet.goal, ctx=ctx)

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
