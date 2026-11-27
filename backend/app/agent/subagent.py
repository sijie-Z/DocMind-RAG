"""Subagent delegation — spawn child agents for complex subtasks.

A parent agent can delegate a subtask to a child agent with its own
isolated context, restricted toolset, and iteration budget. The child
returns a summary to the parent.

Uses PERAgentLoop with planning and reflection disabled for efficiency.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator

from openai import AsyncOpenAI

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.loop import PERAgentLoop

logger = logging.getLogger(__name__)

SUBAGENT_ALLOWED_TAGS = ["search", "analysis"]


async def _record_subagent_usage(
    child: PERAgentLoop,
    *,
    organization_id: int,
    user_id: int | None,
) -> None:
    """把子代理本轮真实 token 用量落 `token_usage_records`（source=subagent）。

    子代理有独立的 PERAgentLoop / ExecutionContext，父代理的
    ``_record_agent_cost`` 只累计父循环自己的 ``usage_by_model``——两边不重叠，
    因此这里单独记账不会重复。无 viewer（内部系统任务）没有可归属的用户，
    如实跳过（不写 user_id=0 的假记录）。
    """
    if not user_id:
        return
    ctx = getattr(child, "last_execution_context", None)
    usage = getattr(ctx, "usage_by_model", None)
    if not isinstance(usage, dict) or not usage:
        return
    try:
        from app.api.v1.endpoints.token_usage import record_token_usage
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            for model, counts in usage.items():
                if not isinstance(model, str) or not model:
                    continue
                try:
                    tokens_in, tokens_out = counts
                except (TypeError, ValueError):
                    continue
                if type(tokens_in) is not int or type(tokens_out) is not int:
                    continue
                if tokens_in <= 0 and tokens_out <= 0:
                    continue
                await record_token_usage(
                    db=db,
                    user_id=user_id,
                    organization_id=organization_id,
                    model=model,
                    source="subagent",
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    feature="subagent",
                )
    except Exception:  # noqa: BLE001 — 记账是旁路，绝不反噬子任务结果
        # CodeQL：完全固定文案、无参数。
        logger.warning("子代理 token 记账失败（不影响结果）")


async def delegate_task(
    client: AsyncOpenAI,
    task: str,
    parent_context: str = "",
    model: str = "deepseek-v4-flash",
    max_iterations: int = 5,
    organization_id: int | None = None,
    user_id: int | None = None,
    internal_system_task: bool = False,
    tool_whitelist: list[str] | None = None,
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
        tool_whitelist: 父运行的组织工具白名单（批次十四 C4）。子代理除
            ``SUBAGENT_ALLOWED_TAGS`` 外再叠加该名单（registry 交集过滤），
            结果永不比父代理更宽；None = 父未限制。
    """
    # A2 P0（fail-closed）：子代理继承父代理的组织边界；缺失即拒绝派发。
    if organization_id is None:
        raise ValueError("delegate_task 缺少 organization_id，拒绝在无组织上下文下派发子任务")
    # issue #511 P0-1: the child inherits the viewer. A missing user_id would
    # silently become PERAgentLoop's legacy 0 and bypass private-doc ACLs.
    if not user_id and not internal_system_task:
        raise ValueError("delegate_task 缺少 user_id，拒绝无 viewer 身份派发子任务")
    if internal_system_task and user_id:
        raise ValueError("internal_system_task=True 不得同时携带真实 user_id")
    child_config = AgentConfig(
        model=model,
        max_iterations=max_iterations,
        max_tool_calls_per_turn=3,
        tool_tags=SUBAGENT_ALLOWED_TAGS,
        # 批次十四 C4：父白名单继续约束子代理（与 tool_tags 取交集）；
        # None 保持旧行为（仅 tag 限制），绝不放宽父代理已有的名单。
        tool_whitelist=list(tool_whitelist) if tool_whitelist is not None else None,
        # 技能（issue #200 评审 B1）：子代理上下文看不到技能工具（schema 过滤 +
        # 执行层白名单拒绝），因此技能索引注入 / 读侧提示 / 写侧学习对子代理全部关闭，
        # 避免提示词承诺一个子代理必被拒绝的工具。
        enable_skills=False,
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
        user_id=user_id,
        internal_system_task=internal_system_task,
    )

    try:
        async for event in child.run(task):
            yield event
    finally:
        # 子代理 token 归因：子循环收尾后写一行（每个 model 一条），
        # 与父代理的 _record_agent_cost 互不重叠；子任务失败也按已发生用量记账。
        await _record_subagent_usage(child, organization_id=organization_id, user_id=user_id)


async def run_parallel_subtasks(
    client: AsyncOpenAI,
    tasks: list[str],
    model: str = "deepseek-v4-flash",
    organization_id: int | None = None,
    user_id: int | None = None,
    internal_system_task: bool = False,
    parent_context: str = "",
    per_task_timeout: float | None = 300.0,
    tool_whitelist: list[str] | None = None,
) -> list[str]:
    """Execute multiple subtasks in parallel and collect results.

    返回与 `tasks` **同序**的结论列表。单个子任务的失败/超时**不外溢**：
    以 `[Error: …]` / `[子任务超时…]` 标注在该条结果里（issue #199 验收：
    错误要能传播进结果、并发要有上限、单任务要有墙钟上限）。

    `per_task_timeout`：单个子任务墙钟上限（秒）；None 表示不设。
    """
    # A2 P0（fail-closed）：并行子任务同样必须带组织上下文。
    if organization_id is None:
        raise ValueError(
            "run_parallel_subtasks 缺少 organization_id，拒绝在无组织上下文下派发子任务"
        )
    if not user_id and not internal_system_task:
        raise ValueError("run_parallel_subtasks 缺少 user_id，拒绝无 viewer 身份派发子任务")

    async def _collect_events(task: str) -> list[str]:
        parts: list[str] = []
        delegate_kwargs: dict[str, object] = {
            "client": client,
            "task": task,
            "parent_context": parent_context,
            "model": model,
            "organization_id": organization_id,
            "user_id": user_id,
            "internal_system_task": internal_system_task,
        }
        # 兼容既有 fake/旧签名：父未限制（None）时不额外传参。
        if tool_whitelist is not None:
            delegate_kwargs["tool_whitelist"] = tool_whitelist
        async for event in delegate_task(**delegate_kwargs):  # type: ignore[arg-type]
            if event.type == "chunk":
                parts.append(event.content)
            elif event.type == "error":
                parts.append(f"[Error: {event.content}]")
        return parts

    async def _run_one(task: str) -> str:
        try:
            if per_task_timeout:
                parts = await asyncio.wait_for(_collect_events(task), timeout=per_task_timeout)
            else:
                parts = await _collect_events(task)
        except TimeoutError:
            return f"[子任务超时（>{per_task_timeout:.0f}s）]"
        except Exception as e:
            logger.warning("子任务执行失败（不拖垮整批）: %s | %s", type(e).__name__, e)
            return f"[Error: {type(e).__name__}: {e}]"
        return "".join(parts) or "No result."

    return await _gather_with_concurrency(
        [_run_one(task) for task in tasks],
        max_concurrency=3,
    )


async def _gather_with_concurrency(coros, max_concurrency: int = 3):
    """Run coroutines with limited concurrency."""
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _wrap(coro):
        async with semaphore:
            return await coro

    return await asyncio.gather(*[_wrap(c) for c in coros])
