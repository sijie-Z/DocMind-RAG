"""Agent service — high-level interface for the PER agent system.

Wires together the PER agent loop, tools, memory, and context engine
into a single service for the API layer.

Supports:
- Agent chat with full PER flow (SSE streaming)
- Hybrid RAG + Agent mode (retrieve first, then agent)
- Session persistence (config + plan + memory state)
"""

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from openai import AsyncOpenAI

from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.loop import PERAgentLoop
from app.agent.memory_bridge import AgentMemoryBridge
from app.models.agent_task import TaskStatus

logger = logging.getLogger(__name__)

# Force import of all tool modules to trigger registration
import app.agent.core_tools  # noqa: F401 — original 11 tools
import app.agent.tools  # noqa: F401 — 14 new tools package


class AgentService:
    """High-level agent interface with session management."""

    def __init__(self, openai_client: AsyncOpenAI | None = None):
        self._client = openai_client

    @property
    def client(self) -> AsyncOpenAI | None:
        if self._client is None:
            from app.dependencies import get_rag_pipeline
            pipeline = get_rag_pipeline()
            self._client = pipeline.openai_client
        return self._client

    async def chat(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        organization_id: int = 1,
        user_id: int = 0,
        session_id: str | None = None,
        config: AgentConfig | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the PER agent loop and yield events.

        This is the main entry point for the agent chat API.
        All SSE event streaming goes through this method.

        Args:
            query: User's question or request.
            history: Previous conversation messages.
            organization_id: Multi-tenant organization scope.
            user_id: Current user ID.
            session_id: Optional session ID for config persistence.
            config: Per-request agent configuration overrides.
        """
        if not self.client:
            yield AgentEvent(type="error", content="LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。")
            return

        # Load or create config
        agent_config = config or AgentConfig()

        if session_id:
            saved_config = await AgentConfig.load_from_redis(session_id)
            if saved_config and not config:
                agent_config = saved_config

        # Apply session-scoped agent_id
        if session_id:
            agent_config.agent_id = f"session:{session_id}"

        # 任务行（issue #201 最小版）：一次 run 一行，checkpoint 的快照载体。
        # 建行失败不影响聊天（fail-open：整个 checkpoint 面静默缺席）。
        task_id: str | None = None
        if agent_config.enable_checkpoint:
            task_id = await checkpoint.create_task(
                user_id=user_id,
                organization_id=organization_id,
                query=query,
            )

        # Create the PER loop
        agent = PERAgentLoop(
            openai_client=self.client,
            config=agent_config,
            organization_id=organization_id,
            user_id=user_id,
        )

        # Emit available tool count in a metadata event
        tools = agent._get_tools()
        tool_count = len(tools) if tools else 0
        meta_event = AgentEvent(
            type="thinking",
            content=f"可用工具: {tool_count} 个",
            thinking_type="reasoning",
        )
        if task_id:
            meta_event.task_id = task_id
        yield meta_event

        # Run the PER loop（task_id 接通 checkpoint：T1–T4 触发点全部生效）
        try:
            async for event in agent.run(
                query, history=history, task_id=task_id, session_id=session_id,
            ):
                if task_id:
                    event.task_id = task_id
                yield event
        except GeneratorExit:
            # 断流（客户端 abort / 生成器被关闭）：尽力落一次快照，status 保持
            # RUNNING 以便 resume（用户"点停止"≠放弃任务，见设计报告 2.3）。
            await self._checkpoint_disconnect(task_id, agent)
            raise
        except asyncio.CancelledError:
            # 请求被取消（uvicorn 断连取消任务）：同断流语义。
            await self._checkpoint_disconnect(task_id, agent)
            raise
        except BaseException as exc:
            # run 内异常：快照 + 置 FAILED（FAILED→RUNNING 是既有重试边，可 resume）。
            await self._checkpoint_error(task_id, agent, exc)
            raise
        else:
            if task_id:
                await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED)

        # Persist config for session recovery
        if session_id:
            await agent_config.save_to_redis(session_id)

    async def resume_chat(
        self,
        task_id: str,
        user_id: int,
        *,
        config: AgentConfig | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """从 checkpoint 恢复一个未终态任务（issue #201 最小版）。

        语义（设计报告 2.3）：
        - 已完成步骤绝不重跑（executor 只挑 pending）；崩溃时在途的步骤重跑一次；
        - 快照里的 config 是 run 起点的规则快照，resume 不重读用户最新配置；
        - 快照缺失/损坏安全降级：从规划阶段干净重跑（没有任何步骤被越过）。
        - 并发/重复 resume 由 Redis 租约 + 状态机拒绝（TaskBusy/NotResumable，
          AppError 子类 → 端点层映射 404/409）。
        """
        if not self.client:
            yield AgentEvent(type="error", content="LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。")
            return

        task, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=user_id)
        if task.is_terminal:
            raise checkpoint.TaskNotResumableError(
                f"任务已处于终态 {task.status.value}，不可恢复",
                detail={"task_id": task_id, "state": task.status.value},
            )
        # 认领：Redis 租约 + RUNNING→FAILED→RUNNING（陈旧 RUNNING 接管）等状态流转。
        task = await checkpoint.begin_resume(task_id)

        if payload:
            query = (payload.get("ctx") or {}).get("query") or task.query
            agent_config = AgentConfig.from_dict(payload.get("config") or {})
            session_id = payload.get("session_id") or None
        else:
            # 快照缺失/损坏：降级为从规划阶段重跑（仍幂等）；规则取调用方传入的配置。
            logger.warning("任务 %s 无可用快照，resume 从规划阶段重新开始", task_id)
            query = task.query
            agent_config = config or AgentConfig()
            session_id = None

        agent = PERAgentLoop(
            openai_client=self.client,
            config=agent_config,
            organization_id=task.organization_id or 1,
            user_id=task.user_id,
        )
        try:
            async for event in agent.run(
                query, task_id=task_id, resume=payload, session_id=session_id,
            ):
                event.task_id = task_id
                yield event
        except GeneratorExit:
            await self._checkpoint_disconnect(task_id, agent)
            raise
        except asyncio.CancelledError:
            await self._checkpoint_disconnect(task_id, agent)
            raise
        except BaseException as exc:
            await self._checkpoint_error(task_id, agent, exc)
            raise
        else:
            await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED)

    # ── Checkpoint 兜底（断流 / 异常；二者都不得改变既有语义）──────────────

    async def _checkpoint_disconnect(self, task_id: str | None, agent: PERAgentLoop) -> None:
        """断流兜底（best-effort）：存一份"到此为止"的快照，不动任务状态。

        语义 = 与"进程被硬杀"一致（行停在 RUNNING，resume 从最近成功的
        checkpoint 继续）。await 在被关闭/取消的路径上可能被底层打断——允许
        它失败：最坏退化 = 恢复点停在上一个 T2，不影响正确性（设计报告 E1）。
        """
        if not task_id:
            return
        try:
            await asyncio.shield(checkpoint.save_from_live(
                task_id=task_id, live=agent.checkpoint_live, trigger="on_disconnect",
            ))
        except Exception as e:
            logger.warning("checkpoint on_disconnect failed (task=%s): %s", task_id, e)

    async def _checkpoint_error(
        self, task_id: str | None, agent: PERAgentLoop, exc: BaseException,
    ) -> None:
        """run 内异常：快照 + 标记 FAILED（写 last_error）；从不吞掉原异常。"""
        if not task_id:
            return
        try:
            await asyncio.shield(checkpoint.save_from_live(
                task_id=task_id, live=agent.checkpoint_live, trigger="on_error",
            ))
        except Exception as e:
            logger.warning("checkpoint on_error save failed (task=%s): %s", task_id, e)
        await checkpoint.mark_terminal(
            task_id,
            TaskStatus.FAILED,
            error=f"{type(exc).__name__}: {exc}",
            error_type=type(exc).__name__,
        )

    async def search_and_chat(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        organization_id: int = 1,
        user_id: int = 0,
        top_k: int = 5,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Hybrid mode: retrieve context first, then run PER agent.

        Combines traditional RAG retrieval with the full PER agent.
        """
        if not self.client:
            yield {"type": "error", "content": "LLM 未配置。"}
            return

        # Step 1: Retrieve context via RAG
        from app.dependencies import get_rag_pipeline
        pipeline = get_rag_pipeline()
        context_docs, _ = await pipeline.search_knowledge_base(
            query=query,
            organization_id=organization_id,
            top_k=top_k,
        )

        if context_docs:
            yield {
                "type": "context",
                "sources": [
                    {
                        "filename": d.get("filename", "Unknown"),
                        "score": d.get("score", 0),
                        "snippet": d.get("snippet", "")[:200],
                    }
                    for d in context_docs
                ],
            }

        # Step 2: Run PER agent with retrieved context
        config = AgentConfig(
            max_iterations=5,
            max_plan_steps=5,
        )

        agent = PERAgentLoop(
            openai_client=self.client,
            config=config,
            organization_id=organization_id,
            user_id=user_id,
        )

        async for event in agent.run(query, history=history, context_docs=context_docs):
            yield {
                "type": event.type,
                "content": event.content,
                "tool_name": event.tool_name,
                "plan_id": event.plan_id,
                "plan_step_id": event.plan_step_id,
                "plan_progress": event.plan_progress,
                "thinking_type": event.thinking_type,
                "reflection_result": event.reflection_result,
            }

    # ── Session management ──

    async def save_session_state(
        self,
        session_id: str,
        organization_id: int,
        user_id: int,
    ) -> None:
        """Save the full agent session state to Redis."""
        bridge = AgentMemoryBridge(
            agent_id=f"session:{session_id}",
            organization_id=organization_id,
            user_id=user_id,
        )
        state = bridge.export_state()
        try:
            from app.core.redis import redis_client
            if redis_client:
                key = f"agent:session_state:{session_id}"
                await redis_client.setex(
                    key, 86400 * 7,
                    json.dumps(state, ensure_ascii=False, default=str),
                )
                logger.info(f"Agent session state saved: {session_id}")
        except Exception as e:
            logger.warning(f"Failed to save session state: {e}")

    async def load_session_state(
        self,
        session_id: str,
        organization_id: int,
        user_id: int,
    ) -> dict[str, Any] | None:
        """Load agent session state from Redis."""
        try:
            from app.core.redis import redis_client
            if not redis_client:
                return None
            key = f"agent:session_state:{session_id}"
            raw = await redis_client.get(key)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"Failed to load session state: {e}")
        return None


# Singleton
agent_service = AgentService()
