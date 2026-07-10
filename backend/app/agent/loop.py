"""PER (Planning-Execution-Reflection) agent loop — the core execution engine.

Flow:
    Phase 0: Memory recall (MemoryBridge)
    Phase 1: Planning (Planner — streaming thinking → structured plan DAG)
    Phase 2: Execution (Executor — step-by-step with retry logic)
    Phase 3: Reflection (Reflector — evaluate, correct, or finalize)
    Phase 4: Store memories + yield final answer

Backward-compatible: PERAgentLoop.run() yields the same AgentEvent types as
the old AgentLoop plus new event types (thinking, plan_*, reflection).
"""

import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.context import ContextEngine
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, Planner, PlanStep
from app.agent.reflector import Reflector
from app.agent.registry import tool_registry
from app.agent.reviewer import Reviewer
from app.agent.run_report import build_run_report, render_run_report_text

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = [
    "AgentEvent",
    "AgentConfig",
    "PERAgentLoop",
    # Legacy alias
    "AgentLoop",
]

MAX_RESULT_TOKENS = 3000


def _smart_truncate(result: str, tool_name: str) -> str:
    """Smart truncation that preserves structure based on tool type."""
    if not result or len(result) <= MAX_RESULT_TOKENS * 2:
        return result

    if tool_name in ("list_documents", "list_conversations", "list_prompt_templates"):
        lines = result.split("\n")
        kept = lines[:15]
        remaining = len(lines) - 15
        if remaining > 0:
            kept.append(f"... and {remaining} more items")
        return "\n".join(kept)

    if tool_name in ("search_knowledge_base", "vector_search", "web_search"):
        sections = result.split("\n\n")
        if len(sections) > 3:
            return "\n\n".join(sections[:3]) + f"\n\n... and {len(sections) - 3} more results omitted"
        return result

    if tool_name in ("summarize_document", "get_conversation_history", "fetch_webpage"):
        head = result[:1500]
        tail = result[-500:]
        return f"{head}\n\n... [middle omitted] ...\n\n{tail}"

    return result[:MAX_RESULT_TOKENS * 2] + "\n...[result truncated]"


DEFAULT_SYSTEM_PROMPT = """你是 DocMind 智能助手，一个基于企业知识库的 AI Agent。

## 能力
你可以使用工具来完成以下任务：
- **知识检索**: 搜索知识库、向量语义搜索、网页搜索
- **文档分析**: 摘要、关键词提取、文档对比、数据分析
- **知识管理**: 查看文档列表和详情
- **对话管理**: 查看历史对话和消息记录
- **语言处理**: 翻译、语言检测
- **数据工具**: Python 代码执行、SQL 查询、数学计算、格式转换
- **系统工具**: 时间查询、系统状态、天气查询

## 工作流程
1. 理解用户意图
2. **复杂任务自动拆解**：如果用户请求涉及多个步骤（如"分析这10份报告的共同风险"），先制定执行计划，再逐步执行
3. 如果需要查找信息，使用 search_knowledge_base 工具
4. 如果需要分析多个文档，逐个调用 summarize_document，然后综合分析
5. 基于实际获取到的数据回答，不编造信息

## 约束
- 只基于知识库中的文档和工具返回的数据回答
- 使用 [n] 格式标注引用来源（如果适用）
- 始终使用简体中文回答
- 复杂任务自动拆解为子任务，分步执行"""


class PERAgentLoop:
    """Planning → Execution → Reflection agent loop.

    Orchestrates the conversation between user, LLM, tools, and memory.
    Replaces the old ReAct-only AgentLoop with the full PER architecture.
    """

    def __init__(
        self,
        openai_client: AsyncOpenAI,
        config: AgentConfig | None = None,
        organization_id: int = 1,
        user_id: int = 0,
        execution_mode: str = "dag",
    ):
        self.client = openai_client
        self.config = config or AgentConfig()
        self.organization_id = organization_id
        self.user_id = user_id
        self._execution_mode = execution_mode

        # Context engine with LLM-based summarization
        self.context_engine = ContextEngine(
            max_context_tokens=self.config.max_context_tokens,
            tail_window=6,
        )
        if self.client:
            self.context_engine.set_llm_client(self.client)

        # Memory bridge
        self.memory = AgentMemoryBridge(
            agent_id=self.config.agent_id,
            organization_id=organization_id,
            user_id=user_id,
        )

        # PER components (created on demand)
        self._planner: Planner | None = None
        self._executor: Executor | None = None
        self._reflector: Reflector | None = None
        self._reviewer: Reviewer | None = None

    @property
    def planner(self) -> Planner:
        if self._planner is None:
            self._planner = Planner(self.client, self.config)
        return self._planner

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            self._executor = Executor(self.client, self.config, self.memory, execution_mode=self._execution_mode)
        return self._executor

    @property
    def reflector(self) -> Reflector:
        if self._reflector is None:
            self._reflector = Reflector(self.client, self.config, self.memory)
        return self._reflector

    @property
    def reviewer(self) -> Reviewer:
        if self._reviewer is None:
            self._reviewer = Reviewer(self.client, self.config)
        return self._reviewer

    def _should_continue_execution(self, final_output: str, plan, remaining_steps: list) -> bool:
        """Conditional edge: decide if execution should continue or proceed to reflection.

        Checks whether the accumulated answer already contains sufficient information
        to address the user's query. If yes, remaining steps are auto-skipped to
        avoid unnecessary tool calls (inspired by TradingAgents' conditional routing).

        Returns True to continue executing, False to skip remaining steps.
        """
        if not remaining_steps:
            return False

        # If no output yet, definitely continue
        if len(final_output.strip()) < 50:
            return True

        # If the output contains SQL results or search results with clear answers,
        # and remaining steps don't add new tools, we can skip
        has_data = any(marker in final_output for marker in [
            '"rows":', '"data":', "找到", "根据",
            "综上所述", "结论", "建议",
        ])

        # If we have substantial output AND the next steps are just more of the same
        # (no new tool types), skip to avoid redundancy
        # If we have substantial output AND remaining steps add no new tool types, skip
        return not (has_data and len(final_output) > 300)

    async def run(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        context_docs: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Execute the PER agent loop, yielding events as they happen.

        Args:
            query: The user's question or request.
            history: Previous conversation messages.
            context_docs: Pre-retrieved document context (optional, for RAG mode).

        Yields:
            AgentEvent objects for thinking, plan, tool calls, chunks, etc.
        """
        start_time = time.perf_counter()

        # ── Execution Context (per-request state) ────────────────────────
        ctx = ExecutionContext(query=query)

        # ── Phase 0: Memory recall ────────────────────────────────────────
        memory_context = ""
        if self.config.enable_memory:
            try:
                memory_context = await self.memory.get_context_for_query(query)
                if memory_context:
                    yield AgentEvent(
                        type="thinking",
                        content="回忆相关上下文...",
                        thinking_type="reasoning",
                    )
                    await _yield_control()
            except Exception as e:
                logger.warning(f"Memory recall failed: {e}")

        # ── Phase 1: Planning ────────────────────────────────────────────
        plan: Plan | None = None
        import uuid as _uuid_mod

        if self.config.enable_planning:
            # Collect plan steps from events as the planner emits them
            plan_id = ""
            plan_goal = query[:80]
            plan_reasoning = ""
            plan_steps: list[PlanStep] = []

            async for event in self.planner.plan(
                query=query,
                history=history,
                memory_context=memory_context,
                ctx=ctx,
            ):
                yield event
                if event.type == "plan_start":
                    plan_id = event.plan_id
                elif event.type == "plan_step" and event.plan_step_id:
                    plan_steps.append(PlanStep(
                        id=event.plan_step_id,
                        description=event.content,
                        dependencies=event.dependencies or [],
                        tool_hint=event.tool_hint or None,
                    ))
                elif event.type == "plan_complete":
                    plan_goal = event.plan_id

            if plan_steps:
                plan = Plan(
                    id=plan_id or _uuid_mod.uuid4().hex[:12],
                    goal=plan_goal,
                    reasoning=plan_reasoning,
                    steps=plan_steps,
                )
                ctx.goal = plan_goal
                ctx.plan_summary = f"{len(plan_steps)} 个步骤"
        else:
            plan = Plan(
                id=_uuid_mod.uuid4().hex[:12],
                goal=query[:80],
                reasoning="规划已禁用，直接执行。",
                steps=[PlanStep(id="s1", description=query)],
            )
            yield AgentEvent(
                type="plan_complete",
                plan_id=plan.id,
                content="直接执行模式",
                plan_progress=0.0,
            )

        if not plan or not plan.steps:
            plan = Plan(
                id=_uuid_mod.uuid4().hex[:12],
                goal=query[:80],
                reasoning="默认计划",
                steps=[PlanStep(id="s1", description=query)],
            )

        # ── Phase 2: Execution ───────────────────────────────────────────
        final_output = ""

        if self.config.enable_tools:
            async for event in self.executor.execute(
                plan=plan,
                history=history,
                organization_id=self.organization_id,
                user_id=self.user_id,
                enable_thinking=self.config.enable_thinking,
                ctx=ctx,
            ):
                yield event
                if event.type == "chunk":
                    final_output += event.content

            # Conditional routing: if remaining steps are unlikely to add value, skip them
            remaining_steps = [s for s in plan.steps if s.status == "pending"]
            if remaining_steps and not self._should_continue_execution(final_output, plan, remaining_steps):
                skipped = [s for s in remaining_steps if s.status == "pending"]
                for s in skipped:
                    s.status = "skipped"
                if skipped:
                    yield AgentEvent(
                        type="thinking",
                        content=f"已有充分信息，跳过 {len(skipped)} 个剩余步骤",
                        thinking_type="evaluation",
                        plan_id=plan.id,
                        plan_progress=plan.progress,
                    )
                    logger.info("Conditional routing: %d steps skipped (sufficient info)", len(skipped))
        else:
            # Tool-less mode: single LLM call
            messages = self._build_messages(query, history, context_docs, memory_context)
            try:
                response = await self.client.chat.completions.create(
                    model=self.config.get_deep_model(),
                    messages=messages,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    timeout=self.config.timeout,
                )
                content = response.choices[0].message.content or ""
                if content:
                    yield AgentEvent(type="chunk", content=content)
                    final_output = content
            except Exception as e:
                yield AgentEvent(type="error", content=f"LLM Error: {e}")

        # ── Phase 2b: Quality Gate ────────────────────────────────────────
        if plan.steps:
            from app.agent.quality_gate import quality_gate_check
            step_results = self.memory.get_all_results()
            qg_result = quality_gate_check(step_results)
            if not qg_result["passed"]:
                fatal = qg_result.get("fatal_issues", [])
                warnings = qg_result["summary"]
                if fatal:
                    yield AgentEvent(
                        type="thinking",
                        content=f"质量门控发现 {len(fatal)} 个问题:\n" + "\n".join(fatal),
                        thinking_type="evaluation",
                    )
                if self.config.enable_thinking:
                    yield AgentEvent(
                        type="thinking",
                        content=warnings[:500],
                        thinking_type="evaluation",
                    )
                logger.info("Quality gate: %d passed, %d concerns, %d failed",
                    sum(1 for g, _ in qg_result["grades"].values() if g == "A"),
                    sum(1 for g, _ in qg_result["grades"].values() if g == "C"),
                    sum(1 for g, _ in qg_result["grades"].values() if g == "F"),
                )

        # ── Phase 3: Reflection ──────────────────────────────────────────
        if self.config.enable_reflection and plan.steps:
            results = self.memory.get_all_results()
            async for event in self.reflector.reflect(plan, results, ctx=ctx):
                yield event
                # Check reflection decision
                if event.type == "reflection" and event.reflection_result == "retry":
                    # Retry specific step
                    step_id = event.plan_step_id
                    if step_id:
                        for step in plan.steps:
                            if step.id == step_id:
                                step.status = "pending"
                                step.retry_count += 1
                                yield AgentEvent(
                                    type="thinking",
                                    content=f"重新执行步骤: {step.description}",
                                    thinking_type="correction",
                                    plan_id=plan.id,
                                    plan_step_id=step.id,
                                    retry_attempt=step.retry_count,
                                )
                                # Re-execute just this step
                                async for retry_event in self.executor._execute_step_with_retry(
                                    step=step,
                                    plan=plan,
                                    history=history,
                                    organization_id=self.organization_id,
                                    user_id=self.user_id,
                                    enable_thinking=self.config.enable_thinking,
                                ):
                                    if retry_event.type == "chunk":
                                        final_output += "\n\n[重试结果]\n" + retry_event.content
                                    yield retry_event
                                break

            # ── Phase 3b: Adversarial Review ────────────────────────────────
            if self.config.enable_reflection and plan.steps:
                results = self.memory.get_all_results()
                yield AgentEvent(
                    type="thinking",
                    content="进行对抗性质检，审查结果中可能存在的问题...",
                    thinking_type="evaluation",
                )
                await _yield_control()
                review = await self.reviewer.review(plan, results)
                issues = review.get("issues_found", [])
                if issues:
                    high_issues = [i for i in issues if i.get("severity") == "high"]
                    if high_issues:
                        logger.info("Adversarial review found %d high-severity issues", len(high_issues))
                        for issue in high_issues:
                            final_output += (
                                f"\n\n⚠️ **审查发现**: {issue.get('description', '')}\n"
                                f"  建议: {issue.get('suggestion', '')}"
                            )
                            yield AgentEvent(
                                type="thinking",
                                content=f"审查发现高风险问题: {issue.get('description', '')}",
                                thinking_type="evaluation",
                            )
                    # Log all issues
                    for issue in issues:
                        logger.info(
                            "Review issue [%s]: %s",
                            issue.get("severity", "unknown"),
                            issue.get("description", "")[:100],
                        )

        # ── Phase 4: Store + Finalize ─────────────────────────────────────
        await self.memory.record_interaction(query, final_output)

        ctx.mark_done()
        ctx.record_decision("finalize", "complete", "Agent loop finished")
        # Persist for execution replay
        try:
            await ctx.save()
        except Exception:
            pass

        # ── Run report (observe-only, no behavior change) ───────────────
        # Pure projection of what the run already recorded into ctx.
        # Does NOT trigger retry/replan/approval/memory/experience. See ADR-1.
        run_report = build_run_report(ctx, final_output)
        try:
            logger.info("\n%s", render_run_report_text(run_report))
        except Exception:
            pass
        yield AgentEvent(
            type="run_report",
            content=render_run_report_text(run_report),
        )

        # Yield execution context as a data event for observability
        yield AgentEvent(
            type="execution_context",
            content=ctx.to_dict(),
            plan_id=plan.id if plan else "",
            plan_progress=1.0,
        )

        elapsed = (time.perf_counter() - start_time) * 1000
        logger.info(
            f"PER loop completed in {elapsed:.0f}ms | "
            f"Plan: {plan.goal[:50]}... | Steps: {plan.completed_steps}/{plan.total_steps} | "
            f"Output: {len(final_output)} chars"
        )

        yield AgentEvent(type="done", plan_progress=1.0)

    def _build_messages(
        self,
        query: str,
        history: list[dict[str, str]] | None,
        context_docs: list[dict[str, Any]] | None,
        memory_context: str = "",
    ) -> list[dict[str, Any]]:
        """Build the initial message list for the LLM."""
        messages: list[dict[str, Any]] = []

        # System prompt
        system_prompt = self.config.system_prompt_override or DEFAULT_SYSTEM_PROMPT

        if memory_context:
            system_prompt += f"\n\n{memory_context}"

        if context_docs:
            context_str = "\n\n".join([
                f"[{i + 1}] {doc.get('filename', 'Unknown')}:\n{doc.get('snippet', doc.get('text', ''))[:500]}"
                for i, doc in enumerate(context_docs[:5])
            ])
            system_prompt += f"\n\n## 已检索到的参考文档\n{context_str}"

        # Personality injection
        personality_guide = {
            "precise": "请给出准确、简洁、直接的回答。优先使用数据和事实。",
            "creative": "请给出富有洞察力的回答，可以适度发散思考。",
            "balanced": "请给出结构化、全面的回答，兼顾准确性和可读性。",
        }
        system_prompt += f"\n\n{personality_guide.get(self.config.personality, personality_guide['balanced'])}"

        messages.append({"role": "system", "content": system_prompt})

        # History
        if history:
            for msg in history[-8:]:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content and role in ("user", "assistant"):
                    messages.append({"role": role, "content": content})

        # Current query
        messages.append({"role": "user", "content": query})

        return messages

    def _get_tools(self) -> list[dict[str, Any]] | None:
        """Get tool definitions for the LLM, filtered by config."""
        if not self.config.enable_tools:
            return None
        tools = tool_registry.to_openai_tools(self.config.tool_tags)
        if self.config.disabled_tools and tools:
            tools = [
                t for t in tools
                if t.get("function", {}).get("name") not in self.config.disabled_tools
            ]
        return tools if tools else None

# ── Backward compatibility alias ──
AgentLoop = PERAgentLoop


async def _yield_control():
    """Yield control to the event loop for SSE flushing."""
    import asyncio
    await asyncio.sleep(0.005)
