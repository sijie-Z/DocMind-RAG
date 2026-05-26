"""AgentMemoryBridge — wires the existing AgentMemorySystem into the PER agent loop.

The memory system (memory_service.py) supports ShortTerm, LongTerm, Working,
and Reflective memory with embedding-based semantic search and Redis persistence.
This bridge provides the agent with:
- Pre-query context retrieval (injected into system prompt)
- Per-step result storage in WorkingMemory
- Automatic interaction storage
- Experience learning from tool success/failure
- Semantic recall via the RAG pipeline's embedding provider
"""

import logging
from typing import Any

from app.core.prometheus import AGENT_MEMORY_RECALLS
from app.services.memory_service import (
    AgentMemorySystem,
    MemoryItem,
    get_memory_system,
)

logger = logging.getLogger(__name__)


class AgentMemoryBridge:
    """Bridges AgentMemorySystem into the PER agent loop.

    Scoped by (organization_id, user_id, agent_id) so memories are
    isolated per user and per agent persona.

    Usage:
        bridge = AgentMemoryBridge(
            agent_id="default",
            organization_id=org_id,
            user_id=user_id,
        )
        context = await bridge.get_context_for_query("分析财报")
        await bridge.record_step(plan_step)
        await bridge.record_interaction(query, answer)
    """

    def __init__(
        self,
        agent_id: str = "default",
        organization_id: int = 1,
        user_id: int = 0,
    ):
        # Namespace: one memory system per (org, user, agent)
        namespace = f"{organization_id}:{user_id}:{agent_id}"
        self.system: AgentMemorySystem = get_memory_system(namespace)
        self.agent_id = agent_id
        self.organization_id = organization_id
        self.user_id = user_id
        self._embedding_ready = False

    # ── Lifecycle ──

    async def ensure_loaded(self) -> None:
        """Load persisted memories from Redis if not already loaded."""
        await self.system._load_from_redis()

    async def ensure_embedding(self) -> None:
        """Wire up the embedding provider for semantic memory search."""
        if self._embedding_ready:
            return
        try:
            from app.dependencies import get_rag_pipeline
            pipeline = get_rag_pipeline()
            self.system.set_embedding_provider(pipeline.get_embedding)
            self._embedding_ready = True
            logger.debug(f"Embedding provider wired for agent '{self.agent_id}'")
        except Exception as e:
            logger.warning(f"Failed to wire embedding provider: {e}")

    # ── Context retrieval (Phase 0: Pre-query) ──

    async def get_context_for_query(self, query: str, top_k: int = 5) -> str:
        """Retrieve relevant memories for injection into the system prompt.

        Returns a formatted string like:
            "## 相关记忆\n1. 上次分析财报时发现毛利率下降...\n2. 用户偏好详细的中文回答..."
        """
        await self.ensure_loaded()
        await self.ensure_embedding()

        try:
            memories = await self.system.recall(query, top_k=top_k)
            if memories:
                AGENT_MEMORY_RECALLS.labels(result="hit").inc()
            else:
                AGENT_MEMORY_RECALLS.labels(result="miss").inc()
        except Exception as e:
            logger.warning(f"Memory recall failed: {e}")
            return ""

        if not memories:
            return ""

        parts = ["## 相关记忆"]
        for i, mem in enumerate(memories, 1):
            content = mem.get("content", mem.get("lesson", ""))
            if content:
                parts.append(f"{i}. {content}")
        return "\n".join(parts)

    # ── Working memory (Phase 2: Execution) ──

    def store_step_result(self, step_id: str, result: dict[str, Any]) -> None:
        """Store a completed plan step result in WorkingMemory."""
        self.system.working.set_result(step_id, result)

    def get_step_result(self, step_id: str) -> dict[str, Any] | None:
        """Retrieve a previous step result for dependent steps."""
        return self.system.working.get_result(step_id)

    def push_task(self, step_id: str, description: str) -> None:
        """Push a plan step onto the task stack."""
        self.system.working.push_task({"step_id": step_id, "description": description})

    def pop_task(self) -> dict[str, Any] | None:
        """Pop the completed task from the stack."""
        return self.system.working.pop_task()

    def set_variable(self, key: str, value: Any) -> None:
        """Set a variable in WorkingMemory for template resolution."""
        self.system.working.set_variable(key, value)

    def get_all_results(self) -> dict[str, Any]:
        """Get all intermediate results for reflection."""
        return dict(self.system.working.intermediate_results)

    # ── Experience recording (Phase 2 & 3) ──

    async def record_experience(
        self,
        success: bool,
        action: str,
        result: str,
        context: str = "",
    ) -> None:
        """Record tool execution outcome for future learning."""
        try:
            await self.system.store_experience(success, action, result, context)
        except Exception as e:
            logger.warning(f"Failed to record experience: {e}")

    async def record_insight(self, insight: str, context: dict | None = None) -> None:
        """Record a high-level insight from reflection."""
        try:
            self.system.reflective.add_insight(insight, context)
            await self.system._save_to_redis()
        except Exception as e:
            logger.warning(f"Failed to record insight: {e}")

    async def record_lesson(
        self, lesson: str, trigger: str | None = None, solution: str | None = None
    ) -> None:
        """Record a lesson learned from a failure."""
        try:
            self.system.reflective.add_lesson(lesson, trigger, solution)
            await self.system._save_to_redis()
        except Exception as e:
            logger.warning(f"Failed to record lesson: {e}")

    # ── Interaction storage (Phase 4: Finalize) ──

    async def record_interaction(self, user_input: str, assistant_response: str) -> None:
        """Store the completed interaction for future recall."""
        try:
            await self.system.store_interaction(user_input, assistant_response)
        except Exception as e:
            logger.warning(f"Failed to record interaction: {e}")

    # ── Memory export/import ──

    def export_state(self) -> dict[str, Any]:
        """Export working + short-term state for session persistence."""
        return {
            "working": self.system.working.to_dict(),
            "short_term": self.system.short_term.to_list(),
            "interaction_count": self.system.interaction_count,
        }

    def load_state(self, state: dict[str, Any]) -> None:
        """Restore working + short-term state from a saved session."""
        if "working" in state:
            w = state["working"]
            self.system.working.state = w.get("state", {})
            self.system.working.task_stack = w.get("task_stack", [])
            self.system.working.intermediate_results = w.get("intermediate_results", {})
            self.system.working.variables = w.get("variables", {})
        if "short_term" in state:
            self.system.short_term.clear()
            for item_data in state["short_term"]:
                item = MemoryItem(
                    content=item_data["content"],
                    memory_type=item_data.get("memory_type", "short_term"),
                    importance=item_data.get("importance", 0.5),
                    metadata=item_data.get("metadata"),
                    embedding=item_data.get("embedding"),
                    created_at=item_data.get("created_at"),
                    last_accessed=item_data.get("last_accessed"),
                    access_count=item_data.get("access_count", 0),
                    item_id=item_data.get("id"),
                )
                self.system.short_term.add(item)
        if "interaction_count" in state:
            self.system.interaction_count = state["interaction_count"]

    def reset_working(self) -> None:
        """Clear working memory for a new conversation turn."""
        self.system.working.clear()
