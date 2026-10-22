"""User Commands —— 描述符、执行上下文与结果类型（issue #475）。

设计边界（与 issue #475 / ADR 对齐）：
- ``Command`` 是**服务端**命令描述符：name / aliases / description / args /
  permission / handler 全部在一处声明；``description_i18n`` 同时给出中英文
  文案（后端无 i18n 运行时，前端按 locale 取用，避免双语漂移）。
- ``CommandContext`` 是一次命令执行的身份与作用域快照：user / org / session /
  是否组织管理员 / 数据库会话。命令 handler 只能通过它触达数据，禁止绕过
  多租户 fail-closed。
- ``CommandExecution`` 是**总返回**：注册表解析、权限、参数、并发、超时、
  handler 异常都收敛为结构化结果（不向 SSE 流抛出半截异常）；HTTP 端点再按
  ``error_code`` 映射状态码。``execute()`` 只会让 ``CancelledError`` 逃逸。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

CommandPermission = Literal["user", "admin"]

# ── 错误码闭集（执行结果 / HTTP detail / SSE command_error.error_type 共用）──
ERROR_NOT_A_COMMAND = "not_a_command"
ERROR_MISSING_COMMAND = "missing_command"
ERROR_UNKNOWN_COMMAND = "unknown_command"
ERROR_PERMISSION_DENIED = "permission_denied"
ERROR_MISSING_ARGUMENT = "missing_argument"
ERROR_UNEXPECTED_ARGUMENT = "unexpected_argument"
ERROR_INVALID_ARGUMENT = "invalid_argument"
ERROR_COMMAND_BUSY = "command_busy"
ERROR_COMMAND_TIMEOUT = "command_timeout"
ERROR_COMMAND_FAILED = "command_failed"
ERROR_SESSION_REQUIRED = "session_required"
ERROR_SESSION_NOT_FOUND = "session_not_found"

DEFAULT_COMMAND_TIMEOUT = 10.0
MAX_COMMAND_TIMEOUT = 120.0


@dataclass(frozen=True)
class CommandArg:
    """一个位置参数的声明（按声明顺序绑定）。

    只支持位置参数是刻意的：斜杠命令是"一行文本"，引入 flag/命名参数会把
    解析复杂度和错误面放大；参数超出一行能表达的范围时应该做成工具而非命令。
    """

    name: str
    description: str
    required: bool = False
    choices: tuple[str, ...] = ()
    default: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required": self.required,
            "choices": list(self.choices),
            "default": self.default,
        }


@dataclass
class CommandResult:
    """handler 的成功返回：content 供人读，data 供结构化消费（前端/审计）。"""

    content: str
    data: dict[str, Any] = field(default_factory=dict)


CommandHandler = Callable[["CommandContext", dict[str, Any]], Awaitable[CommandResult]]


class CommandFailure(Exception):
    """handler 的**预期失败**（未知目标、会话不存在等）。

    ``execute()`` 捕获它并转换为 ``CommandExecution``；message 是面向用户的
    稳定文案，绝不携带异常原文（原始异常走 ``command_failed`` 的通用兜底）。
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        field_name: str = "",
        data: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message
        self.field_name = field_name
        self.data = data or {}
        super().__init__(message)


@dataclass
class Command:
    """服务端命令描述符。

    - ``permission="admin"``：仅组织管理员（或平台超管）可见/可执行；
      可见性与执行都检查，绝不只做菜单隐藏。
    - ``timeout``：单次执行上限（秒），由 ``execute()`` 用 asyncio 超时兜底。
    - ``aliases``：同层与 name 共用命名空间；别名冲突按重名处理。
    """

    name: str
    description: str
    handler: CommandHandler
    aliases: tuple[str, ...] = ()
    usage: str = ""
    permission: CommandPermission = "user"
    args: tuple[CommandArg, ...] = ()
    timeout: float = DEFAULT_COMMAND_TIMEOUT
    description_i18n: dict[str, str] = field(default_factory=dict)

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)

    def to_dict(self) -> dict[str, Any]:
        """GET /commands 的公开形态（不含 handler）。"""
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "description_i18n": dict(self.description_i18n),
            "usage": self.usage or f"/{self.name}",
            "permission": self.permission,
            "args": [arg.to_dict() for arg in self.args],
        }


@dataclass
class CommandContext:
    """一次命令执行的身份 / 作用域 / 依赖快照。

    - ``organization_id`` 必须由端点用 ``require_organization`` 解析后传入
      （fail-closed：无组织不执行任何命令）。
    - ``is_admin`` 由 ``resolve_is_admin`` 统一判定（组织管理员 / 平台超管）。
    - ``scope`` / ``registry`` 由 ``execute()`` 盖章，handler 可读但不应改写。
    """

    user_id: int
    organization_id: int | None
    session_id: str | None = None
    is_admin: bool = False
    is_superuser: bool = False
    db: Any = None  # AsyncSession | None —— 由端点注入，handler 只读
    registry: Any = None  # CommandRegistry | None —— 由 execute() 注入
    scope: str = "global"


@dataclass
class CommandExecution:
    """命令执行结果（总返回；成功/失败都由它承载）。"""

    command: str
    line: str
    success: bool
    content: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    args: dict[str, Any] = field(default_factory=dict)
    error_code: str = ""
    error_message: str = ""
    error_field: str = ""
    scope: str = "global"
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "line": self.line,
            "scope": self.scope,
            "success": self.success,
            "content": self.content,
            "data": self.data,
            "args": self.args,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_field": self.error_field,
            "duration_ms": round(self.duration_ms, 1),
        }
