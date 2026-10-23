"""User Commands API —— 列出 / 执行服务端命令（issue #475 / #498）。

端点：
    GET  /api/v1/commands           — 当前用户可用命令（描述/参数/权限标记）
    POST /api/v1/commands/execute   — 执行一行命令；不写用户消息、不触发模型

请求契约（issue #498）：
- ``line`` 是 **v1 规范形态**（如 ``/help``、``export json``；前导 ``/`` 可选）。
- ``command`` + ``args`` 仅为**兼容层**：端点把它拼回一行再走同一执行路径
  （``args`` 可为位置参数数组或按声明名映射的对象）。
- 可选 ``idempotency_key``：同一 user + 同 key 的**串行**重复请求返回同一
  ``execution_id``，handler 只执行一次（Redis，结果 TTL 24h）。**执行中并发**
  的重复请求最多等待 ~1s，仍未完成返回 409 ``COMMAND_BUSY``（与 Stripe
  幂等键语义一致：同一 key 不能被并发重放）；不校验 payload 是否一致。

响应信封（成功 data）：既有 ``command/line/scope/success/content/data/args/
error_*`` 保留一版，另加 ``execution_id``（uuid）、``status``
（``completed|failed``）、``result``（成功时 ``{content, data}``，失败 null）。

安全语义：
- 两个端点都先 ``require_organization``（多租户 fail-closed：无组织 403），
  再判定组织管理员（管理命令可见性 / 执行双重校验）；
- 携带 ``session_id`` 时必须先过会话归属（不存在 / 他人 / 跨组织一律 404），
  命令作用域与 /status、/export 的数据读取使用同一套 ``session_access_*`` 口径；
- ``COMMANDS_ENABLED=false``（回滚开关）：GET 返回 enabled=false + 空清单，
  POST 503 SERVICE_UNAVAILABLE——前端据此回退本地模板。

错误契约（POST）：未知命令 404 NOT_FOUND；管理命令无权限 403
COMMAND_PERMISSION_DENIED；参数错误 400 COMMAND_INVALID_ARGUMENT（detail.field
给出字段名）；同会话并发 409 COMMAND_BUSY；Redis 不可用 / 命令通道关闭 503
SERVICE_UNAVAILABLE；超时 504 COMMAND_TIMEOUT；/export 超过 1MB 400
VALIDATION_ERROR；handler 内部错误 500 COMMAND_FAILED（原始异常只进日志）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.commands import (
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
    GLOBAL_SCOPE,
    CommandContext,
    CommandExecution,
    CommandSyntaxError,
    agent_scope_for_session,
    command_registry,
    execute,
    resolve_is_admin,
)
from app.agent.commands.parser import compose_line
from app.agent.commands.types import ERROR_RESULT_TOO_LARGE, ERROR_SERVICE_UNAVAILABLE
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_organization
from app.exceptions import AppError, NotFoundError, ServiceUnavailableError, ValidationError
from app.models.user import User
from app.services.session_service import (
    load_accessible_session,
    resolve_session_access_scope,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class CommandExecuteRequest(BaseModel):
    """执行请求。

    ``line`` 是 v1 规范形态；``command``/``args`` 是兼容层（两者至少给一个）。
    """

    line: str | None = Field(
        None,
        min_length=1,
        max_length=4000,
        description="完整命令行（前导 / 可选），如 /help 或 /export json",
    )
    command: str | None = Field(
        None,
        min_length=1,
        max_length=4000,
        description="兼容层：命令名（可省略前导 /）；与 args 一起拼成 line 执行",
    )
    args: list[Any] | dict[str, Any] | None = Field(
        None,
        description="兼容层：命令行参数（位置数组或按声明名映射的对象）",
    )
    session_id: str | None = Field(
        None,
        max_length=64,
        description="会话 id：用于 agent 作用域命令与 /status、/export；须为当前用户可见会话",
    )
    idempotency_key: str | None = Field(
        None,
        min_length=1,
        max_length=128,
        description="幂等键：同一 user + 同 key 的重复请求返回同一 execution_id",
    )

    @model_validator(mode="after")
    def _require_line_or_command(self) -> CommandExecuteRequest:
        if not (self.line or self.command):
            raise ValueError("line 或 command 必须提供其一")
        return self


# 执行错误码 → HTTP 状态 / 对外错误码（稳定契约，前端按此分支）。
_ERROR_STATUS: dict[str, int] = {
    ERROR_NOT_A_COMMAND: 400,
    ERROR_MISSING_COMMAND: 400,
    ERROR_MISSING_ARGUMENT: 400,
    ERROR_UNEXPECTED_ARGUMENT: 400,
    ERROR_INVALID_ARGUMENT: 400,
    ERROR_RESULT_TOO_LARGE: 400,
    ERROR_SESSION_REQUIRED: 400,
    ERROR_PERMISSION_DENIED: 403,
    ERROR_UNKNOWN_COMMAND: 404,
    ERROR_SESSION_NOT_FOUND: 404,
    ERROR_COMMAND_BUSY: 409,
    ERROR_SERVICE_UNAVAILABLE: 503,
    ERROR_COMMAND_TIMEOUT: 504,
    ERROR_COMMAND_FAILED: 500,
}
_ERROR_CODES: dict[str, str] = {
    ERROR_NOT_A_COMMAND: "COMMAND_INVALID_SYNTAX",
    ERROR_MISSING_COMMAND: "COMMAND_INVALID_SYNTAX",
    ERROR_MISSING_ARGUMENT: "COMMAND_INVALID_ARGUMENT",
    ERROR_UNEXPECTED_ARGUMENT: "COMMAND_INVALID_ARGUMENT",
    ERROR_INVALID_ARGUMENT: "COMMAND_INVALID_ARGUMENT",
    # 契约（issue #498）：导出超限对外是 VALIDATION_ERROR（400）。
    ERROR_RESULT_TOO_LARGE: "VALIDATION_ERROR",
    ERROR_PERMISSION_DENIED: "COMMAND_PERMISSION_DENIED",
    # 契约：未知命令 404 NOT_FOUND（前端同时兼容旧码 COMMAND_NOT_FOUND）。
    ERROR_UNKNOWN_COMMAND: "NOT_FOUND",
    ERROR_SESSION_REQUIRED: "COMMAND_SESSION_REQUIRED",
    ERROR_SESSION_NOT_FOUND: "SESSION_NOT_FOUND",
    ERROR_COMMAND_BUSY: "COMMAND_BUSY",
    ERROR_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
    ERROR_COMMAND_TIMEOUT: "COMMAND_TIMEOUT",
    ERROR_COMMAND_FAILED: "COMMAND_FAILED",
}


class CommandExecutionError(AppError):
    """把失败的命令执行映射为 HTTP 错误（状态码/错误码见上方映射表）。"""

    def __init__(self, execution: CommandExecution):
        super().__init__(
            execution.error_message or "命令执行失败",
            error_code=_ERROR_CODES.get(execution.error_code, "COMMAND_FAILED"),
            detail={
                "command": execution.command,
                "error_code": execution.error_code,
                "field": execution.error_field or None,
            },
        )
        self.status_code = _ERROR_STATUS.get(execution.error_code, 500)


async def _require_accessible_session(
    db: AsyncSession, session_id: str, current_user: User
) -> None:
    """会话归属闸门：不存在 / 他人 / 跨组织一律 404（不区分，避免枚举）。"""
    scope = await resolve_session_access_scope(current_user, db)
    session = await load_accessible_session(db, session_id, current_user, scope)
    if session is None:
        raise NotFoundError(
            "会话不存在或无权访问",
            error_code="SESSION_NOT_FOUND",
            detail={"session_id": session_id},
        )


@router.get("")
async def list_commands(
    session_id: str | None = Query(
        None, max_length=64, description="可选：按会话的 agent 作用域合并命令"
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """当前用户可用命令（含描述、参数 schema、权限标记；按作用域遮蔽去重）。"""
    effective_org_id = await require_organization(current_user, db)

    scope: str | None = None
    if session_id:
        await _require_accessible_session(db, session_id, current_user)
        scope = agent_scope_for_session(session_id)

    is_admin = await resolve_is_admin(db, current_user, effective_org_id)
    enabled = bool(settings.COMMANDS_ENABLED)
    commands = (
        command_registry.list_commands(agent_scope=scope, is_admin=is_admin) if enabled else []
    )
    return {
        "success": True,
        "data": {
            "enabled": enabled,
            "scope": scope or GLOBAL_SCOPE,
            "commands": [command.to_dict() for command in commands],
        },
    }


def _resolve_request_line(body: CommandExecuteRequest) -> str:
    """``line`` 优先（v1 规范形态）；否则把 ``command+args`` 兼容层拼回一行。

    映射型 ``args`` 按命令声明的参数顺序拼接（未命中命令时退回映射自身顺序），
    绝不把 dict 顺序当成参数顺序的约定。
    """
    if body.line:
        return body.line.strip()
    command = (body.command or "").strip()
    scope = agent_scope_for_session(body.session_id) if body.session_id else None
    head = command.lstrip("/").split()
    resolved = command_registry.resolve(head[0], agent_scope=scope)[0] if head else None
    arg_names = [arg.name for arg in resolved.args] if resolved else ()
    if isinstance(body.args, Mapping) and resolved is not None:
        # 映射型 args 只接受命令声明的参数名；未知键必须显式 400，
        # 绝不静默当成位置参数错绑（评审 minor）。
        unknown = [key for key in body.args if key not in arg_names]
        if unknown:
            raise ValidationError(
                f"未知参数：{', '.join(unknown)}",
                error_code="COMMAND_INVALID_ARGUMENT",
                detail={"command": resolved.name, "fields": unknown},
            )
    try:
        return compose_line(
            command,
            body.args,
            arg_specs=resolved.args if resolved is not None else (),
        )
    except CommandSyntaxError as exc:
        raise ValidationError(
            exc.message,
            error_code="COMMAND_INVALID_SYNTAX",
            detail={"command": command, "field": exc.field_name or None},
        ) from exc


@router.post("/execute")
async def execute_command(
    body: CommandExecuteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """执行一行命令（无模型轮次、不写用户消息）。

    成功返回 ``CommandExecution``；失败按 ``error_code`` 映射 HTTP 状态与
    稳定错误码（未知命令 / 权限 / 字段级参数错误 / 并发 / 依赖不可用 / 超时）。
    """
    effective_org_id = await require_organization(current_user, db)
    if not settings.COMMANDS_ENABLED:
        raise ServiceUnavailableError("命令功能未启用", detail={"reason": "commands_disabled"})

    if body.session_id:
        await _require_accessible_session(db, body.session_id, current_user)

    line = _resolve_request_line(body)
    is_admin = await resolve_is_admin(db, current_user, effective_org_id)
    ctx = CommandContext(
        user_id=current_user.id,
        organization_id=effective_org_id,
        session_id=body.session_id,
        is_admin=is_admin,
        is_superuser=bool(getattr(current_user, "is_superuser", False)),
        db=db,
    )
    execution = await execute(
        ctx,
        line,
        registry=command_registry,
        idempotency_key=body.idempotency_key,
    )
    if execution.success:
        return {"success": True, "data": execution.to_dict()}
    raise CommandExecutionError(execution)
