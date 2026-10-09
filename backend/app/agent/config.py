"""Agent configuration — persistable, per-session settings.

AgentConfig controls the behavior of the PER agent loop. Instances
can be serialized to/from Redis so that session preferences survive
page reloads and server restarts.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

logger = logging.getLogger(__name__)

Personality = Literal["precise", "creative", "balanced"]
PlanningMode = Literal["coarse", "normal", "fine"]

DEFAULT_DISABLED_TOOLS: list[str] = [
    "execute_python",
    "execute_sql",
    "mcp_call",  # 高危：宿主机文件读写 + 子进程环境变量泄露，默认禁用
]


# ── 值级规整（issue #319；与 #287 artifacts 闸门同族，读/写同源）──────────────
# 快照 / Redis 里"合法 JSON、值类型漂移"的配置字段：坏值会被直接消费
# （max_iterations 与迭代计数比较、max_total_tokens 预算判断、temperature /
# timeout 进 LLM 调用参数……），炸在 run 中段；错误兜底又经 to_dict 把坏值
# **原样回写**快照（毒丸自续——#319 全链实证 chain B）。读（from_dict）与写
# （to_dict）两侧过同一道闸门：好值原样通过，坏值降级为默认（不传该字段 =
# 数据类默认值）并留 warning 行迹——绝不把坏值带进运行态，也绝不让它落回
# 快照。闸门只看**类型**不看域（0 / 负值 / 域外枚举串等类型合法的值一律
# 透传；域的兜底由各自消费方的既有默认分支负责）。
# 冻结：本表必须与 AgentConfig 的 dataclass 字段集全等
# （test_issue319_value_gate_residuals 的冻结用例；新增字段不登记 = 拉红）。


def _is_int(value: Any) -> bool:
    """整数口径（bool 拒斥：JSON true/false 是类型漂移，写入方只写 int）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _is_opt_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _is_list_of_str(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _is_opt_list_of_str(value: Any) -> bool:
    return value is None or _is_list_of_str(value)


_CONFIG_FIELD_SPECS: dict[str, Callable[[Any], bool]] = {
    "model": _is_str,
    "deep_think_model": _is_str,
    "quick_think_model": _is_str,
    "temperature": _is_number,
    "max_tokens": _is_int,
    "max_plan_steps": _is_int,
    "max_iterations": _is_int,
    "max_tool_calls_per_turn": _is_int,
    "max_retries_per_step": _is_int,
    "enable_planning": _is_bool,
    "enable_reflection": _is_bool,
    "enable_tools": _is_bool,
    "enable_memory": _is_bool,
    "enable_thinking": _is_bool,
    "enable_experience": _is_bool,
    "enable_skills": _is_bool,
    "planning_mode": _is_str,  # Literal 域值由 planner 的默认分支兜底（类型级闸门）
    "tool_tags": _is_opt_list_of_str,
    # disabled_tools 拒斥 None/坏形状（不是 _is_opt_*）：消费方按"非空才过滤"
    # 语义读它——漂移成 None 会静默放开 DEFAULT_DISABLED_TOOLS 的兜底禁单；
    # 删键 → 数据类默认值（含服务端强制禁用项），安全性恢复。
    "disabled_tools": _is_list_of_str,
    "system_prompt_override": _is_opt_str,
    "personality": _is_str,
    "agent_id": _is_str,
    "enable_checkpoint": _is_bool,
    "timeout": _is_number,
    "max_context_tokens": _is_int,
    "max_total_tokens": _is_int,
    "plan_max_tokens": _is_int,
    "reflection_max_tokens": _is_int,
}


def _normalize_config_values(raw: dict, *, source: str) -> dict:
    """值级规整（#319；纯函数，除 warning 外无副作用）。

    逐字段按 _CONFIG_FIELD_SPECS 校验：类型合法原样保留，坏值删键并留
    warning（读侧=取数据类默认值；写侧=落盘缺键、下次读取同样得默认值）。
    非本数据类字段（未知/旧键）静默忽略——与 from_dict 既有过滤同语义。
    """
    clean: dict[str, Any] = {}
    for key, value in raw.items():
        spec = _CONFIG_FIELD_SPECS.get(key)
        if spec is None:
            continue
        if spec(value):
            clean[key] = value
        else:
            logger.warning(
                "checkpoint config 值级漂移（key=%s, type=%s, %s）："
                "按默认值降级（坏值不得入运行态/快照）",
                key,
                type(value).__name__,
                source,
            )
    return clean


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

    # ── Checkpoint / resume（issue #201 最小版）──
    # kill-switch：关闭后 chat 不建任务行、不落快照（完全退回旧行为）。
    # 不进 REST 更新面（AgentConfigUpdate），但随 to_dict/from_dict 快照进
    # checkpoint 信封——resume 用 run 起点的规则快照，不重读用户最新配置。
    enable_checkpoint: bool = True

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
        # 写侧闸门（#319）：运行态里万一存在类型漂移的坏值（外部写入者/未来
        # bug），落盘的必须是规整后的形态——坏值删键后由读侧取默认值，毒丸
        # 绝不落回快照/Redis 续给下一次 resume。
        return _normalize_config_values({
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
            "enable_checkpoint": self.enable_checkpoint,
            "timeout": self.timeout,
            "max_context_tokens": self.max_context_tokens,
            "max_total_tokens": self.max_total_tokens,
            "plan_max_tokens": self.plan_max_tokens,
            "reflection_max_tokens": self.reflection_max_tokens,
        }, source="config-write")

    @classmethod
    def from_dict(cls, data: dict) -> "AgentConfig":
        # 读侧闸门（#319）：容器级漂移（非映射）→ 整取默认配置（resume /
        # 会话加载的读取点不得被坏容器炸掉：chain B' 实证 from_dict("boom")
        # `.items()` AttributeError）；字段级漂移 → 删字段 = 数据类默认值。
        if not isinstance(data, dict):
            logger.warning(
                "checkpoint config 值级漂移（容器, type=%s, config-read）："
                "按默认配置降级（坏容器不得炸读取点）",
                type(data).__name__,
            )
            return cls()
        return cls(**_normalize_config_values(data, source="config-read"))

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
