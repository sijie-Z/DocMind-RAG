"""Hooks —— 统一 agent/tool 事件扩展点（issue #474，Epic #473）。

公开面：

- 模型：``HookContext`` / ``HookDecision`` / ``HookError`` / ``HookSpec``；
- 总线：``HookBus``（waterfall/serial、超时、失败策略、指标、审计日志）+
  进程内单例 ``get_hook_bus`` / ``set_hook_bus``；
- 配置：``load_hook_specs``（严格校验，``HookConfigError`` 含字段路径）/
  ``initialize_from_settings``（``HOOKS_ENABLED=false`` 时零读取）；
- 脱敏：``redact_arguments``（password/token/secret/key 等字段打码）。

设计边界（与 issue #474 对齐）：

- **默认关闭**：进程内单例初始 ``enabled=False``；只有启动路径在
  ``HOOKS_ENABLED=true`` 时加载并启用。关闭时 emit 直接 allow、handler 零调用、
  **不读 hooks.yaml**（零行为变化，也是回滚开关）。
- **只做进程内 hook**：外部进程 hook（shell 命令）与 ``llm/*`` 事件是后续 issue。
- **hook 不能绕过既有安全闸门**：``tools/pre_execute`` 的 ask 走 executor 既有
  审批门（无审批通道按 deny），deny 在 handler 调用之前生效；组织上下文 /
  禁用工具 / 审批判定的 fail-closed 逻辑不被 hook 改写——hook 只能更严格，
  不能更宽松（``allow`` 不等于绕过审批）。

事件与插入点：

    agent/turn_start     loop.run 起点（观察）
    agent/pre_step       executor._execute_step_once，消息构建完成后、LLM 调用前
                         （可改写 messages / deny 跳过本轮）
    agent/post_step      executor 步终态后（serial 观察）
    agent/turn_stopping  loop 自然结束前（deny → 继续循环，有硬上限）
    agent/turn_end       loop 收尾（serial 观察）
    tools/pre_execute    executor 审批预检之前（allow/deny/modify/ask）
    tools/post_execute   工具结果落账之前（allow/deny/modify）
"""

from __future__ import annotations

from typing import Any

from app.agent.hooks.loader import (
    DEFAULT_CONFIG_PATH,
    HookConfigError,
    initialize_from_settings,
    load_hook_specs,
    resolve_config_path,
)
from app.agent.hooks.models import (
    AGENT_POST_STEP,
    AGENT_PRE_STEP,
    AGENT_TURN_END,
    AGENT_TURN_START,
    AGENT_TURN_STOPPING,
    EVENTS,
    SERIAL_EVENTS,
    TOOLS_POST_EXECUTE,
    TOOLS_PRE_EXECUTE,
    WATERFALL_EVENTS,
    HookAction,
    HookContext,
    HookDecision,
    HookError,
    HookSpec,
    redact_arguments,
)
from app.agent.hooks.registry import (
    HookBus,
    HookHandler,
    Next,
    get_hook_bus,
    hook_depth_active,
    set_hook_bus,
)

__all__ = [
    "AGENT_POST_STEP",
    "AGENT_PRE_STEP",
    "AGENT_TURN_END",
    "AGENT_TURN_START",
    "AGENT_TURN_STOPPING",
    "DEFAULT_CONFIG_PATH",
    "EVENTS",
    "SERIAL_EVENTS",
    "TOOLS_POST_EXECUTE",
    "TOOLS_PRE_EXECUTE",
    "WATERFALL_EVENTS",
    "HookAction",
    "HookBus",
    "HookConfigError",
    "HookContext",
    "HookDecision",
    "HookError",
    "HookHandler",
    "HookSpec",
    "Next",
    "get_hook_bus",
    "hook_depth_active",
    "initialize_from_settings",
    "load_hook_specs",
    "redact_arguments",
    "resolve_config_path",
    "set_hook_bus",
]


def hook_error_events(errors: list[HookError]) -> list[Any]:
    """把 HookError 列表转成 ``hook_error`` AgentEvent（SSE 通道，issue §5.7）。

    延迟导入 events：本模块在 executor/loop 的热路径上被导入，保持依赖单向。
    """
    if not errors:
        return []
    from app.agent.events import AgentEvent

    return [
        AgentEvent(
            type="hook_error",
            content=error.message,
            error_type=error.error_type,
            hook=error.hook,
            event_name=error.event,
            action=error.action,
            elapsed_ms=error.elapsed_ms,
        )
        for error in errors
    ]
