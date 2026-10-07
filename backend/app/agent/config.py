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
PlanningMode = Literal["coarse", "normal", "fine"]

DEFAULT_DISABLED_TOOLS: list[str] = [
    "execute_python",
    "execute_sql",
    "mcp_call",  # 高危：宿主机文件读写 + 子进程环境变量泄露，默认禁用
]


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
    # 单次 run 内可「执行」的计划步骤数，跨 replan 轮次累计；重试不另计
    # （重试由 max_retries_per_step 管）。0 或负值 = 不限制。默认 20：
    # max_plan_steps(10) × (1 + MAX_REPLANS_PER_RUN=1) —— 默认组合下合法的
    # 最坏情形是两轮计划全部执行，默认值不应把合法的 replan 轮饿死（issue #203 ①）。
    max_iterations: int = 20
    max_tool_calls_per_turn: int = 5
    max_retries_per_step: int = 3

    # ── Phase toggles ──
    enable_planning: bool = True
    enable_reflection: bool = True
    enable_tools: bool = True
    enable_memory: bool = True
    enable_thinking: bool = True
    enable_experience: bool = True  # Self-Improving: learn from past failures
    enable_skills: bool = True  # issue #200：技能索引注入 + load_skill_reference + 学习闭环

    # ── Planning granularity (experimental) ──
    planning_mode: PlanningMode = "normal"
    # "coarse" → single-step, "normal" → balanced, "fine" → maximum decomposition

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
    # 单次 run 累计 token 上限（prompt + completion），0 或负值 = 不限制。
    # 与 max_tokens（单次 LLM 调用的输出上限）语义不同；用量由 issue #202 的
    # 单一入口 record_llm_usage 累计（issue #203 ②）。
    max_total_tokens: int = 0

    # ── Model selection ──
    def get_deep_model(self) -> str:
        """模型用于综合推理/决策（Reflector, Reviewer, 综合回答）"""
        return self.deep_think_model or self.model

    def get_quick_model(self) -> str:
        """模型用于信息收集/工具调用（Executor step, synthesis）"""
        return self.quick_think_model or self.model

    # ── Budget (issue #203 ②) ──

    def over_budget(self, ctx) -> bool:
        """单次 run 累计 token 是否已达上限。

        只读判断：max_total_tokens <= 0（默认）或 ctx 缺失 → 不限制。
        `total_tokens` 由 `app.agent.usage.record_llm_usage`（issue #202）累计，
        本方法不产生用量。
        """
        if self.max_total_tokens <= 0 or ctx is None:
            return False
        return getattr(ctx, "total_tokens", 0) >= self.max_total_tokens

    # ── Advanced ──
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
            "enable_experience": self.enable_experience,
            "enable_skills": self.enable_skills,
            "tool_tags": self.tool_tags,
            "disabled_tools": self.disabled_tools,
            "system_prompt_override": self.system_prompt_override,
            "personality": self.personality,
            "agent_id": self.agent_id,
            "timeout": self.timeout,
            "max_context_tokens": self.max_context_tokens,
            "max_total_tokens": self.max_total_tokens,
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
