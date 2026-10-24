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
- 执行信封（issue #498）：``execution_id``（一次执行一个 uuid）+ ``status``
  （``completed|failed``）+ ``result``（成功时 ``{content, data}``，失败为
  ``null``）。``content/data/args`` 等既有字段保留一版，前端逐步迁移。
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
# Redis 锁 / 幂等依赖不可用时的 fail-closed（HTTP 503 SERVICE_UNAVAILABLE）。
ERROR_SERVICE_UNAVAILABLE = "service_unavailable"
# handler 产出超过上限（/export 1MB）；HTTP 映射 400 VALIDATION_ERROR。
ERROR_RESULT_TOO_LARGE = "result_too_large"

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
        """GET /commands 的公开形态（不含 handler）。

        ``args`` 是 v1 既有形态（位置参数列表，保留兼容）；``args_schema``
        是 issue #498 补齐的 JSON-Schema 风格声明（string 型位置参数），
        供前端/SDK 直接生成参数提示。
        """
        return {
            "name": self.name,
            "aliases": list(self.aliases),
            "description": self.description,
            "description_i18n": dict(self.description_i18n),
            "usage": self.usage or f"/{self.name}",
            "permission": self.permission,
            "args": [arg.to_dict() for arg in self.args],
            "args_schema": self.args_schema,
        }

    @property
    def args_schema(self) -> dict[str, Any]:
        """位置参数的 JSON-Schema 风格声明（全部参数都是 string）。"""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for arg in self.args:
            prop: dict[str, Any] = {"type": "string", "description": arg.description}
            if arg.choices:
                prop["enum"] = list(arg.choices)
            if arg.default is not None:
                prop["default"] = arg.default
            properties[arg.name] = prop
            if arg.required:
                required.append(arg.name)
        return {"type": "object", "properties": properties, "required": required}


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
    """命令执行结果（总返回；成功/失败都由它承载）。

    - ``execution_id``：一次执行一个 uuid（幂等重放返回同一个 id）；
    - ``status``：派生自信封——成功 ``completed``，失败 ``failed``；
    - ``result``：成功时 ``{content, data}``，失败为 ``None``（issue #498）。
    """

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
    execution_id: str = ""

    @property
    def status(self) -> str:
        return "completed" if self.success else "failed"

    @property
    def result(self) -> dict[str, Any] | None:
        if not self.success:
            return None
        return {"content": self.content, "data": self.data}

    def to_dict(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "command": self.command,
            "line": self.line,
            "scope": self.scope,
            "success": self.success,
            "status": self.status,
            "result": self.result,
            "content": self.content,
            "data": self.data,
            "args": self.args,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "error_field": self.error_field,
            "duration_ms": round(self.duration_ms, 1),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CommandExecution:
        """从 ``to_dict()`` 形态重建（幂等键里存的是完整响应 JSON）。"""
        result = payload.get("result")
        content = payload.get("content") or ""
        data = payload.get("data") or {}
        if isinstance(result, dict):
            content = result.get("content", content)
            data = result.get("data", data) or {}
        success = bool(payload.get("success"))
        if payload.get("status") == "failed":
            success = False
        return cls(
            command=str(payload.get("command") or ""),
            line=str(payload.get("line") or ""),
            success=success,
            content=str(content or ""),
            data=dict(data) if isinstance(data, dict) else {},
            args=dict(payload.get("args") or {}),
            error_code=str(payload.get("error_code") or ""),
            error_message=str(payload.get("error_message") or ""),
            error_field=str(payload.get("error_field") or ""),
            scope=str(payload.get("scope") or "global"),
            duration_ms=float(payload.get("duration_ms") or 0.0),
            execution_id=str(payload.get("execution_id") or ""),
        )
