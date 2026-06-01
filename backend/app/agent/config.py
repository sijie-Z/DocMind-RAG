"""Agent configuration — persistable, per-session settings.

AgentConfig controls the behavior of the PER agent loop. Instances
can be serialized to/from Redis so that session preferences survive
page reloads and server restarts.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Literal, Optional

logger = logging.getLogger(__name__)

Personality = Literal["precise", "creative", "balanced"]

DEFAULT_DISABLED_TOOLS: list[str] = ["execute_python", "execute_sql"]


@dataclass
class AgentConfig:
    """Full agent configuration for a single session or user."""

    # ── Model settings ──
    model: str = "deepseek-v4-flash"
    deep_think_model: str = ""  # 强模型（综合推理），为空则回退到 model
    quick_think_model: str = ""  # 快模型（信息收集），为空则回退到 model
    temperature: float = 0.1
    max_tokens: int = 4096

    # ── Loop limits ──
    max_plan_steps: int = 10
    max_iterations: int = 10
    max_tool_calls_per_turn: int = 5
    max_retries_per_step: int = 3

    # ── Phase toggles ──
    enable_planning: bool = True
    enable_reflection: bool = True
    enable_tools: bool = True
    enable_memory: bool = True
    enable_thinking: bool = True

    # ── Tool filtering ──
    tool_tags: list[str] | None = None
    disabled_tools: list[str] = field(default_factory=lambda: list(DEFAULT_DISABLED_TOOLS))

    # ── System prompt ──
    system_prompt_override: str | None = None

    # ── Personality ──
    personality: Personality = "balanced"

    # ── Memory ──
    agent_id: str = "default"

    # ── Limits ──
    timeout: float = 120.0
    max_context_tokens: int = 8000

    # ── Model selection ──
    def get_deep_model(self) -> str:
        """模型用于综合推理/决策（Reflector, Reviewer, 综合回答）"""
        return self.deep_think_model or self.model

    def get_quick_model(self) -> str:
        """模型用于信息收集/工具调用（Executor step, synthesis）"""
        return self.quick_think_model or self.model

    # ── Advanced ──
    thinking_max_tokens: int = 500
    plan_max_tokens: int = 800
    reflection_max_tokens: int = 400

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "deep_think_model": self.deep_think_model,
            "quick_think_model": self.quick_think_model,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "max_plan_steps": self.max_plan_steps,
            "max_iterations": self.max_iterations,
            "max_tool_calls_per_turn": self.max_tool_calls_per_turn,
            "max_retries_per_step": self.max_retries_per_step,
            "enable_planning": self.enable_planning,
            "enable_reflection": self.enable_reflection,
            "enable_tools": self.enable_tools,
            "enable_memory": self.enable_memory,
            "enable_thinking": self.enable_thinking,
            "tool_tags": self.tool_tags,
            "disabled_tools": self.disabled_tools,
            "system_prompt_override": self.system_prompt_override,
            "personality": self.personality,
            "agent_id": self.agent_id,
            "timeout": self.timeout,
            "max_context_tokens": self.max_context_tokens,
            "thinking_max_tokens": self.thinking_max_tokens,
            "plan_max_tokens": self.plan_max_tokens,
            "reflection_max_tokens": self.reflection_max_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        filtered = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**filtered)

    async def save_to_redis(self, session_id: str, ttl: int = 86400 * 7) -> None:
        """Persist config to Redis for session recovery."""
        try:
            from app.core.redis import redis_client
            if not redis_client:
                return
            key = f"agent:config:{session_id}"
            await redis_client.setex(key, ttl, json.dumps(self.to_dict(), ensure_ascii=False))
        except Exception:
            logger.debug("Failed to save agent config to Redis", exc_info=True)

    @classmethod
    async def load_from_redis(cls, session_id: str) -> Optional["AgentConfig"]:
        """Load persisted config from Redis."""
        try:
            from app.core.redis import redis_client
            if not redis_client:
                return None
            key = f"agent:config:{session_id}"
            raw = await redis_client.get(key)
            if raw:
                return cls.from_dict(json.loads(raw))
        except Exception:
            logger.debug("Failed to load agent config from Redis", exc_info=True)
        return None
