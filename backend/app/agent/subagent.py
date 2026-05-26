"""Subagent delegation — spawn child agents for complex subtasks.

A parent agent can delegate a subtask to a child agent with its own
isolated context, restricted toolset, and iteration budget. The child
returns a summary to the parent.

Uses PERAgentLoop with planning and reflection disabled for efficiency.
"""

import logging
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.loop import PERAgentLoop

logger = logging.getLogger(__name__)

SUBAGENT_ALLOWED_TAGS = ["search", "analysis"]


async def delegate_task(
    client: AsyncOpenAI,
    task: str,
    parent_context: str = "",
    model: str = "deepseek-v4-flash",
    max_iterations: int = 5,
    organization_id: int = 1,
) -> AsyncGenerator[AgentEvent, None]:
    """Delegate a subtask to a child PER agent.

    The child agent runs with:
    - Planning disabled (parent already planned)
    - Reflection disabled (parent will evaluate)
    - Restricted toolset (search + analysis only)

    Args:
        client: OpenAI client for LLM calls.
        task: The subtask description.
        parent_context: Context from the parent agent.
        model: Model to use.
        max_iterations: Maximum iterations.
        organization_id: Organization scope.
    """
    child_config = AgentConfig(
        model=model,
        max_iterations=max_iterations,
        max_tool_calls_per_turn=3,
        tool_tags=SUBAGENT_ALLOWED_TAGS,
        enable_planning=False,
        enable_reflection=False,
        enable_memory=False,
        enable_thinking=False,
        system_prompt_override=(
            f"你是 DocMind 子任务代理。你的任务是：{task}\n\n"
            f"## 父代理上下文\n{parent_context}\n\n"
            "请使用工具完成任务，然后给出简洁的结论。不要做多余的分析。"
        ),
    )

    child = PERAgentLoop(
        openai_client=client,
        config=child_config,
        organization_id=organization_id,
    )

    async for event in child.run(task):
        yield event


async def run_parallel_subtasks(
    client: AsyncOpenAI,
    tasks: list[str],
    model: str = "deepseek-v4-flash",
    organization_id: int = 1,
) -> list[str]:
    """Execute multiple subtasks in parallel and collect results.

    Returns a list of final answers from each subtask.
    """

    async def _run_one(task: str) -> str:
        answer_parts: list[str] = []
        async for event in delegate_task(
            client=client,
            task=task,
            model=model,
            organization_id=organization_id,
        ):
            if event.type == "chunk":
                answer_parts.append(event.content)
            elif event.type == "error":
                answer_parts.append(f"[Error: {event.content}]")
        return "".join(answer_parts) or "No result."

    return await _gather_with_concurrency(
        [_run_one(task) for task in tasks],
        max_concurrency=3,
    )


async def _gather_with_concurrency(coros, max_concurrency: int = 3):
    """Run coroutines with limited concurrency."""
    import asyncio
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _wrap(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*[_wrap(c) for c in coros])
