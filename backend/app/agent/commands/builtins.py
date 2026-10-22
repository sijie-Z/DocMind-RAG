"""内置命令 —— /help、/status、/export、/tools（issue #475 最小集）。

边界：
- 所有 handler 只读 ``CommandContext``（user / org / session / db），不触达
  模型、不写会话（命令永不进入 PER 轮次，也不产生用户消息）。
- 会话类命令（/status、/export）必须走 ``session_access_conditions`` 的
  同一套归属口径：不存在 / 他人 / 跨组织一律按``session_not_found`` fail
  closed，绝不按裸 session_id 放行。
- /help、/status、/export、/tools 都是普通用户命令；管理命令由后续扩展
  注册（permission="admin"），可见性与执行双重校验在 registry/execute 层。
- 文案为中文（后端无 i18n 运行时，与既有错误文案约定一致）；描述同时携带
  ``description_i18n`` 中英文，供前端按 locale 取用，避免双语漂移。

测试通过 patch 本模块的 ``_load_session`` / ``_count_messages`` /
``_last_message`` / ``_load_messages`` / ``checkpoint.list_tasks`` /
``tool_registry.list_tools`` 隔离数据库与工具注册表
（tests/unit/test_user_commands_builtins.py）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, func, select

from app.agent import checkpoint
from app.agent.commands.types import (
    ERROR_COMMAND_FAILED,
    ERROR_PERMISSION_DENIED,
    ERROR_SESSION_NOT_FOUND,
    ERROR_SESSION_REQUIRED,
    ERROR_UNKNOWN_COMMAND,
    Command,
    CommandArg,
    CommandContext,
    CommandFailure,
    CommandResult,
)
from app.agent.config import DEFAULT_DISABLED_TOOLS
from app.agent.registry import tool_registry
from app.models.chat import ChatMessage, ChatSession
from app.services.session_service import SessionAccessScope, session_access_conditions

logger = logging.getLogger(__name__)

MAX_EXPORT_MESSAGES = 2000
STATUS_TASK_LIMIT = 5


# ── 数据读取（测试替身边界）──────────────────────────────────────────────────


async def _load_session(ctx: CommandContext) -> ChatSession | None:
    """按 id 加载调用者可见会话（本人 + 有效组织；超管旁路，与既有口径一致）。"""
    if not ctx.session_id:
        return None
    if ctx.db is None:
        raise CommandFailure(ERROR_COMMAND_FAILED, "数据库会话不可用，无法查询会话")
    scope = SessionAccessScope(bypass_org=ctx.is_superuser, org_id=ctx.organization_id)
    stmt = select(ChatSession).where(*session_access_conditions(ctx.session_id, ctx.user_id, scope))
    return (await ctx.db.execute(stmt)).scalar_one_or_none()


async def _count_messages(ctx: CommandContext, session_id: str) -> int:
    stmt = select(func.count(ChatMessage.id)).where(ChatMessage.session_id == session_id)
    return int((await ctx.db.execute(stmt)).scalar() or 0)


async def _last_message(ctx: CommandContext, session_id: str) -> ChatMessage | None:
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(desc(ChatMessage.created_at))
        .limit(1)
    )
    return (await ctx.db.execute(stmt)).scalars().first()


async def _load_messages(ctx: CommandContext, session_id: str) -> list[ChatMessage]:
    """取会话消息：**新 → 旧**，多取一条用于判定截断。

    截断必须保留"最近的 N 条"（与文案一致）；若按旧 → 新取前 N 条，长会话
    会丢掉最新内容却声称导出的是最近消息（评审 m1 实测反例）。
    """
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        .limit(MAX_EXPORT_MESSAGES + 1)
    )
    return list((await ctx.db.execute(stmt)).scalars().all())


# ── /help ────────────────────────────────────────────────────────────────────


def _registry(ctx: CommandContext):
    if ctx.registry is None:
        from app.agent.commands import command_registry

        ctx.registry = command_registry
    return ctx.registry


def _agent_scope(ctx: CommandContext) -> str | None:
    return ctx.scope if ctx.scope and ctx.scope != "global" else None


async def cmd_help(ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    registry = _registry(ctx)
    target = (args.get("command") or "").strip().lstrip("/")
    if target:
        command, _scope = registry.resolve(target, agent_scope=_agent_scope(ctx))
        if command is None:
            raise CommandFailure(
                ERROR_UNKNOWN_COMMAND,
                f"未知命令：/{target}。输入 /help 查看可用命令。",
                field_name="command",
            )
        if command.permission == "admin" and not ctx.is_admin:
            raise CommandFailure(
                ERROR_PERMISSION_DENIED, f"命令 /{command.name} 仅限组织管理员使用"
            )
        return CommandResult(content=_render_command_detail(command), data=command.to_dict())

    commands = registry.list_commands(agent_scope=_agent_scope(ctx), is_admin=ctx.is_admin)
    lines = [f"## 可用命令（{len(commands)}）"]
    for command in commands:
        suffix = (
            f"（别名：{', '.join('/' + alias for alias in command.aliases)}）"
            if command.aliases
            else ""
        )
        admin = "（仅管理员）" if command.permission == "admin" else ""
        lines.append(
            f"- `{command.usage or '/' + command.name}` — {command.description}{admin}{suffix}"
        )
    lines.append("")
    lines.append("提示：命令直接在服务端执行，不会进入模型上下文；输入 `/help <命令名>` 查看详情。")
    return CommandResult(
        content="\n".join(lines),
        data={"commands": [command.to_dict() for command in commands]},
    )


def _render_command_detail(command: Command) -> str:
    lines = [
        f"## /{command.name}",
        command.description,
        f"用法：`{command.usage or '/' + command.name}`",
        f"权限：{'仅组织管理员' if command.permission == 'admin' else '普通用户'}",
    ]
    if command.aliases:
        lines.append(f"别名：{', '.join('/' + alias for alias in command.aliases)}")
    if command.args:
        lines.append("参数：")
        for arg in command.args:
            parts = ["必填" if arg.required else "可选"]
            if arg.choices:
                parts.append("可选值 " + " / ".join(arg.choices))
            if arg.default is not None:
                parts.append(f"默认 {arg.default}")
            lines.append(f"- {arg.name}（{'；'.join(parts)}）：{arg.description}")
    return "\n".join(lines)


# ── /status ──────────────────────────────────────────────────────────────────


async def cmd_status(ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    data: dict[str, Any] = {"session": None, "tasks": []}
    lines = ["## 会话状态"]

    if ctx.session_id:
        session = await _load_session(ctx)
        if session is None:
            raise CommandFailure(ERROR_SESSION_NOT_FOUND, "会话不存在或无权访问")
        message_count = await _count_messages(ctx, session.id)
        last = await _last_message(ctx, session.id)
        last_at = last.created_at.isoformat() if last and last.created_at else ""
        data["session"] = {
            "id": session.id,
            "title": session.title or "",
            "message_count": message_count,
            "last_message_at": last_at,
        }
        lines.append(f"- 会话：{session.title or session.id}（`{session.id}`）")
        lines.append(f"- 消息数：{message_count}")
        if last_at:
            lines.append(f"- 最近消息：{last_at}")
    else:
        lines.append("- 未指定会话（仅展示最近任务状态）")

    try:
        _total, tasks = await checkpoint.list_tasks(
            user_id=ctx.user_id,
            organization_id=ctx.organization_id,
            offset=0,
            limit=STATUS_TASK_LIMIT,
        )
    except Exception as exc:  # noqa: BLE001 — 状态查询降级不得拖垮整条命令
        logger.warning(
            "命令 /status 查询任务列表失败（user=%s）：%s", ctx.user_id, type(exc).__name__
        )
        lines.append("- 最近任务：暂不可用（任务存储查询失败）")
    else:
        if tasks:
            lines.append(f"- 最近任务（{len(tasks)} 条）：")
            for task in tasks:
                state = getattr(getattr(task, "status", None), "value", None) or str(
                    getattr(task, "status", "")
                )
                progress = getattr(task, "progress", 0.0) or 0.0
                updated = getattr(task, "updated_at", None)
                updated_text = updated.isoformat() if updated else ""
                data["tasks"].append(
                    {
                        "task_id": task.id,
                        "state": state,
                        "progress": progress,
                        "updated_at": updated_text,
                    }
                )
                lines.append(f"  - `{task.id}`：{state}（进度 {progress:.0%}）")
        else:
            lines.append("- 最近任务：无")

    return CommandResult(content="\n".join(lines), data=data)


# ── /export ──────────────────────────────────────────────────────────────────


def _safe_filename(title: str, session_id: str, ext: str) -> str:
    stem = (title or session_id).strip() or session_id
    for ch in ("/", "\\", ":", "*", "?", '"', "<", ">", "|"):
        stem = stem.replace(ch, "_")
    return f"{stem[:80]}.{ext}"


def _message_role(message: ChatMessage) -> str:
    message_type = getattr(message, "message_type", None)
    value = getattr(message_type, "value", str(message_type))
    return "user" if value == "user" else "assistant"


async def cmd_export(ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    if not ctx.session_id:
        raise CommandFailure(
            ERROR_SESSION_REQUIRED,
            "未指定会话，无法导出；请先打开一个会话再执行 /export",
            field_name="session_id",
        )
    session = await _load_session(ctx)
    if session is None:
        raise CommandFailure(ERROR_SESSION_NOT_FOUND, "会话不存在或无权访问")

    messages = await _load_messages(ctx, session.id)
    truncated = len(messages) > MAX_EXPORT_MESSAGES
    if truncated:
        messages = messages[:MAX_EXPORT_MESSAGES]
    # 查询为"新 → 旧"（截断保留最近 N 条），输出按时间正序（旧 → 新）。
    messages = list(reversed(messages))

    fmt = args.get("format") or "markdown"
    exported_at = datetime.now(UTC).isoformat(timespec="seconds")
    title = session.title or session.id

    if fmt == "json":
        payload = {
            "session_id": session.id,
            "title": title,
            "exported_at": exported_at,
            "message_count": len(messages),
            "truncated": truncated,
            "messages": [
                {
                    "role": _message_role(message),
                    "content": message.content,
                    "created_at": message.created_at.isoformat() if message.created_at else "",
                }
                for message in messages
            ],
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        ext = "json"
    else:
        lines = [
            f"# {title}",
            "",
            f"> 会话 id：{session.id}｜导出时间：{exported_at}｜消息数：{len(messages)}"
            + (f"（仅导出最近 {MAX_EXPORT_MESSAGES} 条）" if truncated else ""),
            "",
        ]
        for message in messages:
            created_at = message.created_at.isoformat() if message.created_at else ""
            role = "用户" if _message_role(message) == "user" else "助手"
            lines.extend(
                [
                    "---",
                    "",
                    f"**{role}**" + (f" · {created_at}" if created_at else ""),
                    "",
                    message.content,
                    "",
                ]
            )
        content = "\n".join(lines).rstrip() + "\n"
        ext = "md"

    return CommandResult(
        content=content,
        data={
            "format": fmt,
            "filename": _safe_filename(title, session.id, ext),
            "message_count": len(messages),
            "truncated": truncated,
        },
    )


# ── /tools ───────────────────────────────────────────────────────────────────


async def cmd_tools(ctx: CommandContext, args: dict[str, Any]) -> CommandResult:
    tools = tool_registry.list_tools()
    default_disabled = set(DEFAULT_DISABLED_TOOLS)
    entries = [
        {
            "name": tool.name,
            "description": tool.description,
            "tags": list(tool.tags),
            "requires_auth": tool.requires_auth,
            "disabled_by_default": tool.name in default_disabled,
        }
        for tool in tools
    ]
    lines = [f"## Agent 工具（{len(entries)}）"]
    for entry in entries:
        marks = []
        if entry["disabled_by_default"]:
            marks.append("默认禁用")
        if entry["requires_auth"]:
            marks.append("需授权")
        suffix = f"（{' / '.join(marks)}）" if marks else ""
        lines.append(f"- `{entry['name']}` — {entry['description']}{suffix}")
    return CommandResult(content="\n".join(lines), data={"total": len(entries), "tools": entries})


# ── 内置命令清单 ─────────────────────────────────────────────────────────────

BUILTIN_COMMANDS: tuple[Command, ...] = (
    Command(
        name="help",
        aliases=("h",),
        description="列出可用命令或查看某个命令的帮助",
        description_i18n={
            "zh": "列出可用命令或查看某个命令的帮助",
            "en": "List available commands or show help for one command",
        },
        usage="/help [命令名]",
        args=(CommandArg(name="command", description="要查看帮助的命令名（可省略）"),),
        handler=cmd_help,
    ),
    Command(
        name="status",
        description="查看当前会话与最近任务状态",
        description_i18n={
            "zh": "查看当前会话与最近任务状态",
            "en": "Show current session and recent task status",
        },
        usage="/status",
        handler=cmd_status,
    ),
    Command(
        name="export",
        description="导出当前会话（Markdown / JSON）",
        description_i18n={
            "zh": "导出当前会话（Markdown / JSON）",
            "en": "Export the current session (Markdown / JSON)",
        },
        usage="/export [markdown|json]",
        args=(
            CommandArg(
                name="format",
                description="导出格式",
                required=False,
                choices=("markdown", "json"),
                default="markdown",
            ),
        ),
        handler=cmd_export,
    ),
    Command(
        name="tools",
        description="列出 Agent 可用工具与默认禁用状态",
        description_i18n={
            "zh": "列出 Agent 可用工具与默认禁用状态",
            "en": "List available agent tools and default-disabled state",
        },
        usage="/tools",
        handler=cmd_tools,
    ),
)


def register_builtin_commands(registry) -> None:
    """把内置命令注册进全局作用域（幂等：已存在的同名命令跳过）。"""
    for command in BUILTIN_COMMANDS:
        if registry.resolve(command.name, agent_scope=None)[0] is not None:
            continue
        registry.register(command)
