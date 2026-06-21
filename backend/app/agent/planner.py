"""Planner — generates structured execution plans from user queries.

The Planner makes a dedicated LLM call to break down a user request into
a plan DAG (goal + steps + dependencies + tool hints). Planning reasoning
is streamed as `thinking` events so the frontend can render it in real time.

Plan format (JSON, extracted from LLM response):
{
    "goal": "分析最近10份财报，找出共同风险点",
    "steps": [
        {
            "id": "s1",
            "description": "列出所有文档",
            "dependencies": [],
            "tool_hint": "list_documents"
        },
        ...
    ]
}
"""

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.registry import tool_registry
from app.core.prometheus import AGENT_PLANNING_LATENCY, AGENT_PLANNING_TOTAL

logger = logging.getLogger(__name__)

PLANNING_SYSTEM_PROMPT = """你是一个任务规划专家。你的职责是将用户的复杂请求拆解为可执行的步骤。

## 可用工具
{tools_description}

## 语义感知规划原则（必须遵守）
1. 每步骤的工具选择必须匹配工具的**能力类型**:
   - read_only → 适合查询、检索、只读操作
   - mutating  → 修改数据的操作，需标记 risk_level="medium" 以上
   - long_running → 长时间任务，自动标记 risk_level="medium"

2. 并行执行规则（parallel_group）:
   - **read_only 且无依赖的步骤分配相同 parallel_group** → 同时执行
   - mutating 步骤单独分配 parallel_group → 不能与其它步骤并行
   - 数据依赖的步骤必须声明 dependencies，不能并行

3. 重试策略（retry_strategy）:
   - retry_safe=True → "auto_retry"（自动重试）
   - retry_safe=False 或 mutating 操作 → "ask_user"（询问用户后重试）

4. 风险等级（risk_level）:
   - 只读查询 → "low"
   - 涉及数据修改 → "medium" 或 "high"
   - 涉及删除/覆盖 → "high"

5. 总步骤数不超过 {max_steps} 个
6. 如果请求简单（无需多步），只生成 1-2 个步骤

## 输出格式
请严格按照以下 JSON 格式输出（不要包含其他文字）：
```json
{{
    "goal": "用户目标的一句话概括",
    "reasoning": "你的规划思路（2-3句话）",
    "steps": [
        {{
            "id": "s1",
            "description": "清晰的步骤描述",
            "dependencies": [],
            "tool_hint": "tool_name 或 null",
            "parallel_group": 1,
            "risk_level": "low",
            "retry_strategy": "auto_retry",
            "fallback_tool": null
        }}
    ]
}}
```
"""


@dataclass
class PlanStep:
    """A single step in an execution plan.

    v1 semantic-aware fields:
        parallel_group    — steps with the same group number can run in parallel
        risk_level        — "low" | "medium" | "high" (guides auto-execution)
        retry_strategy    — "auto_retry" | "ask_user" | "skip" (failure behaviour)
        fallback_tool     — alternative tool if the primary tool_hint fails
    """
    id: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    tool_hint: str | None = None
    status: str = "pending"  # pending, ready, running, completed, failed, skipped
    result: str | None = None
    error_context: str | None = None
    retry_count: int = 0

    # ── v1 semantic-aware fields ──
    parallel_group: int | None = None  # same number = can parallelize
    risk_level: str = "low"            # low | medium | high
    retry_strategy: str = "auto_retry" # auto_retry | ask_user | skip
    fallback_tool: str | None = None   # alternative tool


@dataclass
class Plan:
    """A structured execution plan."""
    id: str
    goal: str
    reasoning: str = ""
    steps: list[PlanStep] = field(default_factory=list)
    completed_steps: int = 0
    failed_steps: int = 0
    created_at: float = field(default_factory=time.time)

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def progress(self) -> float:
        if not self.steps:
            return 1.0
        return self.completed_steps / len(self.steps)

    @property
    def is_complete(self) -> bool:
        return all(s.status in ("completed", "skipped", "failed") for s in self.steps)

    def get_ready_steps(self) -> list[PlanStep]:
        """Return steps whose dependencies are all satisfied."""
        completed_ids = {s.id for s in self.steps if s.status == "completed"}
        ready = []
        for step in self.steps:
            if step.status != "pending":
                continue
            if all(dep in completed_ids for dep in step.dependencies):
                ready.append(step)
        return ready


class Planner:
    """Generates structured execution plans from user queries."""

    def __init__(self, openai_client: AsyncOpenAI, config: AgentConfig):
        self.client = openai_client
        self.config = config

    async def plan(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        memory_context: str = "",
        ctx: Any = None,  # ExecutionContext (avoid circular import)
    ) -> AsyncGenerator[AgentEvent, Plan | None]:
        """Generate a plan, yielding thinking events as reasoning streams.

        Yields: thinking events during plan generation
        Sends back: Plan object when complete (via generator return)
        """
        plan_id = uuid.uuid4().hex[:12]
        tools_desc = self._build_tools_description()

        # Langfuse trace
        lf = None
        span = None
        try:
            from app.agent.observability import get_langfuse
            lf = get_langfuse()
            if lf:
                span = lf.span(name="planning", input={"query": query, "plan_id": plan_id})
        except Exception:
            pass

        AGENT_PLANNING_TOTAL.inc()
        planning_start = time.time()

        # ── Structured planning: rule-based task decomposition ──
        # Experiments 001-003 proved the LLM-based Planner will not decompose
        # tasks (avg_steps ≈ 1).  When structured planning is enabled, the
        # system classifies the query and builds a template plan directly.
        structured_plan = self._try_structured_planning(plan_id, query)
        if structured_plan:
            self._validate_dag(structured_plan)
            yield AgentEvent(type="plan_start", plan_id=plan_id, content="分析任务类型，制定执行计划...")
            for step in structured_plan.steps:
                yield AgentEvent(type="plan_step", plan_id=plan_id, plan_step_id=step.id,
                                 content=step.description, dependencies=step.dependencies,
                                 tool_hint=step.tool_hint or "", plan_progress=0.0)
                await asyncio.sleep(0.005)
            yield AgentEvent(type="plan_complete", plan_id=plan_id,
                             content=f"计划已生成: {structured_plan.total_steps} 个步骤", plan_progress=0.0)
            if ctx:
                ctx.record_decision("plan", "structured", f"Generated {len(structured_plan.steps)} steps")
            AGENT_PLANNING_LATENCY.observe(time.time() - planning_start)
            return

        # Determine planning granularity
        mode = getattr(self.config, "planning_mode", "normal")
        if mode == "coarse":
            max_steps = 1
            granularity_instruction = "\n\n**重要**: 请一步完成，不要拆分步骤。直接将用户需求作为一个步骤输出。"
        elif mode == "fine":
            max_steps = 12
            granularity_instruction = (
                "\n\n**重要**: 请尽可能细粒度地拆分任务。每个独立子目标作为一个步骤，"
                "宁可多步也不要合并。如果多个步骤可以并行执行，请设置相同的 parallel_group。"
            )
        else:
            max_steps = self.config.max_plan_steps
            granularity_instruction = ""

        system_prompt = PLANNING_SYSTEM_PROMPT.format(
            tools_description=tools_desc,
            max_steps=max_steps,
        )
        if granularity_instruction:
            system_prompt += granularity_instruction

        if memory_context:
            system_prompt += f"\n\n{memory_context}"

        # ── Experience Memory (self-improving via failure learning) ──
        if self.config.enable_experience:
            try:
                from app.agent.experience import get_experience_store
                store = get_experience_store()
                exp_context = await store.format_for_planner(query, top_k=3)
                if exp_context:
                    system_prompt += f"\n\n{exp_context}"
            except Exception:
                pass

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]
        if history:
            for msg in history[-4:]:  # last 4 history messages for context
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if content and role in ("user", "assistant"):
                    messages.append({"role": role, "content": content[:500]})
        messages.append({"role": "user", "content": query})

        # Yield plan_start event
        yield AgentEvent(
            type="plan_start",
            plan_id=plan_id,
            content="正在分析任务并制定执行计划...",
        )

        try:
            # Real streaming: tokens flow to frontend as they arrive from the LLM
            stream = await self.client.chat.completions.create(
                model=self.config.get_deep_model(),
                messages=messages,
                temperature=0.1,
                max_tokens=self.config.plan_max_tokens,
                timeout=30.0,
                stream=True,
            )

            full_content = ""
            fence_start = -1  # index of opening "```" in full_content, -1 = not found

            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if not delta or not delta.content:
                    continue

                token = delta.content
                full_content += token

                # Track code-fence boundaries in the accumulated content.
                # Before the opening fence: stream tokens in real time.
                # After the opening fence: accumulate silently (JSON / structured data).
                if fence_start < 0:
                    idx = full_content.find("```")
                    if idx >= 0:
                        fence_start = idx
                        # Emit any content that arrived in the same chunk before the fence
                        # (earlier chunks were already yielded token by token)
                    else:
                        # Yield every token as a thinking event in real time
                        yield AgentEvent(
                            type="thinking",
                            content=token,
                            thinking_type="planning",
                            plan_id=plan_id,
                        )

            # Parse the complete response
            plan_json = self._extract_json(full_content)
            plan = self._parse_plan(plan_id, plan_json)
            if not plan or not plan.steps:
                # Fallback: single-step plan
                plan = Plan(
                    id=plan_id,
                    goal=query[:80],
                    reasoning="任务简单，直接执行。",
                    steps=[
                        PlanStep(
                            id="s1",
                            description=query,
                            tool_hint=None,
                        )
                    ],
                )

            # Validate DAG — ensure dependencies reference valid step IDs
            self._validate_dag(plan)

            if ctx:
                ctx.record_decision("plan", "complete", f"Generated plan with {len(plan.steps)} steps")

            # Yield plan steps
            for step in plan.steps:
                yield AgentEvent(
                    type="plan_step",
                    plan_id=plan_id,
                    plan_step_id=step.id,
                    content=step.description,
                    dependencies=step.dependencies,
                    tool_hint=step.tool_hint or "",
                    plan_progress=0.0,
                )
                await asyncio.sleep(0.005)

            # Yield plan_complete
            yield AgentEvent(
                type="plan_complete",
                plan_id=plan_id,
                content=f"计划已生成: {plan.total_steps} 个步骤",
                plan_progress=0.0,
            )

            # Store plan in Redis for persistence
            await self._save_plan(plan)

            AGENT_PLANNING_LATENCY.observe(time.time() - planning_start)

            if span:
                span.end(output=f"planned {len(plan.steps)} steps")

            # Plan is yielded as events; caller builds from events (Python < 3.12 compat)
            return

        except Exception as e:
            logger.error(f"Planning failed: {e}", exc_info=True)
            if span:
                span.end(output=f"error: {e}", level="ERROR")
            # Fallback: single-step plan on error
            plan = Plan(
                id=plan_id,
                goal=query[:80],
                reasoning="计划生成失败，直接执行。",
                steps=[PlanStep(id="s1", description=query)],
            )
            yield AgentEvent(
                type="plan_complete",
                plan_id=plan_id,
                content="使用默认计划（单步执行）",
                plan_progress=0.0,
            )
            return

    # ═══════════════════════════════════════════════════════════════════
    # Structured planning — rule-based task decomposition
    # ═══════════════════════════════════════════════════════════════════
    # Experiments 001-003 proved the LLM-based Planner will not decompose
    # tasks (avg_steps ≈ 1).  When structured planning is enabled, the
    # system classifies the query and builds a template plan directly.

    # ── Task classification keywords ──
    # These are grouped by priority: comparison > analysis > research.
    # A query is classified into the first matching type.

    # Comparison: must have BOTH a comparison word AND multi-entity indicator
    _CMP_WORDS = ["对比", "比较", "compare", "vs", "versus", "差异", "区别",
                   "不同之处", "异同", "孰优"]
    _CMP_ENTITIES = ["和", "与", "、", "两家", "三家", "多个", "分别",
                      "双方", "各方"]

    # Analysis: typically uses a named framework
    _ANALYSIS_WORDS = ["swot", "pest", "杜邦", "框架分析", "财务健康",
                        "健康评估", "风险评估", "合同风险"]

    # Research: broader — tasks that need gather → extract → synthesize
    _RESEARCH_WORDS = ["总结", "综述", "概述", "概括", "归纳", "研究", "调研",
                        "趋势", "发展", "行业", "市场", "报告", "分析报告",
                        "预测", "前景", "竞争格局", "市场份额",
                        "政策", "变化", "影响", "融资", "新闻",
                        "消费者", "规模", "竞争"]

    def _classify_task_type(self, query: str) -> str | None:
        """Classify query into known task types for template-based planning.

        Order matters: comparison has highest priority (strictest match),
        then analysis, then research.  Queries that match none fall through
        to the LLM-based planner (simple / single-step tasks).
        """
        q = query.lower().strip()

        # 1. Comparison — needs comparison word + multi-entity signal
        has_cmp = any(kw in q for kw in self._CMP_WORDS)
        has_multi = any(kw in q for kw in self._CMP_ENTITIES)
        if has_cmp and has_multi:
            return "comparison"
        if has_cmp and any(kw in q for kw in ["对比", "比较"]):
            # Loose match: "对比..." alone implies multi-entity
            return "comparison"

        # 2. Analysis — named framework
        if any(kw in q for kw in self._ANALYSIS_WORDS):
            return "analysis"

        # 3. Research — gather / summarize / investigate
        if any(kw in q for kw in self._RESEARCH_WORDS):
            return "research"

        return None

    def _try_structured_planning(self, plan_id: str, query: str) -> Plan | None:
        """Attempt rule-based plan generation for known task types.

        This is now the PRIMARY planning path.  The LLM-based planner is only
        reached as fallback for queries that match no known type.
        """
        task_type = self._classify_task_type(query)
        if task_type == "comparison":
            return self._build_comparison_plan(plan_id, query)
        if task_type == "analysis":
            return self._build_analysis_plan(plan_id, query)
        if task_type == "research":
            return self._build_research_plan(plan_id, query)
        return None

    # ── Template: comparison ──────────────────────────────────────────
    #  Research Entity A (parallel)    \
    #  Research Entity B (parallel)     →  Compare & Synthesize
    #  Research Entity C (parallel)    /
    #  4-6 steps depending on entity count

    def _build_comparison_plan(self, plan_id: str, query: str) -> Plan:
        """Template: parallel entity research → compare → synthesize."""
        import re as _re
        # Extract named entities: split by 和/与/、, then strip known prefixes
        parts = _re.split(r'\s*(?:和|与|、|vs\.?|versus)\s*', query)
        strip_words = (self._CMP_WORDS + self._CMP_ENTITIES
                       + ["搜索", "找", "查", "search", "find", "分析",
                          "两份", "三份", "两家", "三家", "同行业",
                          "的", "财务表现", "相关信息", "信息"])
        entities = []
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # Strip known keywords from start and end (not whole-segment skip)
            for kw in strip_words:
                kw_lower = kw.lower()
                while p.lower().startswith(kw_lower):
                    p = p[len(kw):].strip()
                while p.lower().endswith(kw_lower):
                    p = p[:-len(kw)].strip()
            if p and len(p) >= 2 and p not in entities:  # min 2 chars to be a valid entity
                entities.append(p)
        # Remove duplicates while preserving order
        seen = set()
        entities = [e for e in entities if not (e in seen or seen.add(e))]
        entities = entities[:5]

        # If no specific entities extracted, use generic descriptions
        if len(entities) < 2:
            entity_keywords = ["相关内容"]
            if "公司" in query or "供应商" in query or "企业" in query:
                entity_keywords = ["公司A", "公司B"]
            elif "行业" in query or "市场" in query:
                entity_keywords = ["行业A", "行业B"]
            elif "合同" in query:
                entity_keywords = ["合同A", "合同B"]
            elif "年报" in query or "财报" in query or "报告" in query:
                entity_keywords = ["报告A", "报告B"]
            else:
                entity_keywords = ["实体A", "实体B"]
            # Add a third slot for queries that hint at 3+
            if any(kw in query for kw in ["三家", "三份", "多个"]):
                entity_keywords.append("实体C")
            entities = entity_keywords

        steps: list[PlanStep] = []
        for i, entity in enumerate(entities):
            steps.append(PlanStep(
                id=f"s{i + 1}",
                description=f"研究{entity}的相关信息，提取关键指标",
                dependencies=[],
                tool_hint="search_knowledge_base",
                parallel_group=1,
                risk_level="low", retry_strategy="auto_retry",
            ))

        # Compare step
        all_search = [s.id for s in steps]
        steps.append(PlanStep(
            id=f"s{len(entities) + 1}",
            description=f"对比各实体的关键信息，找出差异和共性: {query[:50]}",
            dependencies=all_search,
            tool_hint="cross_document_analysis",
            risk_level="low", retry_strategy="auto_retry",
        ))

        return Plan(
            id=plan_id, goal=query[:80],
            reasoning=f"对比任务: {len(entities)} 个实体并行搜索 → 综合对比",
            steps=steps)

    # ── Template: analysis ────────────────────────────────────────────
    #  Collect Data (parallel) → Apply Framework → Generate Conclusions
    #  3-4 steps

    def _build_analysis_plan(self, plan_id: str, query: str) -> Plan:
        """Template: collect data → apply framework → conclusions."""
        q_lower = query.lower()
        framework_name = "分析"
        if "swot" in q_lower:
            framework_name = "SWOT"
        elif "pest" in q_lower:
            framework_name = "PEST"
        elif "杜邦" in q_lower:
            framework_name = "杜邦"

        steps = [
            PlanStep(id="s1",
                     description=f"搜索相关信息: {query[:50]}",
                     dependencies=[],
                     tool_hint="search_knowledge_base",
                     parallel_group=1,
                     risk_level="low", retry_strategy="auto_retry"),
            PlanStep(id="s2",
                     description=f"用{framework_name}框架进行分析: {query[:50]}",
                     dependencies=["s1"],
                     tool_hint="extract_insights",
                     risk_level="low", retry_strategy="auto_retry"),
            PlanStep(id="s3",
                     description=f"生成{framework_name}分析结论: {query[:50]}",
                     dependencies=["s2"],
                     tool_hint="generate_report",
                     risk_level="low", retry_strategy="auto_retry"),
        ]
        return Plan(
            id=plan_id, goal=query[:80],
            reasoning=f"分析任务: 搜索 → {framework_name}分析 → 生成结论",
            steps=steps)

    # ── Template: research ────────────────────────────────────────────
    #  Gather Sources → Extract Facts → Synthesize
    #  3-4 steps

    def _build_research_plan(self, plan_id: str, query: str) -> Plan:
        """Template: gather sources → extract → synthesize."""
        steps = [
            PlanStep(id="s1",
                     description=f"搜集相关资料: {query[:50]}",
                     dependencies=[],
                     tool_hint="search_knowledge_base",
                     parallel_group=1,
                     risk_level="low", retry_strategy="auto_retry"),
            PlanStep(id="s2",
                     description=f"提取关键事实和数据: {query[:50]}",
                     dependencies=["s1"],
                     tool_hint="extract_insights",
                     risk_level="low", retry_strategy="auto_retry"),
            PlanStep(id="s3",
                     description=f"综合归纳研究结果: {query[:50]}",
                     dependencies=["s2"],
                     tool_hint="summarize_document",
                     risk_level="low", retry_strategy="auto_retry"),
        ]

        # For queries that explicitly mention "对比趋势" or "变化",
        # add a trend-analysis step between extract and synthesize
        if any(kw in query.lower() for kw in ["趋势", "变化", "对比", "多年", "三年"]):
            steps.insert(2, PlanStep(
                id="s2b",
                description=f"分析跨期趋势和变化: {query[:50]}",
                dependencies=["s2"],
                tool_hint="cross_document_analysis",
                risk_level="low", retry_strategy="auto_retry",
            ))
            # Re-wire dependency: trend → synthesize
            steps[-1].dependencies = ["s2b"]

        return Plan(
            id=plan_id, goal=query[:80],
            reasoning=f"研究任务: 搜集 → 提取 → 归纳",
            steps=steps)

    def _build_tools_description(self) -> str:
        """Build a semantic-aware tool list for the planning prompt.

        Groups tools by category and annotates each with its semantic
        capabilities so the planner can make informed decisions about
        parallelization, retry strategy, and risk level.
        """
        tools = tool_registry.list_tools(self.config.tool_tags)
        if not tools:
            return "(无可用工具)"

        # Filter disabled tools
        active = [t for t in tools if t.name not in (self.config.disabled_tools or [])]
        if not active:
            return "(无可用工具)"

        # Group by category
        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for t in active:
            cat = t.category or "general"
            groups[cat].append(t)

        lines = []
        for cat in sorted(groups):
            lines.append(f"\n[{cat}]")
            for t in sorted(groups[cat], key=lambda x: x.name):
                sem = t.semantic
                sem_tags = []
                if sem.get("type") == "read_only":
                    sem_tags.append("🔍只读")
                elif sem.get("type") == "mutating":
                    sem_tags.append("✏️修改")
                elif sem.get("type") == "long_running":
                    sem_tags.append("⏱长任务")
                if sem.get("idempotent"):
                    sem_tags.append("幂等")
                if sem.get("retry_safe"):
                    sem_tags.append("可重试")
                tag_str = f" ({', '.join(sem_tags)})" if sem_tags else ""

                desc = t.description.split(".")[0]
                lines.append(f"  - **{t.name}**{tag_str}: {desc}")

        return "\n".join(lines)

    def _extract_json(self, text: str) -> dict[str, Any]:
        """Extract JSON from LLM response, handling markdown code fences."""
        # Try to find JSON inside ```json ... ``` blocks
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try raw JSON
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass

        # Try to find { ... } spanning multiple lines
        try:
            brace_match = re.search(r'\{[\s\S]*\}', text)
            if brace_match:
                return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

        return {}

    def _parse_plan(self, plan_id: str, data: dict[str, Any]) -> Plan | None:
        """Parse plan JSON into a Plan object, including semantic fields."""
        goal = data.get("goal", "")
        reasoning = data.get("reasoning", "")
        steps_data = data.get("steps", [])

        if not isinstance(steps_data, list) or not steps_data:
            return None

        steps = []
        step_ids = set()
        for i, s in enumerate(steps_data):
            if not isinstance(s, dict):
                continue
            sid = s.get("id", f"s{i + 1}")
            # Ensure unique step IDs
            while sid in step_ids:
                sid = f"{sid}_{i}"
            step_ids.add(sid)

            # Parse risk_level with validation
            risk = s.get("risk_level", "low")
            if risk not in ("low", "medium", "high"):
                risk = "low"

            # Parse retry_strategy with validation
            retry = s.get("retry_strategy", "auto_retry")
            if retry not in ("auto_retry", "ask_user", "skip"):
                retry = "auto_retry"

            steps.append(PlanStep(
                id=sid,
                description=s.get("description", f"步骤 {i + 1}"),
                dependencies=s.get("dependencies", []) or [],
                tool_hint=s.get("tool_hint") or None,
                # v1 semantic-aware fields
                parallel_group=s.get("parallel_group"),
                risk_level=risk,
                retry_strategy=retry,
                fallback_tool=s.get("fallback_tool") or None,
            ))

        return Plan(
            id=plan_id,
            goal=goal or "执行任务",
            reasoning=reasoning,
            steps=steps,
        )

    def _validate_dag(self, plan: Plan) -> None:
        """Validate that the plan DAG has no cycles and valid dependency references."""
        step_ids = {s.id for s in plan.steps}

        for step in plan.steps:
            # Filter out invalid dependency references
            step.dependencies = [d for d in step.dependencies if d in step_ids]

        # Simple cycle detection via topological sort (Kahn's algorithm)
        in_degree = {s.id: len(s.dependencies) for s in plan.steps}
        adj = {s.id: [] for s in plan.steps}
        for step in plan.steps:
            for dep in step.dependencies:
                if dep in adj:
                    adj[dep].append(step.id)

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        sorted_count = 0

        while queue:
            node = queue.pop(0)
            sorted_count += 1
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if sorted_count != len(plan.steps):
            logger.warning(f"Plan {plan.id} has cycles! Removing all dependencies.")
            for step in plan.steps:
                step.dependencies = []

    async def _save_plan(self, plan: Plan) -> None:
        """Persist plan to Redis for session recovery."""
        try:
            from app.core.redis import redis_client
            if not redis_client:
                return
            key = f"agent:plan:{plan.id}"
            data = {
                "id": plan.id,
                "goal": plan.goal,
                "reasoning": plan.reasoning,
                "steps": [
                    {
                        "id": s.id,
                        "description": s.description,
                        "dependencies": s.dependencies,
                        "tool_hint": s.tool_hint,
                        "status": s.status,
                        # v1 semantic fields
                        "parallel_group": s.parallel_group,
                        "risk_level": s.risk_level,
                        "retry_strategy": s.retry_strategy,
                        "fallback_tool": s.fallback_tool,
                    }
                    for s in plan.steps
                ],
                "completed_steps": plan.completed_steps,
                "failed_steps": plan.failed_steps,
            }
            await redis_client.setex(key, 3600, json.dumps(data, ensure_ascii=False))
        except Exception:
            logger.debug("Failed to save plan to Redis", exc_info=True)

    @staticmethod
    async def load_plan(plan_id: str) -> Plan | None:
        """Load a plan from Redis."""
        try:
            from app.core.redis import redis_client
            if not redis_client:
                return None
            key = f"agent:plan:{plan_id}"
            raw = await redis_client.get(key)
            if not raw:
                return None
            data = json.loads(raw)
            steps = [
                PlanStep(
                    id=s["id"],
                    description=s["description"],
                    dependencies=s.get("dependencies", []),
                    tool_hint=s.get("tool_hint"),
                    status=s.get("status", "pending"),
                    # v1 semantic fields (with safe defaults for old plans)
                    parallel_group=s.get("parallel_group"),
                    risk_level=s.get("risk_level", "low"),
                    retry_strategy=s.get("retry_strategy", "auto_retry"),
                    fallback_tool=s.get("fallback_tool"),
                )
                for s in data["steps"]
            ]
            return Plan(
                id=data["id"],
                goal=data["goal"],
                reasoning=data.get("reasoning", ""),
                steps=steps,
                completed_steps=data.get("completed_steps", 0),
                failed_steps=data.get("failed_steps", 0),
            )
        except Exception:
            return None
