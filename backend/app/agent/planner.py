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

## 规划原则
1. 每个步骤必须明确、可执行、可验证
2. 步骤之间有依赖关系时，必须声明 dependencies
3. 如果某步骤可能用到工具，在 tool_hint 中建议工具名称
4. 总步骤数不超过 {max_steps} 个
5. 如果请求简单（无需多步），只生成 1-2 个步骤

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
            "tool_hint": "tool_name 或 null"
        }}
    ]
}}
```
"""


@dataclass
class PlanStep:
    """A single step in an execution plan."""
    id: str
    description: str
    dependencies: list[str] = field(default_factory=list)
    tool_hint: str | None = None
    status: str = "pending"  # pending, ready, running, completed, failed, skipped
    result: str | None = None
    error_context: str | None = None
    retry_count: int = 0


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
    ) -> AsyncGenerator[AgentEvent, Plan | None]:
        """Generate a plan, yielding thinking events as reasoning streams.

        Yields: thinking events during plan generation
        Sends back: Plan object when complete (via generator return)
        """
        plan_id = uuid.uuid4().hex[:12]
        tools_desc = self._build_tools_description()

        AGENT_PLANNING_TOTAL.inc()
        planning_start = time.time()

        system_prompt = PLANNING_SYSTEM_PROMPT.format(
            tools_description=tools_desc,
            max_steps=self.config.max_plan_steps,
        )

        if memory_context:
            system_prompt += f"\n\n{memory_context}"

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
                model=self.config.model,
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

            # Plan is yielded as events; caller builds from events (Python < 3.12 compat)
            return

        except Exception as e:
            logger.error(f"Planning failed: {e}", exc_info=True)
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

    def _build_tools_description(self) -> str:
        """Build a concise tool list for the planning prompt."""
        tools = tool_registry.list_tools(self.config.tool_tags)
        if not tools:
            return "(无可用工具)"
        lines = []
        for t in tools:
            if t.name in self.config.disabled_tools:
                continue
            desc = t.description.split(".")[0]  # first sentence only
            lines.append(f"- **{t.name}**: {desc}")
        return "\n".join(lines) if lines else "(无可用工具)"

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
        """Parse plan JSON into a Plan object."""
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

            steps.append(PlanStep(
                id=sid,
                description=s.get("description", f"步骤 {i + 1}"),
                dependencies=s.get("dependencies", []) or [],
                tool_hint=s.get("tool_hint") or None,
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
