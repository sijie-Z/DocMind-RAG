"""Agent service — high-level interface for the PER agent system.

Wires together the PER agent loop, tools, memory, and context engine
into a single service for the API layer.

Supports:
- Agent chat with full PER flow (SSE streaming)
- Hybrid RAG + Agent mode (retrieve first, then agent)
- Session persistence (config + plan + memory state)
"""

import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI

from app.agent.events import AgentEvent
from app.agent.config import AgentConfig
from app.agent.loop import PERAgentLoop
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.registry import tool_registry

logger = logging.getLogger(__name__)

# Force import of all tool modules to trigger registration
import app.agent.core_tools  # noqa: F401 — original 11 tools
import app.agent.tools       # noqa: F401 — 14 new tools package


class AgentService:
    """High-level agent interface with session management."""

    def __init__(self, openai_client: Optional[AsyncOpenAI] = None):
        self._client = openai_client

    @property
    def client(self) -> Optional[AsyncOpenAI]:
        if self._client is None:
            from app.dependencies import get_rag_pipeline
            pipeline = get_rag_pipeline()
            self._client = pipeline.openai_client
        return self._client

    async def chat(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
        organization_id: int = 1,
        user_id: int = 0,
        session_id: Optional[str] = None,
        config: Optional[AgentConfig] = None,
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
        yield AgentEvent(
            type="thinking",
            content=f"可用工具: {tool_count} 个",
            thinking_type="reasoning",
        )

        # Run the PER loop
        async for event in agent.run(query, history=history):
            yield event

        # Persist config for session recovery
        if session_id:
            await agent_config.save_to_redis(session_id)

    async def search_and_chat(
        self,
        query: str,
        history: Optional[List[Dict[str, str]]] = None,
        organization_id: int = 1,
        user_id: int = 0,
        top_k: int = 5,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Hybrid mode: retrieve context first, then run PER agent.

        Combines traditional RAG retrieval with the full PER agent.
        """
        if not self.client:
            yield {"type": "error", "content": "LLM 未配置。"}
            return

        # Step 1: Retrieve context via RAG
        from app.dependencies import get_rag_pipeline
        pipeline = get_rag_pipeline()
        context_docs = await pipeline.search_knowledge_base(
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
    ) -> Optional[Dict[str, Any]]:
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
