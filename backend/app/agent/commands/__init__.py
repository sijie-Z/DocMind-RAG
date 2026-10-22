"""User Commands —— 服务端命令注册与无模型轮次执行（issue #475）。

公开面：
- ``Command`` / ``CommandArg`` / ``CommandContext`` / ``CommandResult`` /
  ``CommandExecution``：描述符与执行信封；
- ``CommandRegistry`` + ``command_registry``：进程内单例（内置命令已注册）；
- ``execute(agent, line, ...)``：直接对 Agent 执行一行命令，**不创建模型消息
  或轮次**；``iter_command_events`` 把结果映射到既有 SSE 事件通道；
- ``agent_scope_for_session`` / ``agent_scope`` 约定：``session:<会话 id>``。

启动路径：模块导入时注册内置命令，并应用可选的 ``backend/commands.json``
（``DOCMIND_COMMANDS_CONFIG`` 可覆盖路径）。配置加载失败绝不阻塞启动。
"""

from __future__ import annotations

import logging

from app.agent.commands.builtins import BUILTIN_COMMANDS, register_builtin_commands
from app.agent.commands.config import (
    AgentCommandScopeConfig,
    CommandsConfig,
    apply_commands_config,
    load_commands_config,
)
from app.agent.commands.execution import (
    execute,
    iter_command_events,
    resolve_is_admin,
    resolve_session_scope_for_command,
)
from app.agent.commands.parser import CommandSyntaxError, bind_args, parse_line
from app.agent.commands.registry import (
    GLOBAL_SCOPE,
    CommandRegistrationError,
    CommandRegistry,
    DuplicateCommandError,
    agent_scope_for_session,
    normalize_scope,
)
from app.agent.commands.types import (
    ERROR_COMMAND_BUSY,
    ERROR_COMMAND_FAILED,
    ERROR_COMMAND_TIMEOUT,
    ERROR_INVALID_ARGUMENT,
    ERROR_MISSING_ARGUMENT,
    ERROR_MISSING_COMMAND,
    ERROR_NOT_A_COMMAND,
    ERROR_PERMISSION_DENIED,
    ERROR_SESSION_NOT_FOUND,
    ERROR_SESSION_REQUIRED,
    ERROR_UNEXPECTED_ARGUMENT,
    ERROR_UNKNOWN_COMMAND,
    Command,
    CommandArg,
    CommandContext,
    CommandExecution,
    CommandFailure,
    CommandResult,
)

logger = logging.getLogger(__name__)

# 进程内单例：内置命令注册为全局作用域；配置可追加 agent 作用域 / 全局禁用。
command_registry = CommandRegistry()
register_builtin_commands(command_registry)
try:
    apply_commands_config(command_registry, load_commands_config())
except Exception:  # noqa: BLE001 — 命令配置是增强，绝不阻塞启动
    logger.warning("commands 配置应用失败（忽略，仅保留内置命令）", exc_info=True)

__all__ = [
    "BUILTIN_COMMANDS",
    "GLOBAL_SCOPE",
    "AgentCommandScopeConfig",
    "Command",
    "CommandArg",
    "CommandContext",
    "CommandExecution",
    "CommandFailure",
    "CommandRegistrationError",
    "CommandRegistry",
    "CommandResult",
    "CommandSyntaxError",
    "CommandsConfig",
    "DuplicateCommandError",
    "ERROR_COMMAND_BUSY",
    "ERROR_COMMAND_FAILED",
    "ERROR_COMMAND_TIMEOUT",
    "ERROR_INVALID_ARGUMENT",
    "ERROR_MISSING_ARGUMENT",
    "ERROR_MISSING_COMMAND",
    "ERROR_NOT_A_COMMAND",
    "ERROR_PERMISSION_DENIED",
    "ERROR_SESSION_NOT_FOUND",
    "ERROR_SESSION_REQUIRED",
    "ERROR_UNEXPECTED_ARGUMENT",
    "ERROR_UNKNOWN_COMMAND",
    "agent_scope_for_session",
    "apply_commands_config",
    "bind_args",
    "command_registry",
    "execute",
    "iter_command_events",
    "load_commands_config",
    "normalize_scope",
    "parse_line",
    "register_builtin_commands",
    "resolve_is_admin",
    "resolve_session_scope_for_command",
]
