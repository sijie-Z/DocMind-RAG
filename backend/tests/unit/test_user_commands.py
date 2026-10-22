"""issue #475 User Commands —— 注册表 / 解析 / 执行核心（无 LLM、无模型轮次）。

判别力（每个用例的「变异点」注明「改坏什么会让它红」）：
- 注册同层重名不报错 → duplicate 用例红；
- 别名不参与解析 → alias 用例红；
- 子作用域不遮蔽全局 / 列表出现重复项 → shadow 用例红；
- 列表不过滤管理员命令 → permission-list 用例红；
- 未知命令降级为普通提示词（不返回错误）→ unknown 用例红；
- 执行前不判权限 → admin-forbidden 用例红；
- 参数校验缺失 / 错误不带字段名 → field 用例红；
- 去掉同会话并发锁 → busy 用例红；
- 去掉超时 → timeout 用例红；
- 异常原文透传给用户 → no-leak 用例红；
- 去掉审计日志 → audit 用例红；
- 配置加载器遇到坏 JSON 抛出 → config 用例红。
"""

import asyncio
import json
import logging
from unittest.mock import MagicMock

import pytest

import app.agent.commands.execution as execution_mod
from app.agent.commands import (
    GLOBAL_SCOPE,
    Command,
    CommandArg,
    CommandContext,
    CommandRegistrationError,
    CommandRegistry,
    CommandResult,
    DuplicateCommandError,
    agent_scope_for_session,
    execute,
)
from app.agent.commands.builtins import register_builtin_commands
from app.agent.commands.config import (
    AgentCommandScopeConfig,
    CommandsConfig,
    apply_commands_config,
    load_commands_config,
)


async def _echo_handler(ctx, args):
    return CommandResult(content=f"echo:{args.get('value', '')}", data={"value": args.get("value")})


def _echo_command(name="echo", aliases=(), permission="user", required=True, timeout=10.0):
    return Command(
        name=name,
        description="回显参数",
        aliases=tuple(aliases),
        permission=permission,
        timeout=timeout,
        args=(CommandArg(name="value", description="要回显的值", required=required),),
        handler=_echo_handler,
    )


def _ctx(registry=None, session_id="s1", is_admin=False, **kwargs):
    ctx = CommandContext(
        user_id=7,
        organization_id=3,
        session_id=session_id,
        is_admin=is_admin,
        **kwargs,
    )
    ctx.registry = registry
    return ctx


# ── 注册 / 查找 / 遮蔽 ────────────────────────────────────────────────────────


def test_register_resolve_by_name_and_alias():
    registry = CommandRegistry()
    registry.register(_echo_command(aliases=("e",)))

    by_name, scope = registry.resolve("echo")
    by_alias, alias_scope = registry.resolve("E")  # 大小写归一
    assert by_name is not None and scope == GLOBAL_SCOPE
    assert by_alias is by_name and alias_scope == GLOBAL_SCOPE


def test_duplicate_name_same_scope_rejected():
    """变异点：注册时不做同名/别名冲突检查 → 本用例红。"""
    registry = CommandRegistry()
    registry.register(_echo_command())

    with pytest.raises(DuplicateCommandError):
        registry.register(_echo_command())

    other = _echo_command(name="other", aliases=("echo",))
    with pytest.raises(DuplicateCommandError):
        registry.register(other)


def test_agent_scope_shadows_global_for_same_name():
    """变异点：resolve 不先查 agent 作用域（直接查全局）→ 本用例红。"""
    registry = CommandRegistry()
    registry.register(_echo_command())
    scoped = _echo_command(name="echo", permission="user")
    scoped.description = "agent 作用域版本"
    registry.register(scoped, scope=agent_scope_for_session("s1"))

    scoped_cmd, scoped_scope = registry.resolve("echo", agent_scope=agent_scope_for_session("s1"))
    global_cmd, global_scope = registry.resolve("echo", agent_scope=agent_scope_for_session("s2"))
    assert scoped_cmd is scoped and scoped_scope == "session:s1"
    assert global_cmd is not None and global_scope == GLOBAL_SCOPE

    # 列表：agent 作用域只出现一份（遮蔽后不重复）
    names = [c.name for c in registry.list_commands(agent_scope=agent_scope_for_session("s1"))]
    assert names.count("echo") == 1


def test_agent_command_key_shadows_global_alias():
    """变异点：遮蔽只看 canonical name，不看别名 → 本用例红。"""
    registry = CommandRegistry()
    registry.register(_echo_command(aliases=("e",)))
    shadow = _echo_command(name="e")
    registry.register(shadow, scope="session:s9")

    cmd, scope = registry.resolve("e", agent_scope="session:s9")
    assert cmd is shadow and scope == "session:s9"
    # 遮蔽按整条命令生效：请求全局命令的 canonical name 也不得命中（否则
    # 会出现"列表隐藏、执行仍可达"的错位）。
    assert registry.resolve("echo", agent_scope="session:s9")[0] is None
    # 全局 echo 在该作用域不可达（别名被遮蔽），列表也不得出现
    listed = registry.list_commands(agent_scope="session:s9")
    assert [c.name for c in listed] == ["e"]


def test_invalid_command_names_rejected():
    registry = CommandRegistry()
    for bad in ("", "/help", "Has Space", "1st", "with/slash"):
        with pytest.raises(CommandRegistrationError):
            registry.register(Command(name=bad, description="", handler=_echo_handler))


def test_list_filters_admin_commands_for_non_admin():
    """变异点：list_commands 不按 permission 过滤 → 本用例红。"""
    registry = CommandRegistry()
    registry.register(_echo_command(name="public"))
    registry.register(_echo_command(name="admin-only", permission="admin"))

    user_view = [c.name for c in registry.list_commands(is_admin=False)]
    admin_view = [c.name for c in registry.list_commands(is_admin=True)]
    assert user_view == ["public"]
    assert admin_view == ["admin-only", "public"]


def test_blocked_command_hidden_in_agent_scope():
    """变异点：block 只影响被点名的键、或只影响解析不影响列表 → 本用例红。"""
    registry = CommandRegistry()
    registry.register(_echo_command(name="echo", aliases=("e",)))
    # 只禁用别名：整条全局命令在该作用域不可达（name 也不得命中）。
    registry.block("e", scope="session:blocked")

    assert registry.resolve("echo", agent_scope="session:blocked")[0] is None
    assert registry.resolve("e", agent_scope="session:blocked")[0] is None
    assert registry.list_commands(agent_scope="session:blocked") == []
    # 其它作用域不受影响
    assert registry.resolve("echo", agent_scope="session:other")[0] is not None


def test_unregister_removes_all_keys():
    registry = CommandRegistry()
    registry.register(_echo_command(aliases=("e",)))
    assert registry.unregister("e") is True
    assert registry.resolve("echo")[0] is None
    assert registry.resolve("e")[0] is None


# ── 参数解析 / 字段级错误 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_missing_argument_is_field_level():
    """变异点：缺参不报错（handler 收到空值）→ 本用例红。"""
    registry = CommandRegistry()
    registry.register(_echo_command())
    result = await execute(_ctx(), "/echo", registry=registry)
    assert result.success is False
    assert result.error_code == "missing_argument"
    assert result.error_field == "value"


@pytest.mark.asyncio
async def test_execute_unexpected_argument_is_field_level():
    registry = CommandRegistry()
    registry.register(_echo_command())
    result = await execute(_ctx(), "/echo a b", registry=registry)
    assert result.success is False
    assert result.error_code == "unexpected_argument"


@pytest.mark.asyncio
async def test_execute_invalid_choice_is_field_level():
    registry = CommandRegistry()
    cmd = Command(
        name="fmt",
        description="格式",
        args=(CommandArg(name="format", description="格式", required=False, choices=("a", "b")),),
        handler=_echo_handler,
    )
    registry.register(cmd)
    result = await execute(_ctx(), "/fmt z", registry=registry)
    assert result.success is False
    assert result.error_code == "invalid_argument" and result.error_field == "format"


@pytest.mark.asyncio
async def test_execute_choice_matching_is_case_insensitive():
    """变异点：choices 按原值比较 → `/fmt A` 被误拒（评审 n1）。"""
    registry = CommandRegistry()
    registry.register(
        Command(
            name="fmt",
            description="格式",
            args=(CommandArg(name="format", description="格式", choices=("a", "b")),),
            handler=_echo_handler,
        )
    )
    result = await execute(_ctx(), "/fmt A", registry=registry)
    assert result.success is True
    assert result.args == {"format": "a"}


@pytest.mark.asyncio
async def test_execute_applies_default_argument():
    registry = CommandRegistry()
    cmd = Command(
        name="fmt",
        description="格式",
        args=(
            CommandArg(
                name="format", description="格式", required=False, choices=("a", "b"), default="a"
            ),
        ),
        handler=_echo_handler,
    )
    registry.register(cmd)
    result = await execute(_ctx(), "/fmt", registry=registry)
    assert result.success is True
    assert result.args == {"format": "a"}


@pytest.mark.asyncio
async def test_execute_requires_slash():
    registry = CommandRegistry()
    result = await execute(_ctx(), "help", registry=registry)
    assert result.success is False
    assert result.error_code == "not_a_command"


@pytest.mark.asyncio
async def test_execute_empty_command_name():
    registry = CommandRegistry()
    result = await execute(_ctx(), "/   ", registry=registry)
    assert result.success is False
    assert result.error_code == "missing_command"


# ── 权限 / 未知命令 / 执行结果 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_unknown_command_returns_explicit_error():
    """变异点：未知命令降级成普通提示词/不返回错误 → 本用例红。"""
    registry = CommandRegistry()
    result = await execute(_ctx(), "/nope", registry=registry)
    assert result.success is False
    assert result.error_code == "unknown_command"
    assert "/help" in result.error_message


@pytest.mark.asyncio
async def test_execute_admin_command_rejected_for_non_admin():
    """变异点：执行前不判 permission → 本用例红。"""
    called = []

    async def _handler(ctx, args):
        called.append(True)
        return CommandResult(content="secret")

    registry = CommandRegistry()
    registry.register(
        Command(name="secret", description="管理命令", permission="admin", handler=_handler)
    )
    result = await execute(_ctx(is_admin=False), "/secret", registry=registry)
    assert result.success is False
    assert result.error_code == "permission_denied"
    assert called == []

    ok = await execute(_ctx(is_admin=True), "/secret", registry=registry)
    assert ok.success is True and ok.content == "secret"


@pytest.mark.asyncio
async def test_execute_success_returns_content_data_and_scope():
    registry = CommandRegistry()
    registry.register(_echo_command())
    result = await execute(_ctx(session_id=None), "/echo hi", registry=registry)
    assert result.success is True
    assert result.command == "echo"
    assert result.content == "echo:hi"
    assert result.data == {"value": "hi"}
    assert result.scope == GLOBAL_SCOPE
    assert result.duration_ms >= 0


@pytest.mark.asyncio
async def test_execute_handler_exception_never_leaks_raw_text(caplog):
    """变异点：把 str(exc) 直接外发给用户 → 本用例红。"""

    async def _boom(ctx, args):
        raise RuntimeError("DB password=super-secret")

    registry = CommandRegistry()
    registry.register(Command(name="boom", description="", handler=_boom))
    with caplog.at_level(logging.ERROR, logger="app.agent.commands.execution"):
        result = await execute(_ctx(), "/boom", registry=registry)
    assert result.success is False
    assert result.error_code == "command_failed"
    assert "super-secret" not in result.error_message
    assert any("boom" in record.getMessage() for record in caplog.records), "内部异常必须留痕"


@pytest.mark.asyncio
async def test_execute_timeout():
    """变异点：去掉 asyncio.wait_for → 本用例红（会等到 handler 完成）。"""

    async def _hang(ctx, args):
        await asyncio.sleep(5)
        return CommandResult(content="late")

    registry = CommandRegistry()
    registry.register(Command(name="hang", description="", handler=_hang, timeout=0.05))
    result = await execute(_ctx(), "/hang", registry=registry)
    assert result.success is False
    assert result.error_code == "command_timeout"


@pytest.mark.asyncio
async def test_execute_rejects_concurrent_command_on_same_session():
    """变异点：去掉同会话并发锁 → 第二个执行也成功（本用例红）。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(ctx, args):
        started.set()
        await release.wait()
        return CommandResult(content="slow-done")

    async def _fast(ctx, args):
        return CommandResult(content="fast-done")

    registry = CommandRegistry()
    registry.register(Command(name="slow", description="", handler=_slow))
    registry.register(Command(name="fast", description="", handler=_fast))

    first = asyncio.create_task(execute(_ctx(session_id="s1"), "/slow", registry=registry))
    await started.wait()
    busy = await execute(_ctx(session_id="s1"), "/fast", registry=registry)
    assert busy.success is False
    assert busy.error_code == "command_busy"

    other_session = await execute(_ctx(session_id="s2"), "/fast", registry=registry)
    assert other_session.success is True, "不同会话不得互相阻塞"

    release.set()
    assert (await first).success is True


@pytest.mark.asyncio
async def test_execute_audit_log_records_identity_and_outcome(caplog):
    """变异点：删除审计日志行 → 本用例红。"""
    registry = CommandRegistry()
    registry.register(_echo_command())
    with caplog.at_level(logging.INFO, logger="app.agent.commands.execution"):
        result = await execute(_ctx(session_id="sess-42"), "/echo hi", registry=registry)
    assert result.success is True
    audit = [r.getMessage() for r in caplog.records if "command=echo" in r.getMessage()]
    assert audit, f"缺少审计日志：{[r.getMessage() for r in caplog.records]}"
    line = audit[0]
    assert "user=7" in line and "org=3" in line and "session=sess-42" in line
    assert "success=True" in line


@pytest.mark.asyncio
async def test_execute_cancellation_propagates_and_releases_session_lock():
    """变异点：吞掉 CancelledError 或取消后不释放锁 → 本用例红。"""
    started = asyncio.Event()

    async def _slow(ctx, args):
        started.set()
        await asyncio.sleep(5)
        return CommandResult(content="late")

    async def _fast(ctx, args):
        return CommandResult(content="fast-done")

    registry = CommandRegistry()
    registry.register(Command(name="slow", description="", handler=_slow))
    registry.register(Command(name="fast", description="", handler=_fast))

    task = asyncio.create_task(execute(_ctx(session_id="s-cancel"), "/slow", registry=registry))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # 取消后锁必须释放：同一会话可立即执行下一条命令（不得永远 busy）。
    follow_up = await execute(_ctx(session_id="s-cancel"), "/fast", registry=registry)
    assert follow_up.success is True


@pytest.mark.asyncio
async def test_execute_emits_command_metric_with_result_code(monkeypatch):
    """变异点：_audit 不埋指标 → 本用例红（issue #475 任务 5：失败可查）。"""
    registry = CommandRegistry()
    registry.register(_echo_command())

    spy = MagicMock()
    monkeypatch.setattr(execution_mod, "AGENT_COMMAND_EXECUTIONS", spy)
    assert (await execute(_ctx(), "/echo hi", registry=registry)).success is True
    spy.labels.assert_called_once_with(command="echo", result="success")
    spy.labels.return_value.inc.assert_called_once()

    spy2 = MagicMock()
    monkeypatch.setattr(execution_mod, "AGENT_COMMAND_EXECUTIONS", spy2)
    assert (await execute(_ctx(), "/nope", registry=registry)).success is False
    spy2.labels.assert_called_once_with(command="unknown", result="unknown_command")


# ── 配置化加载（agents 作用域 / 全局禁用）────────────────────────────────────


def test_config_loader_applies_agent_scopes_and_global_disables(tmp_path):
    """变异点：配置加载不生效（只读不用）→ 本用例红。"""
    config_path = tmp_path / "commands.json"
    config_path.write_text(
        json.dumps(
            {
                "disabled": ["tools"],
                "agents": {
                    "session:demo": {"enable": ["export"], "disable": ["help"]},
                    "session:empty": {},
                },
            }
        ),
        encoding="utf-8",
    )
    config = load_commands_config(config_path)
    registry = CommandRegistry()
    register_builtin_commands(registry)
    apply_commands_config(registry, config)

    assert registry.resolve("tools")[0] is None, "全局 disabled 必须生效"
    assert registry.resolve("help", agent_scope="session:demo")[0] is None
    scoped, scope = registry.resolve("export", agent_scope="session:demo")
    assert scoped is not None and scope == "session:demo"
    global_export, global_scope = registry.resolve("export")
    assert global_export is not None and global_scope == GLOBAL_SCOPE


def test_config_loader_env_path_override(tmp_path, monkeypatch):
    """变异点：忽略 DOCMIND_COMMANDS_CONFIG → 本用例红（会读默认路径/空配置）。"""
    custom = tmp_path / "custom-commands.json"
    custom.write_text(json.dumps({"disabled": ["help"]}), encoding="utf-8")
    monkeypatch.setenv("DOCMIND_COMMANDS_CONFIG", str(custom))
    config = load_commands_config()  # no path → env override
    assert config.disabled == ["help"]


def test_config_loader_never_raises_on_bad_json(tmp_path, caplog):
    """变异点：坏配置抛异常（启动路径炸掉）→ 本用例红。"""
    bad = tmp_path / "commands.json"
    bad.write_text("{not json", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        config = load_commands_config(bad)
    assert config.disabled == [] and config.agents == {}
    assert any("commands" in r.getMessage().lower() for r in caplog.records)


def test_config_loader_never_raises_on_non_utf8_file(tmp_path, caplog):
    """变异点：只捕获 OSError（漏 UnicodeDecodeError）→ 本用例红（评审 m5）。"""
    bad = tmp_path / "commands.json"
    bad.write_bytes(b"\xff\xfe{not utf8")
    with caplog.at_level(logging.WARNING):
        config = load_commands_config(bad)
    assert config.disabled == [] and config.agents == {}
    assert caplog.records


def test_config_loader_skips_invalid_entries(tmp_path, caplog):
    missing = tmp_path / "commands.json"
    missing.write_text(json.dumps({"agents": {"": {"enable": ["help"]}}}), encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        config = load_commands_config(missing)
    assert config.agents == {}
    assert caplog.records, "非法条目必须留 warning，不得静默"


def test_config_enable_wins_over_disable_in_same_scope():
    """配置优先级（评审 m4）：enable 的 scoped 命令优先于全局/disable。"""
    registry = CommandRegistry()
    register_builtin_commands(registry)
    apply_commands_config(
        registry,
        CommandsConfig(
            agents={"session:x": AgentCommandScopeConfig(enable=["help"], disable=["help"])}
        ),
    )
    command, scope = registry.resolve("help", agent_scope="session:x")
    assert command is not None and scope == "session:x"
    listed = registry.list_commands(agent_scope="session:x")
    assert sum(1 for item in listed if item.name == "help") == 1


def test_agent_scope_key_is_stable():
    assert agent_scope_for_session("abc") == "session:abc"
