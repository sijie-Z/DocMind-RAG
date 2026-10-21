"""Orchestrator — lightweight coordinator for the Multi-Agent OS.

Orchestrator is NOT an LLM-powered agent. It is a deterministic
coordinator that:
    1. Receives a user query
    2. Sends messages to Planner → Executor → Reviewer agents
    3. Collects results and returns the final answer
    4. Manages trace_id for end-to-end observability

It delegates all LLM work to specialised agents and only does
routing, state management, and error handling.

Usage:
    orch = Orchestrator()
    async for event in orch.run("查询飞书多维表格中状态为待审批的记录"):
        yield event
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.message import AgentMessage, message_bus
from app.agent.planner import Plan, Planner, PlanStep
from app.agent.tracing import ExecutionTrace, get_trace_store

logger = logging.getLogger(__name__)


class Orchestrator:
    """Multi-Agent coordinator — routes messages, manages lifecycle.

    Current phase (Step 1): messages are logged but agents are not yet
    fully independent. The Orchestrator wraps the existing PERAgentLoop
    components (Planner + Executor + Reviewer) and sends messages for
    observability. Step 2 and 3 will make each agent fully independent.
    """

    def __init__(
        self,
        planner: Planner | None = None,
        config: AgentConfig | None = None,
        organization_id: int | None = None,
        user_id: int = 0,
    ):
        # A2 P0（fail-closed）：编排器下的执行器必须有组织上下文。
        if organization_id is None:
            raise ValueError("Orchestrator 缺少 organization_id，拒绝在无组织上下文下运行")
        self._config = config or AgentConfig()
        self._planner = planner
        self._organization_id = organization_id
        self._user_id = user_id

    @property
    def bus(self):
        return message_bus

    async def run(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        context_docs: list[dict[str, Any]] | None = None,
        trace_id: str | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute a user query through the Multi-Agent pipeline.

        Yields AgentEvent objects compatible with the existing SSE frontend.

        Args:
            query: User query string.
            history: Optional conversation history.
            context_docs: Optional pre-retrieved context documents.
            trace_id: Optional trace ID for observability. Auto-generated if not provided.

        Phase 1 (current): components are called in-process, messages
        are sent for observability.
        """
        trace_id = trace_id or uuid.uuid4().hex[:12]
        start_time = time.perf_counter()
        ctx = ExecutionContext(query=query)

        # ── Step 1: Send plan_request → Planner ───────────────────────
        planner_msg = AgentMessage.plan_request(trace_id, query)
        message_bus.send(planner_msg)

        yield AgentEvent(
            type="thinking",
            content="正在分析任务并制定执行计划...",
            thinking_type="reasoning",
        )

        # 1a. Generate plan (using existing Planner)
        plan = await self._run_planner(query, history, ctx)
        if not plan or not plan.steps:
            yield AgentEvent(type="error", content="无法为当前任务生成执行计划")
            return

        # 1b. Send plan result message
        plan_msg = AgentMessage(
            msg_type="plan",
            sender="planner",
            target="orchestrator",
            trace_id=trace_id,
            payload={
                "goal": plan.goal,
                "reasoning": plan.reasoning,
                "steps": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "dependencies": s.dependencies,
                        "tool_hint": s.tool_hint,
                        "parallel_group": s.parallel_group,
                        "risk_level": s.risk_level,
                        "retry_strategy": s.retry_strategy,
                        "fallback_tool": s.fallback_tool,
                    }
                    for s in plan.steps
                ],
            },
            parent_msg_id=planner_msg.msg_id,
        )
        message_bus.send(plan_msg)

        # ── Step 2: Send execute → Executor ──────────────────────────
        yield AgentEvent(
            type="thinking",
            content=f"计划已生成，共 {len(plan.steps)} 个步骤",
            thinking_type="reasoning",
        )

        exec_msg = AgentMessage.execute_request(trace_id, plan_msg.payload, plan_msg.msg_id)
        message_bus.send(exec_msg)

        # 2a. Execute plan via ExecutorAgent (message-driven, autonomous)
        final_output = ""
        step_results: list[dict[str, Any]] = []

        from app.agent.agents.executor_agent import ExecutorAgent

        exec_agent = ExecutorAgent(
            config=self._config,
            organization_id=self._organization_id,
            user_id=self._user_id,
        )

        # ExecutorAgent polls the bus for its execute message, runs steps
        # Step results are yielded as "execution_step_result" events
        async for event in exec_agent.run(trace_id=trace_id, ctx=ctx):
            if event.type == "chunk":
                final_output += event.content
            elif event.type == "execution_step_result":
                if isinstance(event.content, str):
                    try:
                        step_results.append(json.loads(event.content))
                    except (json.JSONDecodeError, TypeError):
                        pass
            yield event

        # ── Step 3: Send review → Reviewer ───────────────────────────
        review_msg = AgentMessage.review_request(
            trace_id,
            plan_msg.payload,
            step_results,
            exec_msg.msg_id,
        )
        message_bus.send(review_msg)

        # 3a. Run review (using existing review logic)
        issues = await self._run_review(plan, step_results, ctx)
        if issues:
            for issue in issues:
                final_output += (
                    f"\n\n⚠️ **审查发现**: {issue.get('description', '')}\n"
                    f"  建议: {issue.get('suggestion', '')}"
                )

        verdict_msg = AgentMessage(
            msg_type="verdict",
            sender="reviewer",
            target="orchestrator",
            trace_id=trace_id,
            payload={"passed": len(issues) == 0, "issues": issues},
            parent_msg_id=review_msg.msg_id,
        )
        message_bus.send(verdict_msg)

        # ── Step 4: Finalise ─────────────────────────────────────────
        ctx.mark_done()
        yield AgentEvent(
            type="execution_context",
            content=ctx.to_dict(),
            plan_id=plan.id if plan else "",
            plan_progress=1.0,
        )

        elapsed = (time.perf_counter() - start_time) * 1000

        # ── Record execution trace for observability ───────────────
        try:
            # Build StepExecutionRecord list from step_results
            from app.agent.tracing import StepExecutionRecord, ToolCallRecord

            trace_steps = []
            tools_used = set()
            total_tool_calls = 0
            completed = 0
            failed = 0

            for sr in step_results:
                status = sr.get("status", "")
                if status == "completed":
                    completed += 1
                elif status == "failed":
                    failed += 1
                th = sr.get("tool_hint")
                if th:
                    tools_used.add(th)

                # Create a StepExecutionRecord for this step
                ser = StepExecutionRecord(
                    step_id=sr.get("step_id", ""),
                    description=sr.get("description", ""),
                    tool_hint=th,
                    status=status,
                    duration_ms=sr.get("latency_ms", 0.0),
                    retry_count=sr.get("retry_count", 0),
                    fallback_used=sr.get("fallback_used", False),
                    error=sr.get("error", None),
                )
                # If there's an error, create a ToolCallRecord
                error_info = sr.get("error")
                if error_info:
                    tcr = ToolCallRecord(
                        tool_name=th or "?",
                        args_summary="",
                        success=(status == "completed"),
                        error_code=error_info.get("code") if isinstance(error_info, dict) else None,
                        error_message=error_info.get("message")
                        if isinstance(error_info, dict)
                        else str(error_info),
                        latency_ms=sr.get("latency_ms", 0.0),
                    )
                    ser.tool_calls.append(tcr)
                    total_tool_calls += 1

                trace_steps.append(ser)

            trace = ExecutionTrace(
                trace_id=trace_id,
                query=query,
                plan_goal=plan.goal if plan else "",
                total_steps=len(step_results) or (plan.total_steps if plan else 0),
                completed_steps=completed,
                failed_steps=failed,
                total_duration_ms=elapsed,
                overall_success=(failed == 0),
                tools_used=sorted(tools_used),
                steps=trace_steps,
                planner_tool_hints=sum(1 for s in plan.steps if s.tool_hint) if plan else 0,
                executor_tool_calls=total_tool_calls,
            )
            get_trace_store().record(trace)
            logger.debug("Trace recorded: %s", trace.summary())
        except Exception as e:
            logger.warning("Failed to record trace: %s", e)

        logger.info(
            "Orchestrator completed trace=%s in %.0fms | steps=%d/%d | output=%d chars",
            trace_id,
            elapsed,
            plan.completed_steps if plan else 0,
            plan.total_steps if plan else 0,
            len(final_output),
        )

        # Cleanup trace messages
        message_bus.clear_trace(trace_id)

        yield AgentEvent(type="done", plan_progress=1.0)

    async def _run_planner(
        self,
        query: str,
        history: list[dict[str, str]] | None,
        ctx: ExecutionContext,
    ) -> Plan | None:
        """Generate plan using the existing Planner.

        This will become an independent Planner Agent call in Step 2.
        """
        if not self._planner:
            return None

        plan_id = uuid.uuid4().hex[:12]
        plan_goal = query[:80]
        plan_steps: list[PlanStep] = []

        async for event in self._planner.plan(query=query, history=history, ctx=ctx):
            if event.type == "plan_step" and event.plan_step_id:
                plan_steps.append(
                    PlanStep(
                        id=event.plan_step_id,
                        description=event.content,
                        dependencies=event.dependencies or [],
                        tool_hint=event.tool_hint or None,
                    )
                )

        if not plan_steps:
            plan_steps = [PlanStep(id="s1", description=query)]
            plan_goal = query[:80]

        plan = Plan(
            id=plan_id,
            goal=plan_goal or query[:80],
            reasoning="Plan generated by Planner Agent",
            steps=plan_steps,
        )
        ctx.goal = plan.goal
        ctx.plan_summary = f"{len(plan.steps)} 个步骤"
        return plan

    async def _run_review(
        self,
        plan: Plan,
        step_results: list[dict[str, Any]],
        ctx: ExecutionContext,
    ) -> list[dict[str, Any]]:
        """Run review on execution results.

        This will become an independent Reviewer Agent call in Step 3.
        """
        from app.agent.reviewer import Reviewer

        if not self._planner or not self._planner.client:
            return []

        reviewer = Reviewer(
            openai_client=self._planner.client,
            config=self._config,
        )

        review = await reviewer.review(plan, step_results, ctx=ctx)
        return review.get("issues_found", [])
