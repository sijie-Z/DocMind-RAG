"""issue #475 User Commands —— API 契约 + chat 前置拦截（无模型轮次）。

issue #498 增补：请求契约（line / command+args / idempotency_key）、响应信封
（execution_id/status/result）、Redis 锁与幂等（本机无 Redis，用共享 FakeRedis
替身；多进程互斥的最小单测在 tests/unit/test_user_commands.py）。

判别力（变异点）：
- GET /commands 不做组织 fail-closed（无组织用户仍 200）→ org 用例红；
- GET /commands 不按权限过滤 / 字段缺失（含 args_schema）→ metadata 用例红；
- 命令未启用时仍可执行 / 状态码不是 503 → disabled 用例红；
- POST /commands/execute 未知命令返回 200 / 消息漏给模型 → unknown 用例红；
- 未知命令错误码不是契约 NOT_FOUND → unknown 用例红；
- 参数错误不带 field → field 用例红；
- 管理命令不做权限判定 → forbidden 用例红；
- session_id 不做归属校验（跨租户作用域）→ session-access 用例红；
- 只接受 line、command+args 兼容层 422 → compat 用例红；
- 同一 idempotency_key 两次不同 execution_id / handler 执行两次 → idem 用例红；
- Redis 不可用不 fail closed（200/404）→ redis-unavailable 用例红；
- /agent/chat 对 "/" 开头文本不短路（进入 agent_service.chat）→ no-model 用例红；
- /chat/stream useAgent 对 "/" 开头文本不短路 → chat-stream 用例红；
- 命令执行后落库（写用户消息）→ save 用例红。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import app.agent.commands.execution as execution_mod
import app.api.v1.endpoints.agent as agent_api
import app.api.v1.endpoints.chat as chat_api
import app.api.v1.endpoints.commands as commands_api
from app.agent.commands import Command, CommandArg, CommandFailure, CommandResult, command_registry
from app.agent.commands.types import ERROR_RESULT_TOO_LARGE, CommandExecution
from app.agent.service import agent_service
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app


class FakeRedis:
    """最小 Redis 替身：SET NX EX / GET / DELETE / Lua compare-and-del。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    def __bool__(self):
        return True

    async def set(self, key, value, nx=False, ex=None, **kwargs):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def eval(self, script, numkeys, *args):
        assert 'redis.call("get", KEYS[1]) == ARGV[1]' in script, "必须先比较 owner"
        key, token = args[0], args[1]
        if 'redis.call("set", KEYS[1], ARGV[2]' in script:
            value = args[2]
            if self.store.get(key) == token:
                self.store[key] = value
                return 1
            return 0
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(execution_mod, "redis_client", client)
    return client


def _user(org_id=1, is_superuser=False):
    return SimpleNamespace(
        id=7,
        organization_id=org_id,
        is_superuser=is_superuser,
        is_active=True,
        role="user",
    )


def _override_db():
    db = SimpleNamespace()
    db.close = lambda: None

    async def _override():
        yield db

    return _override


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def auth():
    """登录态替身 + 可用的 db 依赖；用完即撤。"""
    app.dependency_overrides[get_current_user] = lambda: _user()
    app.dependency_overrides[get_db] = _override_db()
    yield _user()
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def admin_command():
    """临时注册的管理命令（证明权限过滤不是硬编码名单）。"""
    command_registry.register(
        Command(
            name="admin-probe",
            description="测试管理命令",
            permission="admin",
            handler=lambda ctx, args: _async_result(),
        )
    )
    yield "admin-probe"
    command_registry.unregister("admin-probe")


async def _async_result():
    return CommandResult(content="admin-ok")


def _sse_payloads(text: str) -> list[dict]:
    payloads = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[len("data: ") :]))
    return payloads


def _patch_admin(monkeypatch, value: bool):
    async def _is_admin(db, user, organization_id):
        return value

    monkeypatch.setattr(execution_mod, "is_org_admin", _is_admin)


def _stateful_session_loader(state, calls):
    """模拟真实 owner/org/scope 条件；历史 NULL 与超管旁路语义保持一致。"""

    async def _load(db, session_id, user, scope):
        calls.append((session_id, user.id, scope))
        if session_id != state["id"] or state["user_id"] != user.id:
            return None
        resource_org = state["organization_id"]
        if not scope.bypass_org and resource_org is not None and resource_org != scope.org_id:
            return None
        return SimpleNamespace(
            id=state["id"],
            user_id=state["user_id"],
            organization_id=resource_org,
        )

    return _load


@pytest.fixture
def accessible_session(monkeypatch):
    """聊天入口的会话归属前置校验：默认放行（个别用例覆盖为 None）。"""

    async def _ok(*args, **kwargs):
        return SimpleNamespace(id="sess")

    monkeypatch.setattr(execution_mod, "load_accessible_session", _ok)


@pytest.fixture
def no_model(monkeypatch):
    """agent_service.chat 探针：任何进入 PER 的调用都会被记录。"""
    calls = []

    async def _fake_chat(**kwargs):
        calls.append(kwargs)
        return
        yield  # pragma: no cover — 让函数成为 async generator

    monkeypatch.setattr(agent_service, "chat", _fake_chat)
    return calls


# ── GET /api/v1/commands ─────────────────────────────────────────────────────


def test_list_commands_requires_auth(client):
    r = client.get("/api/v1/commands")
    assert r.status_code == 401


def test_list_commands_requires_organization_fail_closed(client, monkeypatch):
    """变异点：删掉 require_organization → 无组织用户拿到命令列表（本用例红）。"""
    app.dependency_overrides[get_current_user] = lambda: _user(org_id=None)
    app.dependency_overrides[get_db] = _override_db()
    try:
        r = client.get("/api/v1/commands")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 403


def test_list_commands_returns_metadata_and_filters_admin(client, auth, monkeypatch, admin_command):
    _patch_admin(monkeypatch, False)
    r = client.get("/api/v1/commands")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["enabled"] is True
    names = [c["name"] for c in data["commands"]]
    assert {"help", "status", "export", "tools"} <= set(names)
    assert admin_command not in names, "非管理员不得看到管理命令"

    help_cmd = next(c for c in data["commands"] if c["name"] == "help")
    assert help_cmd["description"] and help_cmd["usage"].startswith("/")
    assert help_cmd["permission"] == "user"
    assert help_cmd["description_i18n"]["zh"] and help_cmd["description_i18n"]["en"]
    assert isinstance(help_cmd["args"], list) and help_cmd["args"]
    # issue #498：GET 命令项必须带 args_schema（JSON-Schema 风格）。
    assert help_cmd["args_schema"]["type"] == "object"
    assert "command" in help_cmd["args_schema"]["properties"]

    _patch_admin(monkeypatch, True)
    r2 = client.get("/api/v1/commands")
    assert admin_command in [c["name"] for c in r2.json()["data"]["commands"]]


def test_list_commands_disabled_returns_empty_view(client, auth, monkeypatch):
    monkeypatch.setattr(settings, "COMMANDS_ENABLED", False)
    r = client.get("/api/v1/commands")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["enabled"] is False and data["commands"] == []


def test_list_commands_session_scope_requires_accessible_session(client, auth, monkeypatch):
    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(commands_api, "load_accessible_session", _none)
    r = client.get("/api/v1/commands", params={"session_id": "foreign"})
    assert r.status_code == 404
    assert r.json()["error_code"] == "SESSION_NOT_FOUND"


# ── POST /api/v1/commands/execute ───────────────────────────────────────────


def test_execute_help_success_never_calls_model(client, auth, monkeypatch, no_model):
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/commands/execute", json={"line": "/help"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["success"] is True
    assert data["command"] == "help"
    assert "/status" in data["content"]
    assert data["error_code"] == ""
    # issue #498 执行信封
    assert data["execution_id"] and data["status"] == "completed"
    assert data["result"]["content"] == data["content"]
    assert no_model == [], "命令执行不得进入 agent_service.chat（无模型轮次）"


def test_execute_bare_line_without_slash_is_accepted(client, auth, monkeypatch, no_model):
    """issue #498：``help``（无前导 /）与 ``/help`` 等价。"""
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/commands/execute", json={"line": "help"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["command"] == "help"


def test_execute_command_args_compat_body(client, auth, monkeypatch, no_model):
    """issue #498：``{"command","args"}`` 兼容层（args 空对象 / 位置数组均可）。"""
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/commands/execute", json={"command": "help", "args": {}})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["command"] == "help"

    r2 = client.post("/api/v1/commands/execute", json={"command": "help", "args": ["export"]})
    assert r2.status_code == 200, r2.text
    data = r2.json()["data"]
    assert data["command"] == "help" and "/export" in data["content"]


def test_execute_command_args_unknown_mapping_field_is_400(client, auth, monkeypatch):
    """评审 minor：映射型 args 的未知键必须显式 400，不得静默错绑为位置参数。"""
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/commands/execute", json={"command": "help", "args": {"nope": "x"}})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error_code"] == "COMMAND_INVALID_ARGUMENT"
    assert body["detail"]["fields"] == ["nope"]


def test_execute_command_args_mapping_cannot_skip_positional(client, auth, monkeypatch):
    """评审 m2：两参命令只给第二个参数 → 400（不得把值错绑到第一个位置）。"""

    async def _handler(ctx, args):
        return CommandResult(content=f"{args.get('a')}-{args.get('b')}", data=args)

    _patch_admin(monkeypatch, False)
    command_registry.register(
        Command(
            name="two-probe",
            description="",
            args=(
                CommandArg(name="a", description="a", required=True),
                CommandArg(name="b", description="b"),
            ),
            handler=_handler,
        )
    )
    try:
        skipped = client.post(
            "/api/v1/commands/execute", json={"command": "two-probe", "args": {"b": "B"}}
        )
        full = client.post(
            "/api/v1/commands/execute",
            json={"command": "two-probe", "args": {"a": "A", "b": "B"}},
        )
    finally:
        command_registry.unregister("two-probe")

    assert skipped.status_code == 400, skipped.text
    assert skipped.json()["error_code"] == "COMMAND_INVALID_ARGUMENT"
    assert skipped.json()["detail"]["field"] == "a"
    assert full.status_code == 200, full.text
    assert full.json()["data"]["args"] == {"a": "A", "b": "B"}


def test_execute_requires_line_or_command(client, auth, monkeypatch):
    r = client.post("/api/v1/commands/execute", json={"session_id": "s"})
    assert r.status_code == 422


def test_execute_idempotency_key_returns_same_execution_id(client, auth, monkeypatch, no_model):
    """变异点：body 丢弃 idempotency_key → 两次 execution_id 不同、handler 跑两次。"""
    _patch_admin(monkeypatch, False)
    calls: list[int] = []

    async def _handler(ctx, args):
        calls.append(len(calls) + 1)
        return CommandResult(content=f"run-{len(calls)}")

    command_registry.register(Command(name="idem-probe", description="", handler=_handler))
    try:
        r1 = client.post(
            "/api/v1/commands/execute",
            json={"line": "/idem-probe", "idempotency_key": "api-key-1"},
        )
        r2 = client.post(
            "/api/v1/commands/execute",
            json={"line": "/idem-probe", "idempotency_key": "api-key-1"},
        )
    finally:
        command_registry.unregister("idem-probe")
    assert r1.status_code == 200 and r2.status_code == 200
    first, second = r1.json()["data"], r2.json()["data"]
    assert first["execution_id"] == second["execution_id"]
    assert first["content"] == "run-1" and second["content"] == "run-1"
    assert calls == [1], "handler 只执行一次"


def test_empty_session_id_is_normalized_for_completed_replay(client, auth, monkeypatch, fake_redis):
    _patch_admin(monkeypatch, False)
    loader = AsyncMock()
    monkeypatch.setattr(commands_api, "load_accessible_session", loader)
    body = {"line": "/help", "session_id": "", "idempotency_key": "empty-session"}
    first = client.post("/api/v1/commands/execute", json=body)
    replay = client.post("/api/v1/commands/execute", json=body)
    assert first.status_code == 200 and replay.status_code == 200, replay.text
    assert replay.json()["data"]["execution_id"] == first.json()["data"]["execution_id"]
    envelope = json.loads(fake_redis.store["cmd:idem:7:empty-session"])
    assert envelope["session_id"] is None
    assert envelope["resource_org_id"] is None
    loader.assert_not_called()


def test_export_replay_cannot_cross_org_without_session(client, auth, monkeypatch):
    """A 的导出正文不得随 user+key 释放给 B 组织、无 session 的请求。"""
    principal = _user(org_id=1)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    calls = []

    async def _export(ctx, args):
        calls.append((ctx.organization_id, ctx.session_id, args["format"]))
        return CommandResult(content="ORG-A-SECRET")

    export = command_registry.resolve("export")[0]
    monkeypatch.setattr(export, "handler", _export)

    async def _accessible(db, session_id, user, scope):
        return SimpleNamespace(id=session_id, organization_id=1)

    monkeypatch.setattr(commands_api, "load_accessible_session", _accessible)
    first = client.post(
        "/api/v1/commands/execute",
        json={
            "line": "/export json",
            "session_id": "session-A",
            "idempotency_key": "cross-org",
        },
    )
    principal.organization_id = 2
    replay = client.post(
        "/api/v1/commands/execute",
        json={"line": "/export json", "idempotency_key": "cross-org"},
    )
    assert first.status_code == 200, first.text
    assert replay.status_code == 409, replay.text
    assert replay.json()["error_code"] == "COMMAND_BUSY"
    assert "ORG-A-SECRET" not in replay.text
    assert calls == [(1, "session-A", "json")]


@pytest.mark.parametrize("second_session", ["session-B", None], ids=["different", "missing"])
def test_export_replay_rejects_same_org_session_change(client, auth, monkeypatch, second_session):
    principal = _user(org_id=1)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    calls = []

    async def _export(ctx, args):
        calls.append(1)
        return CommandResult(content="SESSION-A-SECRET")

    monkeypatch.setattr(command_registry.resolve("export")[0], "handler", _export)

    async def _accessible(db, session_id, user, scope):
        return SimpleNamespace(id=session_id, organization_id=1)

    monkeypatch.setattr(commands_api, "load_accessible_session", _accessible)
    first = client.post(
        "/api/v1/commands/execute",
        json={"line": "/export", "session_id": "session-A", "idempotency_key": "session"},
    )
    body = {"line": "/export", "idempotency_key": "session"}
    if second_session is not None:
        body["session_id"] = second_session
    replay = client.post("/api/v1/commands/execute", json=body)
    assert first.status_code == 200
    assert replay.status_code == 409, replay.text
    assert "SESSION-A-SECRET" not in replay.text
    assert calls == [1]


def test_session_replay_reloads_cached_session_and_normalizes_request(client, auth, monkeypatch):
    principal = _user(org_id=1)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    handler_calls = []
    loader_calls = []

    async def _export(ctx, args):
        handler_calls.append(args)
        return CommandResult(content="authorized-export")

    monkeypatch.setattr(command_registry.resolve("export")[0], "handler", _export)

    async def _accessible(db, session_id, user, scope):
        loader_calls.append((db, session_id, user, scope))
        return SimpleNamespace(id=session_id, organization_id=1)

    monkeypatch.setattr(commands_api, "load_accessible_session", _accessible)
    first = client.post(
        "/api/v1/commands/execute",
        json={"line": "/export", "session_id": "session-A", "idempotency_key": "positive"},
    )
    replay = client.post(
        "/api/v1/commands/execute",
        json={
            "line": "export markdown",
            "session_id": "session-A",
            "idempotency_key": "positive",
        },
    )
    assert first.status_code == 200 and replay.status_code == 200
    assert replay.json()["data"]["execution_id"] == first.json()["data"]["execution_id"]
    assert replay.json()["data"]["content"] == "authorized-export"
    assert handler_calls == [{"format": "markdown"}]
    assert [call[1] for call in loader_calls] == ["session-A"] * 3
    assert all(call[2] is principal for call in loader_calls)
    assert len({id(call[0]) for call in loader_calls}) == 1
    replay_scope = loader_calls[-1][3]
    assert replay_scope.bypass_org is False and replay_scope.org_id == 1


@pytest.mark.parametrize(
    ("is_superuser", "resource_org"),
    [(True, 2), (False, None)],
    ids=["superuser-other-org", "historical-null-org"],
)
def test_session_replay_allows_visible_original_resource_org(
    client, auth, monkeypatch, fake_redis, is_superuser, resource_org
):
    """resource org 可合法不同于 effective org；不能用 effective org 代替资源属性。"""
    principal = _user(org_id=1, is_superuser=is_superuser)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    state = {"id": "session-A", "user_id": 7, "organization_id": resource_org}
    loader_calls = []
    handler_calls = []

    async def _export(ctx, args):
        handler_calls.append(1)
        return CommandResult(content="VISIBLE-ORIGINAL-EXPORT")

    monkeypatch.setattr(command_registry.resolve("export")[0], "handler", _export)
    monkeypatch.setattr(
        commands_api,
        "load_accessible_session",
        _stateful_session_loader(state, loader_calls),
    )
    body = {
        "line": "/export",
        "session_id": "session-A",
        "idempotency_key": "resource-positive",
    }
    first = client.post("/api/v1/commands/execute", json=body)
    replay = client.post("/api/v1/commands/execute", json=body)
    assert first.status_code == 200 and replay.status_code == 200, replay.text
    assert replay.json()["data"]["execution_id"] == first.json()["data"]["execution_id"]
    assert handler_calls == [1]
    assert len(loader_calls) == 3
    scopes = [call[2] for call in loader_calls]
    assert all(scope.bypass_org is is_superuser for scope in scopes)
    envelope = json.loads(fake_redis.store["cmd:idem:7:resource-positive"])
    assert envelope["organization_id"] == 1
    assert envelope["resource_org_id"] == resource_org


@pytest.mark.parametrize(
    ("is_superuser", "mutated_org"),
    [(True, 2), (False, None)],
    ids=["superuser-bypass-still-visible", "historical-null-still-visible"],
)
def test_session_replay_rejects_resource_org_change_even_when_loader_returns_session(
    client, auth, monkeypatch, is_superuser, mutated_org
):
    """loader 非 None 不足以免责：资源 org 变化必须拒绝，即使当前 scope 仍可见。"""
    principal = _user(org_id=1, is_superuser=is_superuser)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    state = {"id": "session-A", "user_id": 7, "organization_id": 1}
    loader_calls = []
    handler_calls = []

    async def _export(ctx, args):
        handler_calls.append(1)
        return CommandResult(content="MUTATED-RESOURCE-SECRET")

    monkeypatch.setattr(command_registry.resolve("export")[0], "handler", _export)
    monkeypatch.setattr(
        commands_api,
        "load_accessible_session",
        _stateful_session_loader(state, loader_calls),
    )
    body = {
        "line": "/export",
        "session_id": "session-A",
        "idempotency_key": "resource-mutated",
    }
    first = client.post("/api/v1/commands/execute", json=body)
    state["organization_id"] = mutated_org
    replay = client.post("/api/v1/commands/execute", json=body)
    assert first.status_code == 200
    assert replay.status_code == 404, replay.text
    assert replay.json()["error_code"] == "SESSION_NOT_FOUND"
    assert "MUTATED-RESOURCE-SECRET" not in replay.text
    assert handler_calls == [1]
    assert len(loader_calls) == 3


@pytest.mark.parametrize("reason", ["deleted", "owner-changed", "scope-invisible"])
def test_session_replay_denies_inaccessible_original_without_leaking(
    client, auth, monkeypatch, reason
):
    principal = _user(org_id=1)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    handler_calls = []
    loader_calls = []

    async def _export(ctx, args):
        handler_calls.append(1)
        return CommandResult(content="ORIGINAL-SESSION-SECRET")

    monkeypatch.setattr(command_registry.resolve("export")[0], "handler", _export)

    async def _guard(db, session_id, user, scope):
        loader_calls.append((reason, session_id))
        if len(loader_calls) == 3:
            return None
        return SimpleNamespace(id=session_id, organization_id=1)

    monkeypatch.setattr(commands_api, "load_accessible_session", _guard)
    first = client.post(
        "/api/v1/commands/execute",
        json={"line": "/export", "session_id": "session-A", "idempotency_key": "guard"},
    )
    replay = client.post(
        "/api/v1/commands/execute",
        json={"line": "/export", "session_id": "session-A", "idempotency_key": "guard"},
    )
    assert first.status_code == 200
    assert replay.status_code == 404, replay.text
    assert replay.json()["error_code"] == "SESSION_NOT_FOUND"
    assert "ORIGINAL-SESSION-SECRET" not in replay.text
    assert handler_calls == [1]
    assert len(loader_calls) == 3


def test_session_replay_guard_db_error_is_503_not_reexecution(client, auth, monkeypatch):
    principal = _user(org_id=1)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    handler_calls = []
    loader_calls = []

    async def _export(ctx, args):
        handler_calls.append(1)
        return CommandResult(content="CACHED-DB-SECRET")

    monkeypatch.setattr(command_registry.resolve("export")[0], "handler", _export)

    async def _guard(db, session_id, user, scope):
        loader_calls.append(session_id)
        if len(loader_calls) == 3:
            raise RuntimeError("database-credentials-must-not-leak")
        return SimpleNamespace(id=session_id, organization_id=1)

    monkeypatch.setattr(commands_api, "load_accessible_session", _guard)
    first = client.post(
        "/api/v1/commands/execute",
        json={"line": "/export", "session_id": "session-A", "idempotency_key": "db-down"},
    )
    replay = client.post(
        "/api/v1/commands/execute",
        json={"line": "/export", "session_id": "session-A", "idempotency_key": "db-down"},
    )
    assert first.status_code == 200
    assert replay.status_code == 503, replay.text
    assert replay.json()["error_code"] == "SERVICE_UNAVAILABLE"
    assert "CACHED-DB-SECRET" not in replay.text
    assert "database-credentials" not in replay.text
    assert handler_calls == [1]


def test_replay_authorizer_uses_cached_context_not_second_body(client, auth, monkeypatch):
    principal = _user(org_id=1)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)
    loader_calls = []

    async def _accessible(db, session_id, user, scope):
        loader_calls.append(session_id)
        return SimpleNamespace(id=session_id, organization_id=1)

    monkeypatch.setattr(commands_api, "load_accessible_session", _accessible)

    async def _fake_execute(
        ctx,
        line,
        *,
        registry,
        idempotency_key,
        session_resource_org_id,
        replay_authorizer,
    ):
        cached_session = "cached-A" if ctx.session_id else None
        await replay_authorizer(
            1,
            cached_session,
            1 if cached_session is not None else None,
        )
        return CommandExecution(command="help", line=line, success=True)

    monkeypatch.setattr(commands_api, "execute", _fake_execute)
    with_session = client.post(
        "/api/v1/commands/execute", json={"line": "/help", "session_id": "body-B"}
    )
    without_session = client.post("/api/v1/commands/execute", json={"line": "/help"})
    assert with_session.status_code == 200 and without_session.status_code == 200
    assert loader_calls == ["body-B", "cached-A"]


def test_replay_authorizer_independently_rejects_org_mismatch(client, auth, monkeypatch):
    principal = _user(org_id=1)
    app.dependency_overrides[get_current_user] = lambda: principal
    _patch_admin(monkeypatch, False)

    async def _fake_execute(
        ctx,
        line,
        *,
        registry,
        idempotency_key,
        session_resource_org_id,
        replay_authorizer,
    ):
        try:
            await replay_authorizer(2, None, None)
        except CommandFailure as exc:
            return CommandExecution(
                command="help",
                line=line,
                success=False,
                error_code=exc.code,
                error_message=exc.message,
            )
        raise AssertionError("原 org 与当前有效 org 不一致时必须拒绝")

    monkeypatch.setattr(commands_api, "execute", _fake_execute)
    response = client.post("/api/v1/commands/execute", json={"line": "/help"})
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "COMMAND_BUSY"


def test_api_idempotent_replay_rechecks_current_admin_role(
    client, auth, monkeypatch, admin_command
):
    role = {"admin": True}

    async def _is_admin(db, user, organization_id):
        return role["admin"]

    monkeypatch.setattr(execution_mod, "is_org_admin", _is_admin)
    calls = []

    async def _secret(ctx, args):
        calls.append(1)
        return CommandResult(content="ADMIN-CACHED-SECRET")

    command_registry.unregister(admin_command)
    command_registry.register(
        Command(name=admin_command, description="", permission="admin", handler=_secret)
    )
    body = {"line": f"/{admin_command}", "idempotency_key": "admin-demotion"}
    first = client.post("/api/v1/commands/execute", json=body)
    role["admin"] = False
    replay = client.post("/api/v1/commands/execute", json=body)
    assert first.status_code == 200
    assert replay.status_code == 403, replay.text
    assert replay.json()["error_code"] == "COMMAND_PERMISSION_DENIED"
    assert "ADMIN-CACHED-SECRET" not in replay.text
    assert calls == [1]


def test_api_legacy_completed_entry_fails_closed(client, auth, monkeypatch, fake_redis):
    _patch_admin(monkeypatch, False)
    legacy = CommandExecution(
        command="help",
        line="/help",
        success=True,
        content="LEGACY-API-SECRET",
        execution_id="legacy-id",
    )
    fake_redis.store["cmd:idem:7:legacy-api"] = json.dumps(legacy.to_dict())
    response = client.post(
        "/api/v1/commands/execute", json={"line": "/help", "idempotency_key": "legacy-api"}
    )
    assert response.status_code == 409, response.text
    assert response.json()["error_code"] == "COMMAND_BUSY"
    assert "LEGACY-API-SECRET" not in response.text


def test_execute_redis_unavailable_is_503(client, auth, monkeypatch, no_model):
    """issue #498 fail-closed：Redis 不可用 → 503 SERVICE_UNAVAILABLE（不假装安全）。"""
    _patch_admin(monkeypatch, False)
    monkeypatch.setattr(execution_mod, "redis_client", None)
    r = client.post("/api/v1/commands/execute", json={"line": "/help"})
    assert r.status_code == 503
    body = r.json()
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["detail"]["error_code"] == "service_unavailable"


def test_export_too_large_maps_to_400_validation_error():
    """issue #498：/export 超 1MB 的上限错误码对外映射 400 VALIDATION_ERROR。"""
    exc = commands_api.CommandExecutionError(
        CommandExecution(
            command="export",
            line="/export",
            success=False,
            error_code=ERROR_RESULT_TOO_LARGE,
            error_message="导出内容超过 1MB 上限",
        )
    )
    assert exc.status_code == 400
    assert exc.error_code == "VALIDATION_ERROR"


def test_execute_unknown_command_is_404(client, auth, monkeypatch, no_model):
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/commands/execute", json={"line": "/definitely-not-a-command"})
    assert r.status_code == 404
    body = r.json()
    # 契约（issue #498）：404 NOT_FOUND；detail.error_code 保留内部原因。
    assert body["error_code"] == "NOT_FOUND"
    assert body["detail"]["error_code"] == "unknown_command"
    assert no_model == []


def test_execute_argument_error_is_field_level(client, auth, monkeypatch):
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/commands/execute", json={"line": "/export xml"})
    assert r.status_code == 400
    body = r.json()
    assert body["error_code"] == "COMMAND_INVALID_ARGUMENT"
    assert body["detail"]["field"] == "format"


def test_execute_admin_command_forbidden_for_non_admin(client, auth, monkeypatch, admin_command):
    """变异点：执行前不判 permission → 本用例红。"""
    called = []

    async def _handler(ctx, args):
        called.append(True)
        return CommandResult(content="admin-ok")

    command_registry.unregister(admin_command)
    command_registry.register(
        Command(name=admin_command, description="", permission="admin", handler=_handler)
    )
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/commands/execute", json={"line": f"/{admin_command}"})
    assert r.status_code == 403
    assert r.json()["error_code"] == "COMMAND_PERMISSION_DENIED"
    assert called == []

    _patch_admin(monkeypatch, True)
    r2 = client.post("/api/v1/commands/execute", json={"line": f"/{admin_command}"})
    assert r2.status_code == 200 and r2.json()["data"]["success"] is True


def test_execute_requires_organization_fail_closed(client, monkeypatch):
    app.dependency_overrides[get_current_user] = lambda: _user(org_id=None)
    app.dependency_overrides[get_db] = _override_db()
    try:
        r = client.post("/api/v1/commands/execute", json={"line": "/help"})
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 403


def test_execute_disabled_feature_is_503(client, auth, monkeypatch, no_model):
    """issue #498：COMMANDS_ENABLED=false → POST 503 SERVICE_UNAVAILABLE。"""
    monkeypatch.setattr(settings, "COMMANDS_ENABLED", False)
    r = client.post("/api/v1/commands/execute", json={"line": "/help"})
    assert r.status_code == 503
    body = r.json()
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["detail"]["reason"] == "commands_disabled"
    assert no_model == []


def test_execute_session_scope_requires_accessible_session(client, auth, monkeypatch, no_model):
    """变异点：跳过 session_id 归属校验 → 跨租户会话作用域可被指定（本用例红）。"""

    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(commands_api, "load_accessible_session", _none)
    r = client.post(
        "/api/v1/commands/execute", json={"line": "/status", "session_id": "foreign-session"}
    )
    assert r.status_code == 404
    assert r.json()["error_code"] == "SESSION_NOT_FOUND"
    assert no_model == []


# ── /agent/chat 前置拦截（命令不进入模型轮次）────────────────────────────────


def test_agent_chat_help_short_circuits_before_model(
    client, auth, monkeypatch, no_model, accessible_session
):
    """变异点：删掉 agent.py 命令分支 → agent_service.chat 被调用、本用例红。"""
    _patch_admin(monkeypatch, False)
    save = AsyncMock()
    monkeypatch.setattr(agent_api, "_save_session_interaction", save)
    create_task = AsyncMock()
    monkeypatch.setattr(agent_api.checkpoint, "create_task", create_task)

    r = client.post(
        "/api/v1/agent/chat",
        json={"query": "/help", "session_id": "sess-cmd"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    payloads = _sse_payloads(r.text)
    types = [p["type"] for p in payloads]
    # command_* 是命令语义；chunk 是兼容层（当前 Agent 页只渲染 chunk）。
    assert types == ["command_start", "command_result", "chunk", "done"], types
    result = payloads[1]
    assert result["command_name"] == "help"
    assert "/status" in result["content"]
    assert payloads[2]["content"] == result["content"], "兼容 chunk 必须携带命令结果"
    assert payloads[3].get("saved") is False, "命令不落库（saved=false 如实盖章）"
    assert no_model == [], "命令文本绝不能作为 prompt 漏给模型"
    save.assert_not_awaited()  # 命令执行不得落库（不写用户消息）
    create_task.assert_not_awaited()  # 命令不创建任务行（无模型轮次）


def test_agent_chat_unknown_command_is_explicit_and_never_reaches_model(
    client, auth, monkeypatch, no_model
):
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/agent/chat", json={"query": "/nope"})
    assert r.status_code == 200
    payloads = _sse_payloads(r.text)
    types = [p["type"] for p in payloads]
    assert types == ["command_start", "command_error", "chunk", "done"], types
    err = payloads[1]
    assert err["error_type"] == "unknown_command"
    assert "/help" in err["content"]
    assert payloads[2]["content"] == err["content"]
    assert no_model == []


def test_agent_chat_slash_text_with_arguments_never_reaches_model(
    client, auth, monkeypatch, no_model
):
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/agent/chat", json={"query": "/export xml"})
    payloads = _sse_payloads(r.text)
    types = [p["type"] for p in payloads]
    assert types == ["command_start", "command_error", "chunk", "done"]
    assert payloads[1]["error_type"] == "invalid_argument"
    assert no_model == []


def test_agent_chat_foreign_session_is_rejected_before_execution(
    client, auth, monkeypatch, no_model
):
    """变异点：聊天入口不校验 session_id 归属 → 本用例红（纵深防御，评审 m2）。"""
    _patch_admin(monkeypatch, False)

    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(execution_mod, "load_accessible_session", _none)
    r = client.post(
        "/api/v1/agent/chat", json={"query": "/status", "session_id": "foreign-session"}
    )
    payloads = _sse_payloads(r.text)
    types = [p["type"] for p in payloads]
    assert types == ["command_error", "chunk", "done"], types
    assert payloads[0]["error_type"] == "session_not_found"
    assert payloads[1]["content"] == payloads[0]["content"]
    assert payloads[2].get("saved") is False
    assert no_model == []


def test_agent_chat_disabled_feature_falls_back_to_model(client, auth, monkeypatch):
    """回滚语义（issue §8）：关闭 COMMANDS_ENABLED 后恢复旧行为。"""
    monkeypatch.setattr(settings, "COMMANDS_ENABLED", False)
    calls = []

    async def _fake_chat(**kwargs):
        calls.append(kwargs)
        return
        yield  # pragma: no cover

    monkeypatch.setattr(agent_service, "chat", _fake_chat)
    r = client.post("/api/v1/agent/chat", json={"query": "/help"})
    assert r.status_code == 200
    assert calls, "关闭命令通道后普通聊天链路必须照旧"
    assert "command_result" not in r.text


# ── /chat/stream useAgent 前置拦截 ──────────────────────────────────────────


def test_chat_stream_useagent_help_never_reaches_model(client, auth, monkeypatch, no_model):
    """变异点：只拦 /agent/chat、不拦 /chat/stream useAgent → 本用例红。"""
    _patch_admin(monkeypatch, False)
    r = client.post("/api/v1/chat/stream", json={"content": "/help", "useAgent": True})
    assert r.status_code == 200
    text = r.text
    assert "event: command_result" in text, text[:500]
    assert "event: command_error" not in text
    assert "event: message" in text, "既有消息通道口径：命令结果同时以 message 事件外发"
    events: list[tuple[str, dict]] = []
    current = ""
    for line in text.splitlines():
        if line.startswith("event: "):
            current = line[len("event: ") :].strip()
        elif line.startswith("data: "):
            events.append((current, json.loads(line[len("data: ") :])))
    message = next(payload for name, payload in events if name == "message")
    assert "/status" in message["content"]
    assert no_model == []


def test_chat_stream_useagent_foreign_conversation_rejected(client, auth, monkeypatch, no_model):
    """变异点：聊天入口不校验 conversationId 归属 → 本用例红（评审 m2）。"""
    _patch_admin(monkeypatch, False)

    async def _none(*args, **kwargs):
        return None

    monkeypatch.setattr(execution_mod, "load_accessible_session", _none)
    r = client.post(
        "/api/v1/chat/stream",
        json={"content": "/status", "useAgent": True, "conversationId": "foreign"},
    )
    assert "event: command_error" in r.text
    assert "session_not_found" in r.text
    assert "event: command_result" not in r.text
    assert no_model == []


def test_chat_stream_useagent_disabled_falls_back_to_model(client, auth, monkeypatch):
    """回滚语义：关闭命令通道后 /chat/stream useAgent 恢复旧行为。"""
    monkeypatch.setattr(settings, "COMMANDS_ENABLED", False)
    monkeypatch.setattr(chat_api, "save_session_interaction", AsyncMock(return_value=None))
    calls = []

    async def _fake_chat(**kwargs):
        calls.append(kwargs)
        return
        yield  # pragma: no cover

    monkeypatch.setattr(agent_service, "chat", _fake_chat)
    r = client.post("/api/v1/chat/stream", json={"content": "/help", "useAgent": True})
    assert r.status_code == 200
    assert calls
    assert "event: command_result" not in r.text


def test_list_commands_session_scope_merges_and_shadows(client, auth, monkeypatch):
    """变异点：session_id 作用域不合并/不遮蔽 → 本用例红（评审 n4）。"""
    scoped = Command(
        name="help",
        description="作用域帮助",
        handler=lambda ctx, args: _async_result(),
    )
    command_registry.register(scoped, scope="session:scope-sess")
    try:

        async def _ok(*args, **kwargs):
            return SimpleNamespace(id="scope-sess")

        monkeypatch.setattr(commands_api, "load_accessible_session", _ok)
        r = client.get("/api/v1/commands", params={"session_id": "scope-sess"})
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["scope"] == "session:scope-sess"
        helps = [c for c in data["commands"] if c["name"] == "help"]
        assert len(helps) == 1, "遮蔽后不得出现两份 help"
        assert helps[0]["description"] == "作用域帮助"
    finally:
        command_registry.unregister("help", scope="session:scope-sess")
