"""issue #475 User Commands —— 注册表 / 解析 / 执行核心（无 LLM、无模型轮次）。

issue #498 增补：Redis 锁 + 幂等键。本机无 Redis，测试用最小 FakeRedis
（SET NX EX / GET / DELETE / Lua compare-and-del 语义）替代；两个 execute
调用共享同一个 FakeRedis 实例模拟两个 worker 的外部共享存储。

判别力（每个用例的「变异点」注明「改坏什么会让它红」）：
- 注册同层重名不报错 → duplicate 用例红；
- 别名不参与解析 → alias 用例红；
- 子作用域不遮蔽全局 / 列表出现重复项 → shadow 用例红；
- 列表不过滤管理员命令 → permission-list 用例红；
- 未知命令降级为普通提示词（不返回错误）→ unknown 用例红；
- 执行前不判权限 → admin-forbidden 用例红；
- 参数校验缺失 / 错误不带字段名 → field 用例红；
- 裸命令名不做前导 / 归一 → bare-name 用例红（issue #498）；
- 去掉 Redis 锁 → busy 用例红；去掉 Lua compare-and-del → 锁残留/误删用例红；
- 去掉 idempotency SET NX → 同 key 两次执行两次、execution_id 不同（用例红）；
- Redis 不可用时不 fail closed → service-unavailable 用例红；
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
    CommandFailure,
    CommandRegistrationError,
    CommandRegistry,
    CommandResult,
    CommandSyntaxError,
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
from app.agent.commands.parser import compose_line
from app.core.config import settings


class FakeRedis:
    """最小 Redis 替身（issue #498）：只实现命令执行用到的原语。

    - ``set(nx=True)`` 从检查到写入没有 await 间隙，在单事件循环里等价原子；
    - ``eval`` 只识别 execution.py 的 Lua compare-and-del 脚本（并校验脚本文本，
      把"改成无条件 DEL"的变异变成测试红）；
    - TTL 不真正过期（单测时长远小于 TTL），但记录每次 SET 的 ex 供断言。
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.set_calls: list[dict] = []
        self.get_calls: list[str] = []

    def __bool__(self):
        return True

    async def set(self, key, value, nx=False, ex=None, **kwargs):
        self.set_calls.append({"key": key, "value": value, "nx": nx, "ex": ex})
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        self.get_calls.append(key)
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def eval(self, script, numkeys, *args):
        assert 'redis.call("get", KEYS[1]) == ARGV[1]' in script, (
            "Lua 必须先比较 owner；无条件写入/删除会覆盖或误删新持有者的值"
        )
        key, token = args[0], args[1]
        if 'redis.call("set", KEYS[1], ARGV[2]' in script:
            value, ttl = args[2], int(args[3])
            if self.store.get(key) == token:
                await self.set(key, value, ex=ttl)
                return 1
            return 0
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    """默认给所有执行用例注入共享 FakeRedis；个别用例可覆盖为 None。"""
    client = FakeRedis()
    monkeypatch.setattr(execution_mod, "redis_client", client)
    return client


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
        organization_id=kwargs.pop("organization_id", 3),
        session_id=session_id,
        is_admin=is_admin,
        **kwargs,
    )
    ctx.registry = registry
    return ctx


def _allow_replay(calls=None):
    async def _authorize(organization_id, session_id, resource_org_id):
        if calls is not None:
            calls.append((organization_id, session_id, resource_org_id))

    return _authorize


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
async def test_execute_accepts_bare_command_name():
    """issue #498：``help`` 与 ``/help`` 等价（前导 / 可选，内部归一）。"""
    registry = CommandRegistry()
    registry.register(_echo_command())
    result = await execute(_ctx(), "echo hi", registry=registry)
    assert result.success is True
    assert result.command == "echo"
    assert result.content == "echo:hi"

    slashed = await execute(_ctx(), "/echo hi", registry=registry)
    assert slashed.success is True and slashed.content == result.content


@pytest.mark.asyncio
async def test_execute_empty_command_name():
    registry = CommandRegistry()
    result = await execute(_ctx(), "/   ", registry=registry)
    assert result.success is False
    assert result.error_code == "missing_command"


def test_compose_line_mapping_requires_contiguous_prefix():
    """评审 m2：映射 args 提供后面的参数时，前面的位置参数必须有值/默认值。"""
    required_first = (
        CommandArg(name="a", description="a", required=True),
        CommandArg(name="b", description="b"),
    )
    assert compose_line("two", {"a": "A", "b": "B"}, arg_specs=required_first) == "/two A B"
    with pytest.raises(CommandSyntaxError) as excinfo:
        compose_line("two", {"b": "B"}, arg_specs=required_first)
    assert excinfo.value.code == "missing_argument"
    assert excinfo.value.field_name == "a"

    default_first = (
        CommandArg(name="a", description="a", default="x"),
        CommandArg(name="b", description="b"),
    )
    assert compose_line("two", {"b": "B"}, arg_specs=default_first) == "/two x B"
    with pytest.raises(CommandSyntaxError):
        compose_line("two", {"nope": "x"}, arg_specs=default_first)


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
async def test_execute_envelope_has_execution_id_status_result():
    """issue #498：响应信封 execution_id/status/result（保留旧字段）。"""
    registry = CommandRegistry()
    registry.register(_echo_command())

    ok = await execute(_ctx(session_id=None), "/echo hi", registry=registry)
    payload = ok.to_dict()
    assert payload["execution_id"] == ok.execution_id
    assert len(payload["execution_id"]) == 32, "execution_id 必须是 uuid hex"
    assert payload["status"] == "completed"
    assert payload["result"] == {"content": "echo:hi", "data": {"value": "hi"}}
    # 旧字段保留一版兼容
    assert payload["content"] == "echo:hi" and payload["data"] == {"value": "hi"}

    failed = await execute(_ctx(session_id=None), "/nope", registry=registry)
    failed_payload = failed.to_dict()
    assert failed_payload["status"] == "failed"
    assert failed_payload["result"] is None
    assert failed_payload["execution_id"] and failed_payload["execution_id"] != ok.execution_id


@pytest.mark.asyncio
async def test_execute_fails_closed_when_redis_unavailable(monkeypatch):
    """变异点：Redis 不可用时退回进程内锁/放行 → 本用例红（issue #498 fail-closed）。"""
    monkeypatch.setattr(execution_mod, "redis_client", None)
    registry = CommandRegistry()
    registry.register(_echo_command())
    result = await execute(_ctx(), "/echo hi", registry=registry)
    assert result.success is False
    assert result.error_code == "service_unavailable"
    assert result.data.get("reason") == "redis_unavailable"


@pytest.mark.asyncio
async def test_idempotency_key_returns_same_execution_and_runs_handler_once():
    """变异点：去掉幂等 SET NX → 同 key 两次都执行（本用例红，issue #498）。"""
    calls: list[int] = []

    async def _count(ctx, args):
        calls.append(len(calls) + 1)
        return CommandResult(content=f"run-{len(calls)}", data={"n": len(calls)})

    registry = CommandRegistry()
    registry.register(Command(name="count", description="", handler=_count))

    first = await execute(
        _ctx(session_id=None), "/count", registry=registry, idempotency_key="key-1"
    )
    second = await execute(
        _ctx(session_id=None), "/count", registry=registry, idempotency_key="key-1"
    )
    assert first.success is True and first.content == "run-1"
    assert second.success is True
    assert second.execution_id == first.execution_id, "重复请求必须返回同一 execution_id"
    assert second.content == "run-1"
    assert calls == [1], "handler 只允许执行一次"


@pytest.mark.asyncio
async def test_idempotency_key_is_scoped_per_user(fake_redis):
    """变异点：幂等键不带 user 前缀 → 跨用户互相顶掉（本用例红）。"""
    calls: list[int] = []

    async def _count(ctx, args):
        calls.append(ctx.user_id)
        return CommandResult(content="ok")

    registry = CommandRegistry()
    registry.register(Command(name="count", description="", handler=_count))

    ctx_a = _ctx(session_id=None)
    ctx_b = _ctx(session_id=None)
    ctx_b.user_id = 8
    a = await execute(ctx_a, "/count", registry=registry, idempotency_key="same-key")
    b = await execute(ctx_b, "/count", registry=registry, idempotency_key="same-key")
    assert a.execution_id != b.execution_id
    assert calls == [7, 8]
    assert any(key.startswith("cmd:idem:7:") for key in fake_redis.store)
    assert any(key.startswith("cmd:idem:8:") for key in fake_redis.store)


@pytest.mark.asyncio
async def test_idempotency_failure_is_not_cached_and_can_retry():
    """失败不缓存：同 key 的未知命令失败后，重试可正常执行。"""
    registry = CommandRegistry()
    registry.register(_echo_command())

    failed = await execute(_ctx(), "/nope", registry=registry, idempotency_key="retry")
    assert failed.success is False and failed.error_code == "unknown_command"
    retried = await execute(_ctx(), "/echo hi", registry=registry, idempotency_key="retry")
    assert retried.success is True and retried.content == "echo:hi"
    assert retried.execution_id != failed.execution_id


@pytest.mark.asyncio
async def test_idempotency_cleanup_keeps_new_claimant_after_pending_takeover(fake_redis):
    """评审边界：pending 过期被新请求接管后，旧请求的失败清理不得误删新占位。"""
    idem_key = "cmd:idem:7:race-key"

    async def _takeover_then_fail(ctx, args):
        # 模拟 pending 已过期、新请求已用同一 key 写入自己的 v2 占位。
        fake_redis.store[idem_key] = json.dumps(
            {
                "version": 2,
                "state": "pending",
                "fingerprint": "f" * 64,
                "organization_id": 3,
                "session_id": None,
                "resource_org_id": None,
                "command": "race",
                "scope": "global",
                "owner": "new-claimant",
            }
        )
        raise CommandFailure("command_failed", "boom")

    registry = CommandRegistry()
    registry.register(Command(name="race", description="", handler=_takeover_then_fail))
    result = await execute(
        _ctx(session_id=None), "/race", registry=registry, idempotency_key="race-key"
    )
    assert result.success is False and result.error_code == "command_failed"
    assert json.loads(fake_redis.store[idem_key])["owner"] == "new-claimant"


@pytest.mark.asyncio
async def test_idempotent_replay_rechecks_admin_permission(monkeypatch):
    """降权重放须先重新加载原 session，再按当前角色拒绝且不反序列化正文。"""
    calls: list[int] = []
    authorizations = []

    async def _secret(ctx, args):
        calls.append(1)
        return CommandResult(content="secret")

    registry = CommandRegistry()
    registry.register(
        Command(name="secret", description="管理命令", permission="admin", handler=_secret)
    )
    first = await execute(
        _ctx(is_admin=True),
        "/secret",
        registry=registry,
        idempotency_key="admin-key",
    )
    assert first.success is True and first.content == "secret"

    deserialize = MagicMock(side_effect=AssertionError("权限检查前不得反序列化缓存正文"))
    monkeypatch.setattr(execution_mod.CommandExecution, "from_dict", deserialize)
    replay = await execute(
        _ctx(is_admin=False),
        "/secret",
        registry=registry,
        idempotency_key="admin-key",
        replay_authorizer=_allow_replay(authorizations),
    )
    assert replay.success is False and replay.error_code == "permission_denied"
    assert calls == [1], "缓存命中也不得重跑 handler"
    assert authorizations == [(3, "s1", None)]
    deserialize.assert_not_called()


@pytest.mark.asyncio
async def test_admin_denial_with_idempotency_key_is_not_cached(fake_redis):
    calls = []

    async def _secret(ctx, args):
        calls.append(1)
        return CommandResult(content="secret")

    registry = CommandRegistry()
    registry.register(Command(name="secret", description="", permission="admin", handler=_secret))
    denied = await execute(
        _ctx(session_id=None), "/secret", registry=registry, idempotency_key="admin-retry"
    )
    assert denied.success is False and denied.error_code == "permission_denied"
    assert "cmd:idem:7:admin-retry" not in fake_redis.store
    allowed = await execute(
        _ctx(session_id=None, is_superuser=True),
        "/secret",
        registry=registry,
        idempotency_key="admin-retry",
    )
    assert allowed.success is True and allowed.content == "secret"
    assert calls == [1]


@pytest.mark.asyncio
async def test_idempotency_pending_uses_short_ttl_and_result_uses_24h(fake_redis):
    """评审 M2：pending 用短 TTL（崩溃自愈）；最终结果用 24h TTL。"""
    registry = CommandRegistry()
    registry.register(_echo_command())
    await execute(_ctx(session_id=None), "/echo hi", registry=registry, idempotency_key="ttl-key")
    calls = [call for call in fake_redis.set_calls if call["key"] == "cmd:idem:7:ttl-key"]
    assert len(calls) == 2
    assert calls[0]["nx"] is True, "pending 必须 SET NX 抢占"
    assert calls[0]["ex"] == execution_mod.idempotency_pending_ttl()
    assert calls[0]["ex"] < settings.COMMANDS_IDEMPOTENCY_TTL_SECONDS, (
        "pending 不得沿用 24h TTL（worker 崩溃会锁死同一幂等键）"
    )
    assert calls[1]["nx"] is False and calls[1]["ex"] == settings.COMMANDS_IDEMPOTENCY_TTL_SECONDS


@pytest.mark.asyncio
async def test_idempotency_v2_normalizes_alias_defaults_and_reauthorizes(fake_redis, monkeypatch):
    """别名、前导斜杠和默认参数同指纹；重放先复核原 session 再取正文。"""
    idem_key = "cmd:idem:7:normalized"
    pending = []
    handler_calls = []
    order = []

    async def _handler(ctx, args):
        pending.append(json.loads(fake_redis.store[idem_key]))
        handler_calls.append(args)
        return CommandResult(content=f"export:{args['format']}")

    registry = CommandRegistry()
    registry.register(
        Command(
            name="echo",
            aliases=("e",),
            description="",
            args=(
                CommandArg(
                    name="format",
                    description="",
                    choices=("markdown", "json"),
                    default="markdown",
                ),
            ),
            handler=_handler,
        )
    )
    first = await execute(
        _ctx(),
        "/e",
        registry=registry,
        idempotency_key="normalized",
        session_resource_org_id=3,
    )
    assert first.success is True
    completed = json.loads(fake_redis.store[idem_key])
    expected = {
        "version": 2,
        "fingerprint": pending[0]["fingerprint"],
        "organization_id": 3,
        "session_id": "s1",
        "resource_org_id": 3,
        "command": "echo",
        "scope": "global",
        "owner": pending[0]["owner"],
    }
    assert pending[0]["state"] == "pending"
    assert all(pending[0][key] == value for key, value in expected.items())
    assert completed["state"] == "completed"
    assert all(completed[key] == value for key, value in expected.items())
    assert completed["execution"]["execution_id"] == first.execution_id
    assert completed["execution"]["args"] == {"format": "markdown"}

    original_from_dict = execution_mod.CommandExecution.from_dict

    async def _authorize(organization_id, session_id, resource_org_id):
        order.append("authorize")
        assert (organization_id, session_id, resource_org_id) == (3, "s1", 3)

    def _deserialize(payload):
        order.append("deserialize")
        return original_from_dict(payload)

    monkeypatch.setattr(execution_mod.CommandExecution, "from_dict", _deserialize)
    replay = await execute(
        _ctx(),
        "echo markdown",
        registry=registry,
        idempotency_key="normalized",
        session_resource_org_id=3,
        replay_authorizer=_authorize,
    )
    assert replay.success is True and replay.execution_id == first.execution_id
    assert replay.content == "export:markdown"
    assert handler_calls == [{"format": "markdown"}]
    assert order == ["authorize", "deserialize"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("line", "organization_id", "session_id"),
    [
        ("/echo one", 4, "s1"),
        ("/echo one", 3, "s2"),
        ("/echo one", 3, None),
        ("/other one", 3, "s1"),
        ("/echo two", 3, "s1"),
    ],
    ids=["org", "session", "missing-session", "command", "args"],
)
async def test_idempotency_fingerprint_rejects_every_context_change(
    line, organization_id, session_id
):
    calls = []

    async def _handler(ctx, args):
        calls.append((ctx.organization_id, ctx.session_id, args["value"]))
        return CommandResult(content="PRIVATE-CONTENT")

    registry = CommandRegistry()
    command = _echo_command(aliases=("e",))
    command.handler = _handler
    registry.register(command)
    other = _echo_command(name="other")
    other.handler = _handler
    registry.register(other)
    first = await execute(_ctx(), "/echo one", registry=registry, idempotency_key="context")
    replay = await execute(
        _ctx(organization_id=organization_id, session_id=session_id),
        line,
        registry=registry,
        idempotency_key="context",
        replay_authorizer=_allow_replay(),
    )
    assert first.success is True
    assert replay.success is False and replay.error_code == "command_busy"
    assert "PRIVATE-CONTENT" not in replay.error_message + replay.content
    assert calls == [(3, "s1", "one")]


@pytest.mark.asyncio
async def test_idempotency_fingerprint_includes_resolved_scope(fake_redis):
    calls = []

    async def _handler(ctx, args):
        calls.append(1)
        return CommandResult(content="PRIVATE-CONTENT")

    registry = CommandRegistry()
    registry.register(Command(name="echo", description="", handler=_handler))
    first = await execute(_ctx(), "/echo", registry=registry, idempotency_key="scope")
    registry.register(
        Command(name="echo", description="", handler=_handler),
        scope=agent_scope_for_session("s1"),
    )
    replay = await execute(
        _ctx(),
        "/echo",
        registry=registry,
        idempotency_key="scope",
        replay_authorizer=_allow_replay(),
    )
    assert first.success is True
    assert replay.success is False and replay.error_code == "command_busy"
    assert calls == [1]


@pytest.mark.asyncio
async def test_legacy_completed_entry_fails_closed_without_deserializing(fake_redis, monkeypatch):
    idem_key = "cmd:idem:7:legacy-completed"
    legacy = execution_mod.CommandExecution(
        command="echo",
        line="/echo",
        success=True,
        content="LEGACY-SECRET",
        scope="global",
        execution_id="legacy-execution",
    )
    fake_redis.store[idem_key] = json.dumps(legacy.to_dict())
    deserialize = MagicMock(side_effect=AssertionError("legacy 不得反序列化"))
    monkeypatch.setattr(execution_mod.CommandExecution, "from_dict", deserialize)
    calls = []

    async def _handler(ctx, args):
        calls.append(1)
        return CommandResult(content="NEW-HANDLER")

    registry = CommandRegistry()
    registry.register(Command(name="echo", description="", handler=_handler))
    replay = await execute(
        _ctx(session_id=None), "/echo", registry=registry, idempotency_key="legacy-completed"
    )
    assert replay.success is False and replay.error_code == "command_busy"
    assert "LEGACY-SECRET" not in replay.error_message + replay.content
    assert calls == []
    deserialize.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        "{not-json",
        "[]",
        json.dumps({"state": "pending", "execution_id": "legacy"}),
        json.dumps(
            {
                "version": 2,
                "state": "pending",
                "fingerprint": "0" * 64,
                "organization_id": 3,
                "session_id": None,
                "resource_org_id": None,
                "command": "echo",
                "scope": "global",
            }
        ),
        json.dumps({"version": 1, "state": "completed", "status": "completed"}),
    ],
    ids=["bad-json", "wrong-type", "legacy-pending", "missing-owner", "old-version"],
)
async def test_corrupt_or_legacy_entries_fail_closed_immediately(fake_redis, raw):
    idem_key = "cmd:idem:7:corrupt"
    fake_redis.store[idem_key] = raw
    calls = []

    async def _handler(ctx, args):
        calls.append(1)
        return CommandResult(content="MUST-NOT-RUN")

    registry = CommandRegistry()
    registry.register(Command(name="echo", description="", handler=_handler))
    result = await execute(
        _ctx(session_id=None), "/echo", registry=registry, idempotency_key="corrupt"
    )
    assert result.success is False and result.error_code == "command_busy"
    assert calls == []
    assert fake_redis.get_calls == [idem_key]


@pytest.mark.asyncio
async def test_internally_inconsistent_completed_entry_fails_closed(fake_redis, monkeypatch):
    registry = CommandRegistry()
    registry.register(_echo_command())
    first = await execute(
        _ctx(session_id=None), "/echo one", registry=registry, idempotency_key="inconsistent"
    )
    assert first.success is True
    idem_key = "cmd:idem:7:inconsistent"
    payload = json.loads(fake_redis.store[idem_key])
    payload["execution"]["args"]["value"] = "tampered"
    fake_redis.store[idem_key] = json.dumps(payload)
    deserialize = MagicMock(side_effect=AssertionError("不一致信封不得反序列化"))
    monkeypatch.setattr(execution_mod.CommandExecution, "from_dict", deserialize)

    replay = await execute(
        _ctx(session_id=None), "/echo one", registry=registry, idempotency_key="inconsistent"
    )
    assert replay.success is False and replay.error_code == "command_busy"
    deserialize.assert_not_called()


@pytest.mark.asyncio
async def test_completed_org_recheck_is_independent_of_fingerprint(fake_redis):
    registry = CommandRegistry()
    registry.register(_echo_command())
    first = await execute(
        _ctx(session_id=None), "/echo one", registry=registry, idempotency_key="org-check"
    )
    assert first.success is True
    idem_key = "cmd:idem:7:org-check"
    payload = json.loads(fake_redis.store[idem_key])
    payload["organization_id"] = 999
    fake_redis.store[idem_key] = json.dumps(payload)

    replay = await execute(
        _ctx(session_id=None), "/echo one", registry=registry, idempotency_key="org-check"
    )
    assert replay.success is False and replay.error_code == "command_busy"
    assert replay.content == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("second_line", ["/other", "/slow changed"], ids=["command", "args"])
async def test_pending_different_fingerprint_conflicts_without_polling(fake_redis, second_line):
    started = asyncio.Event()
    release = asyncio.Event()
    slow_calls = []
    other_calls = []

    async def _slow(ctx, args):
        slow_calls.append(1)
        started.set()
        await release.wait()
        return CommandResult(content="slow")

    async def _other(ctx, args):
        other_calls.append(1)
        return CommandResult(content="other")

    registry = CommandRegistry()
    registry.register(
        Command(
            name="slow",
            description="",
            args=(CommandArg(name="value", description=""),),
            handler=_slow,
        )
    )
    registry.register(Command(name="other", description="", handler=_other))
    first = asyncio.create_task(
        execute(_ctx(session_id=None), "/slow", registry=registry, idempotency_key="pending-diff")
    )
    await started.wait()
    fake_redis.get_calls.clear()
    conflict = await execute(
        _ctx(session_id=None), second_line, registry=registry, idempotency_key="pending-diff"
    )
    release.set()
    assert (await first).success is True
    assert conflict.success is False and conflict.error_code == "command_busy"
    assert fake_redis.get_calls == ["cmd:idem:7:pending-diff"]
    assert other_calls == []
    assert slow_calls == [1]


@pytest.mark.asyncio
async def test_matching_pending_completed_replay_rechecks_original_session(fake_redis, monkeypatch):
    monkeypatch.setattr(execution_mod, "_IDEM_POLL_INTERVAL", 0.001)
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []
    authorizations = []

    async def _slow(ctx, args):
        calls.append(1)
        started.set()
        await release.wait()
        return CommandResult(content="done")

    registry = CommandRegistry()
    registry.register(Command(name="slow", description="", handler=_slow))
    first_task = asyncio.create_task(
        execute(_ctx(), "/slow", registry=registry, idempotency_key="pending-same")
    )
    await started.wait()
    fake_redis.get_calls.clear()
    replay_task = asyncio.create_task(
        execute(
            _ctx(),
            "/slow",
            registry=registry,
            idempotency_key="pending-same",
            replay_authorizer=_allow_replay(authorizations),
        )
    )
    while not fake_redis.get_calls:
        await asyncio.sleep(0)
    release.set()
    first, replay = await asyncio.gather(first_task, replay_task)
    assert first.success is True and replay.success is True
    assert replay.execution_id == first.execution_id
    assert calls == [1]
    assert authorizations == [(3, "s1", None)]


@pytest.mark.asyncio
async def test_old_owner_cannot_overwrite_new_claimant_completed_value(fake_redis):
    idem_key = "cmd:idem:7:completed-race"
    new_owner = json.dumps(
        {
            "version": 2,
            "state": "pending",
            "fingerprint": "a" * 64,
            "organization_id": 3,
            "session_id": None,
            "resource_org_id": None,
            "command": "race",
            "scope": "global",
            "owner": "new-owner",
        }
    )

    async def _takeover(ctx, args):
        fake_redis.store[idem_key] = new_owner
        return CommandResult(content="old-owner-result")

    registry = CommandRegistry()
    registry.register(Command(name="race", description="", handler=_takeover))
    result = await execute(
        _ctx(session_id=None), "/race", registry=registry, idempotency_key="completed-race"
    )
    assert result.success is True
    assert fake_redis.store[idem_key] == new_owner


@pytest.mark.asyncio
async def test_completed_write_failure_cannot_become_handler_retry(fake_redis, monkeypatch):
    class FailCompletedWriteRedis(FakeRedis):
        async def eval(self, script, numkeys, *args):
            if 'redis.call("set", KEYS[1], ARGV[2]' in script:
                raise ConnectionError("redis down")
            return await super().eval(script, numkeys, *args)

    client = FailCompletedWriteRedis()
    calls = []

    async def _handler(ctx, args):
        calls.append(1)
        return CommandResult(content="done")

    registry = CommandRegistry()
    registry.register(Command(name="count", description="", handler=_handler))
    first = await execute(
        _ctx(session_id=None),
        "/count",
        registry=registry,
        idempotency_key="write-down",
        redis=client,
    )
    assert first.success is True
    monkeypatch.setattr(execution_mod, "_IDEM_POLL_ATTEMPTS", 2)
    monkeypatch.setattr(execution_mod, "_IDEM_POLL_INTERVAL", 0)
    replay = await execute(
        _ctx(session_id=None),
        "/count",
        registry=registry,
        idempotency_key="write-down",
        redis=client,
    )
    assert replay.success is False and replay.error_code == "command_busy"
    assert calls == [1]
    assert json.loads(client.store["cmd:idem:7:write-down"])["state"] == "pending"


@pytest.mark.asyncio
async def test_session_replay_without_authorizer_fails_closed(fake_redis):
    registry = CommandRegistry()
    registry.register(_echo_command())
    first = await execute(_ctx(), "/echo one", registry=registry, idempotency_key="no-guard")
    assert first.success is True
    replay = await execute(_ctx(), "/echo one", registry=registry, idempotency_key="no-guard")
    assert replay.success is False and replay.error_code == "service_unavailable"
    assert replay.content == ""


@pytest.mark.asyncio
async def test_idempotency_read_storage_error_is_503_not_cache_miss(fake_redis):
    class BrokenGetRedis(FakeRedis):
        async def get(self, key):
            raise ConnectionError("redis down")

    client = BrokenGetRedis()
    client.store["cmd:idem:7:read-down"] = "not-a-cache-miss"
    calls = []

    async def _handler(ctx, args):
        calls.append(1)
        return CommandResult(content="MUST-NOT-RUN")

    registry = CommandRegistry()
    registry.register(Command(name="echo", description="", handler=_handler))
    result = await execute(
        _ctx(session_id=None), "/echo", registry=registry, idempotency_key="read-down", redis=client
    )
    assert result.success is False and result.error_code == "service_unavailable"
    assert calls == []


@pytest.mark.asyncio
async def test_lock_release_compare_and_del_keeps_new_owners_lock(fake_redis):
    """变异点：Lua 改成无条件 DEL → 本用例红（误删 TTL 过期后新持有者的锁）。"""
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(ctx, args):
        started.set()
        await release.wait()
        return CommandResult(content="slow-done")

    registry = CommandRegistry()
    registry.register(Command(name="slow", description="", handler=_slow))
    task = asyncio.create_task(
        execute(_ctx(session_id="s-cas"), "/slow", registry=registry, redis=fake_redis)
    )
    await started.wait()
    lock_key = execution_mod.lock_redis_key(_ctx(session_id="s-cas"))
    fake_redis.store[lock_key] = "new-owner-token"  # 模拟 TTL 过期后新 owner 接管
    release.set()
    assert (await task).success is True
    assert fake_redis.store[lock_key] == "new-owner-token", "旧 owner 不得误删新锁"


@pytest.mark.asyncio
async def test_audit_log_escapes_control_chars_in_line(caplog):
    """评审 minor：用户可控 line 不得在审计日志里伪造新行（CWE-117）。"""
    registry = CommandRegistry()
    with caplog.at_level(logging.INFO, logger="app.agent.commands.execution"):
        await execute(_ctx(session_id=None), "/nope\nFORGED-LINE", registry=registry)
    messages = [record.getMessage() for record in caplog.records]
    assert any("nope" in message for message in messages)
    assert any("\\nFORGED-LINE" in message for message in messages)
    assert not any("\n" in message for message in messages), "日志必须是单物理行"


@pytest.mark.asyncio
async def test_lock_is_external_and_shared_between_execution_instances(fake_redis):
    """两个 execute 实例共享同一 FakeRedis（模拟两个 worker）：锁必须互斥。

    变异点：锁退回进程内字典 → 第二个实例看不到第一个的锁（本用例红）。
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(ctx, args):
        started.set()
        await release.wait()
        return CommandResult(content="slow-done")

    async def _fast(ctx, args):
        return CommandResult(content="fast-done")

    registry_a = CommandRegistry()
    registry_a.register(Command(name="slow", description="", handler=_slow))
    registry_b = CommandRegistry()
    registry_b.register(Command(name="fast", description="", handler=_fast))

    first = asyncio.create_task(
        execute(
            _ctx(session_id="shared"),
            "/slow",
            registry=registry_a,
            redis=fake_redis,
        )
    )
    await started.wait()
    busy = await execute(_ctx(session_id="shared"), "/fast", registry=registry_b, redis=fake_redis)
    assert busy.success is False and busy.error_code == "command_busy"

    other_session = await execute(
        _ctx(session_id="other"), "/fast", registry=registry_b, redis=fake_redis
    )
    assert other_session.success is True, "不同会话不得互相阻塞"

    release.set()
    assert (await first).success is True
    assert not any(key.startswith("cmd:lock:") for key in fake_redis.store), (
        "执行结束必须 compare-and-del 释放锁"
    )


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
