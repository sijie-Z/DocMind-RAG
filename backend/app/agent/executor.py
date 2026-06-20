"""Executor — executes a Plan step by step with tool orchestration and retry logic.

The Executor processes a Plan's steps in dependency order. For each step:
1. Yield thinking event describing what will be done
2. Call LLM with step context, instructing it to use the suggested tool
3. Execute any tool calls, observe results
4. On failure: retry with exponential backoff (up to max_retries_per_step)
5. Store results in WorkingMemory via MemoryBridge
6. Update plan progress

Parallel steps (same dependency satisfaction level) may be executed concurrently.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import tool_registry
from app.core.prometheus import (
    AGENT_EXECUTION_STEPS,
    AGENT_TOOL_CALLS,
    AGENT_TOOL_LATENCY,
)

logger = logging.getLogger(__name__)

EXECUTION_SYSTEM_PROMPT = """你是 DocMind 智能助手。你的任务是执行计划中的当前步骤。

## 当前任务
{step_description}

## 执行指导
- 使用可用的工具来完成当前步骤
- 如果建议使用工具 {tool_hint}，请优先使用它
- 只完成当前这一个步骤，不要做多余的事
- 基于工具返回的结果，给出当前步骤的简洁结论

## 上下文
{memory_context}
{previous_results}

## 约束
- 只基于工具返回的数据回答，不编造信息
- 使用简体中文
"""


class Executor:
    """Executes a Plan step by step with tool orchestration.

    Includes information gain tracking: if consecutive steps produce
    minimal new information, remaining pending steps are auto-skipped
    to avoid wasted LLM calls (inspired by AlphaInsight's debate
    convergence via information entropy threshold).
    """

    # Number of consecutive low-gain steps before prompting early termination
    MAX_LOW_GAIN_STEPS: int = 2

    # Circuit breaker: skip remaining steps after this many consecutive failures
    MAX_CONSECUTIVE_FAILURES: int = 3
    TOOL_TIMEOUT_SECONDS: float = 30.0

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        config: AgentConfig,
        memory_bridge: AgentMemoryBridge,
        execution_mode: str = "dag",
    ):
        self.client = openai_client
        self.config = config
        self.memory = memory_bridge
        self._execution_mode = execution_mode  # "dag" or "serial"
        # Information gain tracking: hashes of seen result content
        self._seen_fingerprints: set[int] = set()
        self._consecutive_low_gain: int = 0
        # Circuit breaker: skip steps after N consecutive failures
        self._consecutive_failures: int = 0

    async def execute(
        self,
        plan: Plan,
        history: list[dict[str, str]] | None = None,
        organization_id: int = 1,
        user_id: int = 0,
        enable_thinking: bool = True,
        ctx: Any = None,  # ExecutionContext
    ) -> AsyncGenerator[AgentEvent, str]:
        """Execute plan steps in dependency order.

        Yields: tool_call, tool_result, tool_error, thinking, chunk events
        Returns: accumulated final answer string (via generator return)
        """

        while not plan.is_complete:
            ready_steps = plan.get_ready_steps()

            # ── SERIAL MODE: ignore dependencies, execute in definition order ──
            if self._execution_mode == "serial":
                pending = [s for s in plan.steps if s.status == "pending"]
                if not pending:
                    break
                step = pending[0]
                async for event in self._execute_single_step(
                    step, plan, history, organization_id, user_id, enable_thinking, ctx=ctx,
                ):
                    yield event
                continue

            # ── DAG MODE: dependency-aware execution ──
            if not ready_steps:
                # Check for deadlock (pending steps with unsatisfied deps)
                pending = [s for s in plan.steps if s.status == "pending"]
                if pending:
                    logger.warning(f"Deadlock detected in plan {plan.id}: {[s.id for s in pending]}")
                    # Skip blocked steps
                    for s in pending:
                        s.status = "skipped"
                break

            # Circuit breaker: skip remaining steps after too many consecutive failures
            if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
                remaining = [s for s in plan.steps if s.status == "pending"]
                if remaining:
                    logger.warning(
                        "Circuit breaker: %d consecutive failures, skipping %d remaining step(s)",
                        self._consecutive_failures, len(remaining),
                    )
                    for s in remaining:
                        s.status = "skipped"
                    yield AgentEvent(
                        type="thinking",
                        content=f"⏱️ 连续 {self._consecutive_failures} 次步骤失败，剩余步骤已跳过",
                        thinking_type="evaluation",
                        plan_id=plan.id,
                    )
                    break

            # Information gain: auto-skip if too many consecutive low-gain steps
            if self._consecutive_low_gain >= self.MAX_LOW_GAIN_STEPS:
                remaining = [s for s in plan.steps if s.status == "pending"]
                if remaining:
                    logger.info(
                        "Early termination: %d consecutive low-gain steps, skipping %d remaining step(s)",
                        self._consecutive_low_gain, len(remaining),
                    )
                    for s in remaining:
                        s.status = "skipped"
                    break

            # Execute ready steps — parallel when multiple independent steps exist
            if len(ready_steps) > 1:
                async for event in self._execute_steps_parallel(
                    ready_steps, plan, history, organization_id, user_id, enable_thinking, ctx=ctx,
                ):
                    yield event
            else:
                async for event in self._execute_single_step(
                    ready_steps[0], plan, history, organization_id, user_id, enable_thinking, ctx=ctx,
                ):
                    yield event

        return

    async def _execute_single_step(
        self, step, plan, history, organization_id, user_id, enable_thinking, ctx=None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a single step (sequential path)."""
        step.status = "running"
        step_result = ""

        if ctx:
            ctx.current_step_id = step.id
            ctx.record_decision("execute", "begin_step", step.description)

        yield AgentEvent(
            type="thinking",
            content=f"执行步骤: {step.description}",
            thinking_type="reasoning",
            plan_id=plan.id,
            plan_step_id=step.id,
            plan_progress=plan.progress,
        )
        await asyncio.sleep(0.01)
        AGENT_EXECUTION_STEPS.inc()

        async for event in self._execute_step_with_retry(
            step=step, plan=plan, history=history,
            organization_id=organization_id, user_id=user_id,
            enable_thinking=enable_thinking, ctx=ctx,
        ):
            if event.type == "chunk":
                step_result += event.content
            yield event

        step.result = step_result[:2000] if step_result else None
        if step.status != "failed":
            step.status = "completed"
            plan.completed_steps += 1
            self._consecutive_failures = 0  # reset circuit breaker on success
            self.memory.store_step_result(step.id, {
                "description": step.description, "status": step.status, "result": step.result,
            })
            # Track information gain
            self._update_info_gain(step_result or "")
        else:
            plan.failed_steps += 1
            self._consecutive_failures += 1  # increment circuit breaker

        await self.memory.record_experience(
            success=(step.status == "completed"),
            action=f"{step.tool_hint or 'llm'}: {step.description[:100]}",
            result=step_result[:200] if step_result else str(step.error_context or "")[:200],
        )

        yield AgentEvent(
            type="thinking",
            content=f"{'✅' if step.status == 'completed' else '❌'} 步骤完成: {step.description}",
            thinking_type="evaluation",
            plan_id=plan.id, plan_step_id=step.id,
            plan_progress=plan.progress, plan_step_status=step.status,
        )

    async def _execute_steps_parallel(
        self, steps, plan, history, organization_id, user_id, enable_thinking, ctx=None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute multiple independent steps concurrently."""
        # Mark all as running
        for step in steps:
            step.status = "running"

        # Announce all steps
        for step in steps:
            yield AgentEvent(
                type="thinking",
                content=f"执行步骤: {step.description}",
                thinking_type="reasoning",
                plan_id=plan.id, plan_step_id=step.id, plan_progress=plan.progress,
            )
        AGENT_EXECUTION_STEPS.inc()

        # Run all steps concurrently
        event_queues: dict[str, list] = {s.id: [] for s in steps}
        {s.id: False for s in steps}

        async def run_step(step):
            step_result = ""
            async for event in self._execute_step_with_retry(
                step=step, plan=plan, history=history,
                organization_id=organization_id, user_id=user_id,
                enable_thinking=enable_thinking, ctx=ctx,
            ):
                if event.type == "chunk":
                    step_result += event.content
                event_queues[step.id].append(event)
            return step, step_result

        tasks = [asyncio.create_task(run_step(s)) for s in steps]

        # Drain events as they arrive
        pending = set(tasks)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                step, step_result = task.result()
                # Flush this step's events
                for evt in event_queues[step.id]:
                    yield evt
                event_queues[step.id].clear()

                step.result = step_result[:2000] if step_result else None
                if step.status != "failed":
                    step.status = "completed"
                    plan.completed_steps += 1
                    self._consecutive_failures = 0
                    self._update_info_gain(step_result or "")
                    self.memory.store_step_result(step.id, {
                        "description": step.description, "status": step.status, "result": step.result,
                    })
                else:
                    plan.failed_steps += 1
                    self._consecutive_failures += 1

                await self.memory.record_experience(
                    success=(step.status == "completed"),
                    action=f"{step.tool_hint or 'llm'}: {step.description[:100]}",
                    result=step_result[:200] if step_result else str(step.error_context or "")[:200],
                )

                yield AgentEvent(
                    type="thinking",
                    content=f"{'✅' if step.status == 'completed' else '❌'} 步骤完成: {step.description}",
                    thinking_type="evaluation",
                    plan_id=plan.id, plan_step_id=step.id,
                    plan_progress=plan.progress, plan_step_status=step.status,
                )

    async def _execute_step_with_retry(
        self,
        step: PlanStep,
        plan: Plan,
        history: list[dict[str, str]] | None,
        organization_id: int,
        user_id: int,
        enable_thinking: bool,
        ctx=None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a single step with retry on failure.

        Semantic-aware behaviour:
            retry_strategy="auto_retry" (default) → retry with backoff
            retry_strategy="ask_user"             → skip after 1 attempt
            retry_strategy="skip"                 → skip immediately on first failure
            fallback_tool set                     → try alternate tool after retries exhaust
        """
        last_error = ""
        max_attempts = 1 if step.retry_strategy in ("ask_user", "skip") else self.config.max_retries_per_step + 1

        for attempt in range(max_attempts):
            if attempt > 0:
                step.retry_count = attempt
                yield AgentEvent(
                    type="thinking",
                    content=f"重试步骤 (第 {attempt}/{self.config.max_retries_per_step} 次): {last_error[:100]}",
                    thinking_type="correction",
                    plan_id=plan.id,
                    plan_step_id=step.id,
                    retry_attempt=attempt,
                    retry_hint=last_error[:200],
                )
                await asyncio.sleep(min(2.0, 0.5 * (2 ** (attempt - 1))))  # exponential backoff

            result_text = ""
            had_error = False

            async for event in self._execute_step_once(
                step=step,
                plan=plan,
                history=history,
                organization_id=organization_id,
                user_id=user_id,
                enable_thinking=enable_thinking,
                error_context=last_error if attempt > 0 else "",
                ctx=ctx,
            ):
                if event.type == "tool_error" or event.type == "error":
                    had_error = True
                    last_error = event.content
                elif event.type == "chunk":
                    result_text += event.content
                yield event

            if not had_error:
                # Also yield the result as a chunk for the final answer accumulator
                if result_text:
                    step.result = result_text
                return

            # Skip retry for "skip" strategy — single attempt only
            if had_error and step.retry_strategy == "skip":
                break

            if had_error and attempt < max_attempts - 1:
                continue  # retry

        # ── Retries exhausted — try fallback_tool if available ──
        if had_error and step.fallback_tool and step.retry_strategy != "skip":
            logger.info(
                "Retries exhausted for step %s, trying fallback tool '%s'",
                step.id, step.fallback_tool,
            )
            yield AgentEvent(
                type="thinking",
                content=f"主工具失败，尝试备用工具 {step.fallback_tool}...",
                thinking_type="correction",
                plan_id=plan.id,
                plan_step_id=step.id,
                retry_attempt=step.retry_count + 1,
                retry_hint=f"fallback: {step.fallback_tool}",
            )

            # Temporarily swap the tool hint and re-run once
            original_hint = step.tool_hint
            step.tool_hint = step.fallback_tool
            step.retry_count = 0  # fresh start for fallback

            fallback_had_error = False
            async for event in self._execute_step_once(
                step=step,
                plan=plan,
                history=history,
                organization_id=organization_id,
                user_id=user_id,
                enable_thinking=enable_thinking,
                error_context=f"主工具失败，尝试备用工具: {last_error[:200]}",
                ctx=ctx,
            ):
                if event.type == "tool_error" or event.type == "error":
                    fallback_had_error = True
                    last_error = event.content
                elif event.type == "chunk":
                    result_text += event.content
                yield event

            if not fallback_had_error and result_text:
                step.result = result_text
                step.tool_hint = original_hint  # restore for consistency
                return

            # Fallback also failed — restore original hint and fall through
            step.tool_hint = original_hint

        # All retries exhausted — graceful degradation
        step.status = "failed"
        step.error_context = last_error
        yield AgentEvent(
            type="tool_error",
            tool_name=step.tool_hint or "unknown",
            content=f"步骤失败（已重试 {step.retry_count} 次）: {last_error[:300]}",
            plan_id=plan.id,
            plan_step_id=step.id,
            retry_attempt=step.retry_count,
        )
        # Emit a fallback chunk so the caller sees something, not silence
        if last_error:
            yield AgentEvent(
                type="chunk",
                content=f"[该步骤因 API 连接问题未能完成，已跳过。问题: {last_error[:100]}]",
                plan_id=plan.id,
                plan_step_id=step.id,
            )

    async def _execute_step_once(
        self,
        step: PlanStep,
        plan: Plan,
        history: list[dict[str, str]] | None,
        organization_id: int,
        user_id: int,
        enable_thinking: bool,
        error_context: str = "",
        ctx=None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a single attempt of a plan step."""
        step_start = time.perf_counter()
        lf = None
        step_span = None
        try:
            from app.agent.observability import get_langfuse
            lf = get_langfuse()
            if lf:
                step_span = lf.span(
                name="execute_step",
                input={
                    "step_id": step.id,
                    "description": step.description,
                    "tool_hint": step.tool_hint,
                    "retry": step.retry_count,
                },
            )
        except Exception:
            pass

        # Build messages for this step
        memory_context = await self.memory.get_context_for_query(step.description)
        previous_results = self._get_previous_results(step.dependencies)

        system_prompt = EXECUTION_SYSTEM_PROMPT.format(
            step_description=step.description,
            tool_hint=step.tool_hint or "无建议",
            memory_context=memory_context,
            previous_results=previous_results or "(无前置结果)",
        )

        if error_context:
            system_prompt += f"\n\n⚠️ 上次执行失败，请调整策略：{error_context}"

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            for msg in history[-4:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content and role in ("user", "assistant"):
                    messages.append({"role": role, "content": content[:500]})

        messages.append({
            "role": "user",
            "content": f"请执行这个步骤: {step.description}",
        })

        # Get tools
        tools = self._get_step_tools(step)

        try:
            # Trace LLM generation
            llm_span = None
            if lf:
                llm_span = lf.generation(
                    name="llm_call",
                    model=self.config.get_quick_model(),
                    messages=messages,
                    input={"tool_count": len(tools) if tools else 0, "step_id": step.id},
                )

            response = await self.client.chat.completions.create(
                model=self.config.get_quick_model(),
                messages=messages,
                tools=tools if tools else None,
                tool_choice="auto" if tools else None,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                timeout=self.config.timeout,
            )

            if lf and llm_span:
                tool_names = [tc.function.name for tc in (response.choices[0].message.tool_calls or [])]
                llm_span.end(
                    output=response.choices[0].message.content or f"tool_calls: {tool_names}",
                    usage=response.usage,
                )
        except Exception as e:
            logger.error(f"LLM call failed for step {step.id}: {e}")
            if lf and step_span:
                step_span.end(output=f"llm_error: {e}", level="ERROR")
            yield AgentEvent(
                type="tool_error",
                tool_name=step.tool_hint or "llm",
                content=f"LLM 调用失败: {e}",
                plan_id=plan.id,
                plan_step_id=step.id,
            )
            return

        message = response.choices[0].message

        # If no tool calls, just return the text
        if not message.tool_calls:
            content = message.content or ""
            if lf and step_span:
                step_span.end(output=f"step completed by LLM ({len(content or '')} chars)")
            if content:
                yield AgentEvent(
                    type="chunk",
                    content=content,
                    plan_id=plan.id,
                    plan_step_id=step.id,
                    plan_progress=plan.progress,
                )
            return

        # Process tool calls
        tool_results: list[dict[str, str]] = []
        for tc in message.tool_calls[:self.config.max_tool_calls_per_turn]:
            func = tc.function
            tool_call_id = tc.id or uuid.uuid4().hex[:16]

            # Parse arguments
            try:
                args = json.loads(func.arguments) if func.arguments else {}
            except json.JSONDecodeError:
                args = {}

            # Yield tool_call event
            yield AgentEvent(
                type="tool_call",
                tool_name=func.name,
                tool_args=args,
                tool_call_id=tool_call_id,
                plan_id=plan.id,
                plan_step_id=step.id,
                plan_progress=plan.progress,
            )

            # Execute tool (with timeout)
            start = time.perf_counter()
            try:
                result = await asyncio.wait_for(
                    tool_registry.execute(
                        func.name,
                        args,
                        organization_id=organization_id,
                        user_id=user_id,
                    ),
                    timeout=self.TOOL_TIMEOUT_SECONDS,
                )
                elapsed = (time.perf_counter() - start) * 1000
                AGENT_TOOL_LATENCY.observe(elapsed / 1000)

                if result.startswith("Error:"):
                    AGENT_TOOL_CALLS.labels(tool=func.name, result="error").inc()
                    yield AgentEvent(
                        type="tool_error",
                        tool_name=func.name,
                        tool_call_id=tool_call_id,
                        content=result,
                        tool_duration_ms=elapsed,
                        plan_id=plan.id,
                        plan_step_id=step.id,
                    )
                    tool_results.append({"tool": func.name, "result": f"Error: {result}"})
                else:
                    AGENT_TOOL_CALLS.labels(tool=func.name, result="success").inc()
                    yield AgentEvent(
                        type="tool_result",
                        tool_name=func.name,
                        tool_call_id=tool_call_id,
                        content=result,
                        tool_duration_ms=elapsed,
                        plan_id=plan.id,
                        plan_step_id=step.id,
                        plan_progress=plan.progress,
                    )
                    tool_results.append({"tool": func.name, "result": result})
                    if ctx:
                        ctx.add_finding(step.id, result[:200], func.name)

            except Exception as e:
                elapsed = (time.perf_counter() - start) * 1000
                AGENT_TOOL_CALLS.labels(tool=func.name, result="error").inc()
                AGENT_TOOL_LATENCY.observe(elapsed / 1000)
                logger.error(f"Tool {func.name} failed: {e}")
                yield AgentEvent(
                    type="tool_error",
                    tool_name=func.name,
                    tool_call_id=tool_call_id,
                    content=f"{type(e).__name__}: {e}",
                    tool_duration_ms=elapsed,
                    plan_id=plan.id,
                    plan_step_id=step.id,
                )
                tool_results.append({"tool": func.name, "result": f"Exception: {e}"})

        # Synthesize tool results into a natural language answer
        if tool_results:
            synthesis = await self._synthesize_tool_results(
                step_description=step.description,
                tool_results=tool_results,
                original_instruction=message.content or "",
            )
            if synthesis:
                yield AgentEvent(
                    type="chunk",
                    content=synthesis,
                    plan_id=plan.id,
                    plan_step_id=step.id,
                    plan_progress=plan.progress,
                )

        used_tools = [r["tool"] for r in tool_results] if tool_results else []
        if lf and step_span:
            result_len = len(synthesis) if tool_results and synthesis else 0
            step_span.end(output=f"tools used: {used_tools}, result_len={result_len}")

        if ctx:
            step_duration = (time.perf_counter() - step_start) * 1000
            ctx.complete_step(
                step_id=step.id,
                description=step.description,
                tool=used_tools[0] if used_tools else None,
                status="completed",
                result=synthesis if tool_results and synthesis else None,
                duration=step_duration,
            )

    async def _synthesize_tool_results(
        self,
        step_description: str,
        tool_results: list[dict[str, str]],
        original_instruction: str,
    ) -> str:
        """Synthesize tool results into a natural language answer."""
        if not tool_results:
            return ""

        results_text = "\n\n".join(
            f"工具: {r['tool']}\n结果: {r['result'][:1500]}"
            for r in tool_results
        )

        messages = [
            {"role": "system", "content": f"你是 DocMind 智能助手。根据工具返回的结果，用自然语言回答用户的问题。\n\n当前步骤: {step_description}"},
            {"role": "user", "content": f"工具执行结果如下:\n\n{results_text}\n\n请根据这些结果，用简洁的自然语言回答用户的问题。不要重复工具名称和技术细节，直接给出答案。如果是搜索知识库，告诉用户你找到了什么信息。如果出错，友好地告知用户。"},
        ]

        try:
            response = await self.client.chat.completions.create(
                model=self.config.get_quick_model(),
                messages=messages,
                temperature=0.3,
                max_tokens=1024,
                timeout=10,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"Synthesis LLM call failed: {e}")
            # Fallback: return the first tool result summary
            first = tool_results[0]
            return f"找到以下信息：\n{first['result'][:500]}"

    def _update_info_gain(self, step_result: str) -> None:
        """Track information gain from completed step.

        Compares the step result against previously seen results using
        content fingerprint hashing. If the result overlaps significantly
        with earlier steps, it's considered low-gain and increments the
        consecutive counter. Resets the counter when new information appears.

        This prevents wasted LLM calls when the agent is going in circles.
        """
        if not step_result:
            self._consecutive_low_gain += 1
            return

        # Compute fingerprint: hash of normalized first 300 chars + any numbers found
        import hashlib
        import re

        normalized = step_result.strip().lower()[:300]
        numbers = re.findall(r"\b\d+(?:\.\d+)?%?", step_result)
        fingerprint_source = normalized + "|" + "|".join(sorted(numbers))
        fp = hashlib.md5(fingerprint_source.encode()).digest()

        if fp in self._seen_fingerprints:
            self._consecutive_low_gain += 1
            logger.debug("Low-gain step detected (%d consecutive)", self._consecutive_low_gain)
        else:
            self._seen_fingerprints.add(fp)
            self._consecutive_low_gain = 0

    def _get_step_tools(self, step: PlanStep) -> list[dict[str, Any]] | None:
        """Get available tools for a step, filtered by config."""
        if not self.config.enable_tools:
            return None

        tools = tool_registry.to_openai_tools(self.config.tool_tags)
        if not tools:
            return None

        # Filter out disabled tools
        if self.config.disabled_tools:
            tools = [
                t for t in tools
                if t.get("function", {}).get("name") not in self.config.disabled_tools
            ]

        # If step has a tool hint, prioritize by rearranging (hinted tool first)
        if step.tool_hint:
            for i, t in enumerate(tools):
                if t.get("function", {}).get("name") == step.tool_hint:
                    tools.insert(0, tools.pop(i))
                    break

        return tools if tools else None

    def _get_previous_results(self, dependency_ids: list[str]) -> str:
        """Build a summary of previous step results that this step depends on.

        Each result is truncated to 200 chars and at most 5 deps are included
        to keep context window under control (message cleanup pattern).
        """
        if not dependency_ids:
            return ""

        parts = ["## 前置步骤结果"]
        # Limit to most recent 5 deps to avoid context bloat
        for dep_id in dependency_ids[-5:]:
            result = self.memory.get_step_result(dep_id)
            if result:
                desc = result.get("description", dep_id)
                res_text = result.get("result", "(无结果)")
                # Truncate each result to keep total context manageable
                parts.append(f"- [{dep_id}] {desc}: {res_text[:200]}")
        return "\n".join(parts) if len(parts) > 1 else ""
