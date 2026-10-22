"""Hooks 数据模型（issue #474）—— 事件、上下文、决策、规格与脱敏工具。

与 `backend/app/agent/hooks/registry.py` 的分工：本模块只放**纯数据**（pydantic
模型 + 常量 + 脱敏函数），不导入事件循环/执行器，避免热路径循环依赖。

契约要点（与 issue #474 §5.2/§5.3 对齐）：

- ``HookContext``：事件名 + 租户/会话/任务上下文 + 可变 payload。
- ``HookDecision``：waterfall 链的最终决策。action 取值在 issue §5.2 的
  ``allow/deny/modify`` 之上**扩展了 ``ask``** —— §5.3 与 §8 的验收明确要求
  ``tools/pre_execute`` 可以返回 ask 强制进入人工审批门；若按 §5.2 的
  Literal 封闭，ask 无路可走。其它事件类型收到 ask 视为**非法决策**，按该
  hook 的 ``failure_policy`` 处理（fail_open 放行 / fail_closed 拒绝）。
- ``HookSpec``：hooks.yaml/代码注册的统一定义；``order`` 是注册序号（同
  priority 时按注册顺序稳定排序，由总线维护）。
- ``HookError``：单个 hook 的超时/异常/非法决策记录，供 SSE ``hook_error``
  事件、审计日志与指标三处消费——失败绝不静默。
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── 事件清单（issue #474 §3：agent/* 与 tools/*）───────────────────────────
AGENT_TURN_START = "agent/turn_start"
AGENT_PRE_STEP = "agent/pre_step"
AGENT_POST_STEP = "agent/post_step"
AGENT_TURN_STOPPING = "agent/turn_stopping"
AGENT_TURN_END = "agent/turn_end"
TOOLS_PRE_EXECUTE = "tools/pre_execute"
TOOLS_POST_EXECUTE = "tools/post_execute"

EVENTS: tuple[str, ...] = (
    AGENT_TURN_START,
    AGENT_PRE_STEP,
    AGENT_POST_STEP,
    AGENT_TURN_STOPPING,
    AGENT_TURN_END,
    TOOLS_PRE_EXECUTE,
    TOOLS_POST_EXECUTE,
)

# serial 语义只用于**纯观察**事件（issue #474 §5.2：serial 只用于
# agent/post_step、agent/turn_end 这类观察事件）。其余事件走 waterfall：
# 监听器拿到 next，可以改写 payload / 短路。
SERIAL_EVENTS = frozenset({AGENT_POST_STEP, AGENT_TURN_END})
WATERFALL_EVENTS = frozenset(EVENTS) - SERIAL_EVENTS

HookAction = Literal["allow", "deny", "modify", "ask"]
FailurePolicy = Literal["fail_open", "fail_closed"]

# 模块白名单（issue #474 §5.4）：只允许 app.agent.hooks. 下的 dotted path。
# 这条正则同时拒绝 ``..``、绝对路径（含 Windows 盘符）、``os``/``subprocess``
# 等任意 import —— 校验先于 importlib 执行。
HOOK_MODULE_PATTERN = re.compile(
    r"^app\.agent\.hooks\.[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
HOOK_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")

# 参数脱敏（issue #474 §5.6）：至少 password/token/secret/key 字段打码。
# `key` 以"独立词"匹配（前后不是字母）：{"key"} / api_key / secret_key 命中；
# keyword / monkey / keyboard 不误伤（audit 日志保留可读字段）。
# 常见复合键（apiKey/accessKey/privateKey/authKey/signingKey 等 camelCase）单独
# 列式匹配——独立词规则不会命中它们。
_SENSITIVE_KEY = re.compile(
    r"(password|passwd|token|secret|(?<![a-z])key(?![a-z])"
    r"|(?:api|access|private|auth|signing|encryption)[_-]?key"
    r"|authorization|cookie|credential)",
    re.IGNORECASE,
)
_REDACTED = "***"


class HookContext(BaseModel):
    """一次 hook 调用的上下文（issue #474 §5.2）。"""

    event: str
    session_id: str | None = None
    task_id: str | None = None
    user_id: int | None = None
    organization_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class HookDecision(BaseModel):
    """waterfall 链的最终决策。

    - ``allow``：放行（payload 若给出则作为当前 payload 继续传递）；
    - ``deny``：阻断（reason 进 SSE/log；工具不执行 / 步骤不执行）；
    - ``modify``：改写 payload（``payload`` 必须为 dict）；
    - ``ask``：仅 ``tools/pre_execute`` 生效——强制走人工审批门。
    """

    action: HookAction = "allow"
    payload: dict[str, Any] | None = None
    reason: str = ""


class HookSpec(BaseModel):
    """一个 hook 的注册定义（代码注册与 hooks.yaml 共用）。"""

    name: str
    event: str
    module: str = ""
    enabled: bool = True
    priority: int = 100
    timeout_seconds: float = 2.0
    failure_policy: FailurePolicy = "fail_open"
    # 注册序号：总线写入，用于同 priority 时的稳定排序（低 priority 先执行）。
    order: int = 0


class HookError(BaseModel):
    """单个 hook 的失败记录（超时/异常/非法决策）。

    ``action`` 是**实际生效的失败策略**（fail_open/fail_closed），与 SSE
    ``hook_error`` 事件的 action 字段同义；``outcome`` 区分失败种类
    （timeout/error/invalid_decision），便于指标聚合。
    """

    hook: str
    event: str
    action: FailurePolicy = "fail_open"
    outcome: str = "error"
    error_type: str = ""
    message: str = ""
    elapsed_ms: int = 0


def redact_arguments(value: Any) -> Any:
    """递归脱敏：敏感字段名（password/token/secret/key/...）的值替换为 ``***``。

    只用于**日志/审计**；传给 hook 的 payload 保留原值（hook 需要真实参数才能
    做改写/判定）。非容器值原样返回。
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SENSITIVE_KEY.search(key):
                out[key] = _REDACTED
            else:
                out[key] = redact_arguments(item)
        return out
    if isinstance(value, (list, tuple)):
        return [redact_arguments(item) for item in value]
    return value
