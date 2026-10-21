"""Executor Agent — autonomous execution runtime for the Multi-Agent OS.

Role:
    The Executor Agent is NOT an LLM-powered agent. It is a policy-driven
    execution runtime that:

    1. Receives a Plan via the Message Bus
    2. Interprets semantic annotations on each step
       - retry_strategy: auto_retry | ask_user | skip
       - risk_level: low | medium | high
       - parallel_group: same group → concurrent execution
       - fallback_tool: alternate tool on failure
    3. Executes tools via tool_registry.execute_detailed()
    4. Returns ToolResult messages via the Message Bus

Key distinction from monolithic Executor:
    - Self-contained: no dependency on Plan state or Orchestrator internals
    - Message-driven: input and output go through message_bus
    - Semantic-aware: execution policy is driven by tool annotations
    - No LLM calls: pure tool execution + policy (no generation/synthesis)

Usage:
    agent = ExecutorAgent(config=AgentConfig())
    async for event in agent.run(trace_id="..."):
        yield event
"""

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.message import AgentMessage, message_bus
from app.agent.planner import PlanStep
from app.agent.registry import ToolResult
from app.agent.tracing import ToolCallRecord, get_trace_store

logger = logging.getLogger(__name__)

# How long to wait for an execute message from the Orchestrator
_POLL_TIMEOUT = 10.0


class ExecutorAgent:
    """Policy-driven execution runtime.

    Executes a PlanStep by step, driven by semantic annotations.
    Does NOT make LLM calls — pure tool execution + retry/fallback policy.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        organization_id: int | None = None,
        user_id: int = 0,
        execution_mode: str = "dag",
    ):
        # A2 P0（fail-closed）：执行器直接调用工具，必须有组织上下文。
        if organization_id is None:
            raise ValueError("ExecutorAgent 缺少 organization_id，拒绝在无组织上下文下运行")
        self._config = config or AgentConfig()
        self._organization_id = organization_id
        self._user_id = user_id
        self._execution_mode = execution_mode  # "dag" or "serial"
        self._seen_fingerprints: set[int] = set()
        self._consecutive_low_gain: int = 0

    async def run(
        self,
        trace_id: str,
        ctx: ExecutionContext | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Main loop: wait for execute message → execute → return results.

        Yields AgentEvent for the SSE frontend during execution.
        Sends a "result" AgentMessage to the bus when complete.

        Args:
            trace_id: Link to the parent Orchestrator trace.
            ctx: Optional ExecutionContext for observability.
        """
        # Wait for execute message from Orchestrator
        msg = message_bus.poll_one("executor", trace_id, timeout=_POLL_TIMEOUT)
        if msg is None:
            yield AgentEvent(type="error", content="Executor Agent: 等待执行消息超时")
            message_bus.send(
                AgentMessage.error_msg(
                    trace_id,
                    "executor",
                    "等待执行消息超时",
                )
            )
            return

        if msg.msg_type == "abort":
            return

        if msg.msg_type != "execute":
            yield AgentEvent(type="error", content=f"Executor Agent: 未知消息类型 {msg.msg_type}")
            return

        plan_data = msg.payload.get("plan", {})
        steps_data = plan_data.get("steps", [])

        yield AgentEvent(
            type="thinking",
            content=f"执行器开始执行 {len(steps_data)} 个步骤",
            thinking_type="reasoning",
        )

        # Parse steps into PlanStep objects
        steps = []
        for s in steps_data:
            steps.append(
                PlanStep(
                    id=s.get("id", "s?"),
                    description=s.get("description", ""),
                    dependencies=s.get("dependencies", []),
                    tool_hint=s.get("tool_hint"),
                    parallel_group=s.get("parallel_group"),
                    risk_level=s.get("risk_level", "low"),
                    retry_strategy=s.get("retry_strategy", "auto_retry"),
                    fallback_tool=s.get("fallback_tool"),
                )
            )

        # Execute steps
        completed: dict[str, str] = {}  # step_id → result summary
        step_results: list[dict[str, Any]] = []
        total = len(steps)
        done = 0

        if self._execution_mode == "serial":
            # ── SERIAL MODE: ignore dependencies, execute in definition order ──
            # This simulates a ReAct-like execution path for benchmark comparison.
            for step in steps:
                step.status = "running"
                result = await self._execute_one_step(step, ctx)
                for event in self._emit_step_events(step, result):
                    yield event
                completed[step.id] = str(result.data)[:200] if result.success else ""
                step_results.append(
                    {
                        "step_id": step.id,
                        "description": step.description,
                        "status": "completed" if result.success else "failed",
                        "result": str(result.data)[:2000] if result.success else None,
                        "error": result.error.to_dict() if result.error else None,
                        "latency_ms": result.meta.latency_ms,
                        "tool_hint": step.tool_hint,
                    }
                )
                if result.success:
                    done += 1
        else:
            # ── DAG MODE: dependency-aware, parallel group execution ──
            while done < total:
                # Find ready steps (all dependencies satisfied)
                ready = []
                for step in steps:
                    if step.status != "pending":
                        continue
                    if all(dep in completed for dep in step.dependencies):
                        ready.append(step)

                if not ready:
                    # Deadlock or all remaining are stuck
                    remaining = [s for s in steps if s.status == "pending"]
                    for s in remaining:
                        s.status = "skipped"
                        step_results.append(
                            {
                                "step_id": s.id,
                                "description": s.description,
                                "status": "skipped",
                                "result": None,
                                "error": "依赖条件无法满足（死锁）",
                                "tool_hint": s.tool_hint,
                            }
                        )
                    break

                # Group by parallel_group for concurrent execution
                groups: dict[int | None, list[PlanStep]] = {}
                for step in ready:
                    g = step.parallel_group
                    if g not in groups:
                        groups[g] = []
                    groups[g].append(step)

                # Execute each group (sequential between groups, parallel within)
                for group_id, group_steps in groups.items():
                    if len(group_steps) > 1 and group_id is not None:
                        # Parallel execution
                        tasks = [self._execute_one_step(s, ctx) for s in group_steps]
                        results = await asyncio.gather(*tasks)
                        for step, result in zip(group_steps, results, strict=False):
                            for event in self._emit_step_events(step, result):
                                yield event
                            completed[step.id] = str(result.data)[:200] if result.success else ""
                            step_results.append(
                                {
                                    "step_id": step.id,
                                    "description": step.description,
                                    "status": "completed" if result.success else "failed",
                                    "result": str(result.data)[:2000] if result.success else None,
                                    "error": result.error.to_dict() if result.error else None,
                                    "latency_ms": result.meta.latency_ms,
                                    "tool_hint": step.tool_hint,
                                }
                            )
                            if result.success:
                                done += 1
                    else:
                        # Sequential execution
                        for step in group_steps:
                            result = await self._execute_one_step(step, ctx)
                            for event in self._emit_step_events(step, result):
                                yield event
                            completed[step.id] = str(result.data)[:200] if result.success else ""
                            step_results.append(
                                {
                                    "step_id": step.id,
                                    "description": step.description,
                                    "status": "completed" if result.success else "failed",
                                    "result": str(result.data)[:2000] if result.success else None,
                                    "error": result.error.to_dict() if result.error else None,
                                    "latency_ms": result.meta.latency_ms,
                                    "tool_hint": step.tool_hint,
                                }
                            )
                            if result.success:
                                done += 1

        # Yield step results back to Orchestrator (JSON-serialized)
        for sr in step_results:
            yield AgentEvent(
                type="execution_step_result", content=json.dumps(sr, ensure_ascii=False)
            )

        yield AgentEvent(
            type="thinking",
            content=f"执行完成: {done}/{total} 步骤成功",
            thinking_type="evaluation",
        )

    async def _execute_one_step(
        self,
        step: PlanStep,
        ctx: ExecutionContext | None = None,
    ) -> ToolResult:
        """Execute a single step with semantic-aware retry/fallback policy.

        Also records ToolCallRecords into the global trace store for
        observability and failure analysis.

        Policy decisions:
            retry_strategy="auto_retry" → retry up to max_retries with backoff
            retry_strategy="ask_user"    → 1 attempt, then ask
            retry_strategy="skip"        → 1 attempt, skip on failure
            fallback_tool set            → try alternate tool after retries exhaust
        """
        step.status = "running"
        trace_store = get_trace_store()
        tool_calls: list[ToolCallRecord] = []
        fallback_used = False
        total_retries = 0

        # Determine max attempts based on retry_strategy
        max_attempts = {
            "auto_retry": self._config.max_retries_per_step + 1,
            "ask_user": 2,
            "skip": 1,
        }.get(step.retry_strategy, 1)

        last_error: ToolResult | None = None
        step_start = time.perf_counter()

        for attempt in range(max_attempts):
            if attempt > 0:
                step.retry_count = attempt
                total_retries = attempt
                backoff = min(2.0, 0.5 * (2 ** (attempt - 1)))
                await asyncio.sleep(backoff)

            # Execute the tool
            result, tc_record = await self._call_tool(step, attempt > 0)
            tool_calls.append(tc_record)

            if result.success:
                step_duration = (time.perf_counter() - step_start) * 1000
                # Record the step with all tool calls
                self._record_step_trace(
                    trace_store,
                    step,
                    tool_calls,
                    fallback_used,
                    total_retries,
                    step_duration,
                    success=True,
                )
                return result

            last_error = result

            if step.retry_strategy == "skip":
                break

        # ── Retries exhausted — try fallback_tool if available ──
        if last_error and step.fallback_tool and step.retry_strategy != "skip":
            logger.info(
                "Step %s: retries exhausted, trying fallback tool '%s'",
                step.id,
                step.fallback_tool,
            )
            fallback_used = True
            fallback_step = PlanStep(
                id=step.id,
                description=step.description,
                dependencies=step.dependencies,
                tool_hint=step.fallback_tool,
                parallel_group=step.parallel_group,
                risk_level=step.risk_level,
                retry_strategy="auto_retry",
                fallback_tool=None,
            )
            result, tc_record = await self._call_tool(fallback_step, retry=True)
            tc_record.fallback_used = True
            tool_calls.append(tc_record)
            if result.success:
                step_duration = (time.perf_counter() - step_start) * 1000
                self._record_step_trace(
                    trace_store,
                    step,
                    tool_calls,
                    fallback_used,
                    total_retries,
                    step_duration,
                    success=True,
                )
                return result

        step.status = "failed"
        step_duration = (time.perf_counter() - step_start) * 1000
        self._record_step_trace(
            trace_store,
            step,
            tool_calls,
            fallback_used,
            total_retries,
            step_duration,
            success=False,
        )
        return last_error or ToolResult.fail(
            code="execution_failed",
            message=f"步骤 {step.id} 执行失败（已重试）",
        )

    def _record_step_trace(
        self,
        trace_store,
        step: PlanStep,
        tool_calls: list[ToolCallRecord],
        fallback_used: bool,
        retry_count: int,
        duration_ms: float,
        success: bool,
    ) -> None:
        """Record a StepExecutionRecord for the current step."""
        from app.agent.tracing import StepExecutionRecord

        trace_store._pending_step = StepExecutionRecord(
            step_id=step.id,
            description=step.description,
            tool_hint=step.tool_hint,
            semantic_type="read_only",
            retry_strategy=step.retry_strategy,
            risk_level=step.risk_level,
            parallel_group=step.parallel_group,
            status="completed" if success else "failed",
            duration_ms=duration_ms,
            retry_count=retry_count,
            fallback_used=fallback_used,
            tool_calls=tool_calls,
            error=None if success else (tool_calls[-1].error_message if tool_calls else "unknown"),
        )

    # Parameters a step's description can safely stand in for: a step such as
    # "检索 2024 年营收" is itself the query text the tool wants. Anything else
    # (ids, structured payloads, multi-arg tools) cannot be guessed.
    _TEXTUAL_PARAMS = ("query", "text", "code", "url", "expression")

    def _resolve_tool_arguments(self, step: PlanStep, entry) -> tuple[dict, str | None]:
        """Best-effort conversion of a plan step into the tool's arguments.

        Returns (arguments, error). An error means the step cannot be dispatched
        honestly — better a diagnosable validation_error than a TypeError from
        calling the handler with an empty dict.
        """
        params = (entry.parameters or {}).get("properties") or {}
        required = list((entry.parameters or {}).get("required") or [])
        if not required:
            return {}, None

        arguments: dict[str, Any] = {}
        unresolved: list[str] = []
        for name in required:
            if name in self._TEXTUAL_PARAMS and name in params:
                arguments[name] = step.description
            else:
                unresolved.append(name)

        if unresolved:
            return {}, (
                f"步骤 {step.id} 无法为工具 '{step.tool_hint}' 解析参数 {unresolved}"
                f"（步骤描述中缺少这些值）"
            )
        return arguments, None

    async def _call_tool(
        self, step: PlanStep, retry: bool = False
    ) -> tuple[ToolResult, ToolCallRecord]:
        """Call the actual tool via ToolRegistry.

        Returns:
            (ToolResult, ToolCallRecord) — the result plus a trace record.
        """
        if not step.tool_hint:
            tr = ToolResult.fail(code="no_tool", message=f"步骤 {step.id} 未指定工具")
            return tr, ToolCallRecord(
                tool_name="(none)",
                args_summary="",
                success=False,
                error_code="no_tool",
                error_message=tr.error.message,
            )

        from app.agent.registry import tool_registry as registry

        entry = registry.get(step.tool_hint)
        if not entry:
            tr = ToolResult.fail(code="not_found", message=f"未知工具 '{step.tool_hint}'")
            return tr, ToolCallRecord(
                tool_name=step.tool_hint,
                args_summary="",
                success=False,
                error_code="not_found",
                error_message=tr.error.message,
            )

        arguments, arg_error = self._resolve_tool_arguments(step, entry)
        if arg_error:
            tr = ToolResult.fail(code="validation_error", message=arg_error)
            return tr, ToolCallRecord(
                tool_name=step.tool_hint,
                args_summary="",
                success=False,
                error_code="validation_error",
                error_message=arg_error,
            )

        start = time.perf_counter()
        result = await registry.execute_detailed(
            step.tool_hint,
            arguments,
            organization_id=self._organization_id,
            user_id=self._user_id,
        )
        elapsed = (time.perf_counter() - start) * 1000

        record = ToolCallRecord(
            tool_name=step.tool_hint,
            args_summary=str({k: str(v)[:50] for k, v in arguments.items()})[:200],
            success=result.success,
            error_code=result.error.code if result.error else None,
            error_message=result.error.message if result.error else None,
            latency_ms=elapsed,
            retry_attempt=step.retry_count if retry else 0,
            fallback_used=False,
        )
        return result, record

    def _emit_step_events(self, step: PlanStep, result: ToolResult) -> list[AgentEvent]:
        """Yield AgentEvent for a completed step (for SSE frontend)."""
        events = []

        if result.success:
            step.status = "completed"
            events.append(
                AgentEvent(
                    type="thinking",
                    content=f"  ✅ {step.description}",
                    thinking_type="evaluation",
                    plan_step_id=step.id,
                    plan_step_status="completed",
                )
            )
            # Yield tool result as chunk
            data_str = str(result.data)[:500] if result.data else ""
            if data_str:
                events.append(
                    AgentEvent(
                        type="chunk",
                        content=data_str,
                        plan_step_id=step.id,
                    )
                )
        else:
            step.status = "failed"
            error_str = result.error.message if result.error else "未知错误"
            events.append(
                AgentEvent(
                    type="tool_error",
                    tool_name=step.tool_hint or "unknown",
                    content=f"  ❌ {step.description}: {error_str}",
                    plan_step_id=step.id,
                )
            )

        return events
