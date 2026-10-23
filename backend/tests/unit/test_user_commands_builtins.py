"""issue #475 内置命令 —— /help /status /export /tools 的判别用例。

issue #498 增补：/status 会话必填 + 任务只查该会话；/tools category/risk_level；
/export 1MB 上限 + SYSTEM role。

变异点：
- /help 不按权限过滤 → help-permission 用例红；
- /help 未知目标不返回错误 → help-unknown 用例红；
- /status 不校验会话归属 → status-foreign 用例红；
- /status 缺 session_id 仍 200 → status-session-required 用例红；
- /status 任务不按会话过滤（查用户最近 5 个）→ status-isolation 用例红；
- /status 无活跃任务时 active_task 非 null → status-active-null 用例红；
- /export 不校验会话归属（可导出他人会话）→ export-foreign 用例红；
- /export 输出格式与结构化 data 不一致 → export 用例红；
- /export 不限制 1MB → export-too-large 用例红；
- /export 把 SYSTEM 消息当 assistant → export-system-role 用例红；
- /tools 不返回 category/risk_level → tools 用例红；
- 无会话时 /export 不返回明确错误 → export-no-session 用例红。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.agent.commands.builtins as builtins_mod
import app.agent.commands.execution as execution_mod
from app.agent.commands import Command, CommandContext, CommandRegistry, execute
from app.agent.commands.builtins import register_builtin_commands


class FakeRedis:
    """最小 Redis 替身（issue #498）；语义见 test_user_commands.py 同名类。"""

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
        key, token = args[0], args[1]
        if self.store.get(key) == token:
            self.store.pop(key, None)
            return 1
        return 0


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    client = FakeRedis()
    monkeypatch.setattr(execution_mod, "redis_client", client)
    return client


def _registry():
    registry = CommandRegistry()
    register_builtin_commands(registry)
    return registry


def _ctx(registry, session_id="sess-1", is_admin=False, db=None):
    ctx = CommandContext(
        user_id=7,
        organization_id=3,
        session_id=session_id,
        is_admin=is_admin,
        db=db,
    )
    ctx.registry = registry
    return ctx


def _fake_session(title="测试会话", sid="sess-1", status="active", settings=None):
    return SimpleNamespace(
        id=sid,
        title=title,
        organization_id=3,
        user_id=7,
        status=SimpleNamespace(value=status),
        settings=settings if settings is not None else {"theme": "dark"},
    )


def _fake_message(content="你好", role="user", created_at=None):
    msg_type = SimpleNamespace(value=role)
    return SimpleNamespace(
        content=content,
        message_type=msg_type,
        created_at=created_at or SimpleNamespace(isoformat=lambda: "2026-10-22T10:00:00"),
    )


def _fake_task(task_id="task-1", state="running", progress=0.5, sid="sess-1"):
    return SimpleNamespace(
        id=task_id,
        session_id=sid,
        status=SimpleNamespace(value=state),
        progress=progress,
        updated_at=SimpleNamespace(isoformat=lambda: "2026-10-22T11:00:00"),
    )


@pytest.mark.asyncio
async def test_load_session_enforces_owner_and_org_with_real_sql(tmp_path):
    """真实 SQL 归属校验（评审 m3）：mock 掉 `_load_session` 的用例测不到接线。

    变异点：`_load_session` 漏掉 user_id 或 org 条件 → 本用例红。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 — 注册全部表
    from app.core.database import Base
    from app.models.chat import ChatSession

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'commands.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            db.add_all(
                [
                    ChatSession(id="own", user_id=7, organization_id=3, title="own"),
                    ChatSession(id="other-user", user_id=8, organization_id=3, title="other"),
                    ChatSession(id="other-org", user_id=7, organization_id=99, title="foreign"),
                ]
            )
            await db.commit()

            own = await builtins_mod._load_session(_ctx(None, session_id="own", db=db))
            assert own is not None and own.id == "own"
            assert (
                await builtins_mod._load_session(_ctx(None, session_id="other-user", db=db)) is None
            ), "他人会话必须 fail closed"
            assert (
                await builtins_mod._load_session(_ctx(None, session_id="other-org", db=db)) is None
            ), "跨组织会话必须 fail closed"
    finally:
        await engine.dispose()


def test_help_lists_commands_and_hides_admin_ones(monkeypatch):
    """变异点：help 不使用 registry.list_commands 的 is_admin 过滤 → 本用例红。"""
    registry = _registry()
    registry.register(
        Command(name="secret", description="管理命令", permission="admin", handler=AsyncMock())
    )
    result = asyncio.run(execute(_ctx(registry, session_id=None), "/help", registry=registry))
    assert result.success is True
    assert "/help" in result.content and "/status" in result.content and "/export" in result.content
    assert "/secret" not in result.content, "非管理员不得在 /help 中看到管理命令"


def test_help_admin_view_includes_admin_commands():
    import asyncio

    registry = _registry()
    registry.register(
        Command(name="secret", description="管理命令", permission="admin", handler=AsyncMock())
    )
    result = asyncio.run(
        execute(_ctx(registry, session_id=None, is_admin=True), "/help", registry=registry)
    )
    assert "/secret" in result.content


def test_help_unknown_target_is_an_explicit_error():
    import asyncio

    registry = _registry()
    result = asyncio.run(execute(_ctx(registry), "/help nope", registry=registry))
    assert result.success is False
    assert result.error_code == "unknown_command"


def test_help_detail_for_specific_command():
    import asyncio

    registry = _registry()
    result = asyncio.run(execute(_ctx(registry), "/help export", registry=registry))
    assert result.success is True
    assert "/export" in result.content
    assert "markdown" in result.content and "json" in result.content


def test_tools_lists_manifest_with_category_and_risk_level(monkeypatch):
    """变异点：不合并 DEFAULT_DISABLED_TOOLS / 不返回 category+risk_level → 本用例红。"""
    fake_manifest = [
        {
            "name": "search_knowledge_base",
            "description": "检索知识库",
            "category": "rag",
            "risk_level": "low",
            "tags": ["rag"],
            "requires_auth": False,
        },
        {
            "name": "execute_python",
            "description": "执行代码",
            "category": "code",
            "risk_level": "high",
            "tags": ["code"],
            "requires_auth": False,
        },
        {
            "name": "mcp_call",
            "description": "MCP 调用",
            "category": "mcp",
            "risk_level": "high",
            "tags": ["mcp"],
            "requires_auth": True,
        },
    ]
    monkeypatch.setattr(builtins_mod.tool_registry, "get_manifest", lambda: fake_manifest)
    registry = _registry()

    import asyncio

    result = asyncio.run(execute(_ctx(registry, session_id=None), "/tools", registry=registry))
    assert result.success is True
    assert "search_knowledge_base" in result.content
    assert "execute_python" in result.content and "默认禁用" in result.content
    assert result.data["total"] == 3
    by_name = {t["name"]: t for t in result.data["tools"]}
    assert by_name["execute_python"]["category"] == "code"
    assert by_name["execute_python"]["risk_level"] == "high"
    assert by_name["mcp_call"]["risk_level"] == "high"
    disabled = {t["name"] for t in result.data["tools"] if t["disabled_by_default"]}
    assert disabled == {"execute_python", "mcp_call"}


def test_status_requires_session(monkeypatch):
    """issue #498：/status 缺 session_id → session_required（不再 200 兜底）。"""
    list_tasks = AsyncMock(return_value=[])
    monkeypatch.setattr(builtins_mod, "_list_session_tasks", list_tasks)
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry, session_id=None), "/status", registry=registry))
    assert result.success is False
    assert result.error_code == "session_required"
    assert result.error_field == "session_id"
    list_tasks.assert_not_awaited()


def test_status_reports_session_and_session_scoped_tasks(monkeypatch):
    """变异点：/status 不查会话任务 / 不返回 active_task → 本用例红。"""
    session = _fake_session()
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=session))
    monkeypatch.setattr(builtins_mod, "_count_messages", AsyncMock(return_value=4))
    monkeypatch.setattr(
        builtins_mod, "_last_message", AsyncMock(return_value=_fake_message("最近一条"))
    )
    tasks = [
        _fake_task("task-1", state="running", progress=0.5),
        _fake_task("task-0", state="completed", progress=1.0),
    ]
    list_tasks = AsyncMock(return_value=tasks)
    monkeypatch.setattr(builtins_mod, "_list_session_tasks", list_tasks)

    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry), "/status", registry=registry))
    assert result.success is True
    data = result.data
    assert data["session_id"] == "sess-1"
    assert data["title"] == "测试会话"
    assert data["session_status"] == "active"
    assert data["message_count"] == 4
    assert data["last_message_at"] == "2026-10-22T10:00:00"
    assert data["memory"]["message_count"] == 4
    assert data["memory"]["settings"] == {"theme": "dark"}
    assert data["active_task"]["task_id"] == "task-1"
    assert [t["task_id"] for t in data["tasks"]] == ["task-1", "task-0"]
    # 只查该会话（不再调用 checkpoint.list_tasks 的用户级查询）
    called_ctx, called_session_id = list_tasks.await_args.args
    assert called_session_id == "sess-1"
    assert called_ctx.user_id == 7 and called_ctx.organization_id == 3
    assert "测试会话" in result.content and "4" in result.content


def test_status_foreign_session_fails_closed(monkeypatch):
    """变异点：跳过会话归属校验 → 本用例红。"""
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=None))
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry), "/status", registry=registry))
    assert result.success is False
    assert result.error_code == "session_not_found"


def test_status_active_task_is_null_when_only_terminal_tasks(monkeypatch):
    """issue #498：无活跃任务时 active_task=null（不是旧任务假装活跃）。"""
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=_fake_session()))
    monkeypatch.setattr(builtins_mod, "_count_messages", AsyncMock(return_value=1))
    monkeypatch.setattr(builtins_mod, "_last_message", AsyncMock(return_value=None))
    monkeypatch.setattr(
        builtins_mod,
        "_list_session_tasks",
        AsyncMock(return_value=[_fake_task("t-done", state="completed", progress=1.0)]),
    )
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry), "/status", registry=registry))
    assert result.success is True
    assert result.data["active_task"] is None
    assert result.data["tasks"][0]["task_id"] == "t-done"


@pytest.mark.asyncio
async def test_list_session_tasks_filters_by_session_and_user_with_real_sql(tmp_path):
    """真实 SQL：任务只按该会话 + 该用户查询（issue #498 会话隔离）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 — 注册全部表
    from app.core.database import Base
    from app.models.agent_task import AgentTask

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'status.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            db.add_all(
                [
                    AgentTask(
                        id="own", user_id=7, organization_id=3, session_id="sess-1", query="q"
                    ),
                    AgentTask(
                        id="other-session",
                        user_id=7,
                        organization_id=3,
                        session_id="sess-2",
                        query="q",
                    ),
                    AgentTask(
                        id="other-user",
                        user_id=8,
                        organization_id=3,
                        session_id="sess-1",
                        query="q",
                    ),
                    AgentTask(
                        id="other-org",
                        user_id=7,
                        organization_id=99,
                        session_id="sess-1",
                        query="q",
                    ),
                ]
            )
            await db.commit()
            tasks = await builtins_mod._list_session_tasks(
                _ctx(None, session_id="sess-1", db=db), "sess-1"
            )
            assert [task.id for task in tasks] == ["own"]
    finally:
        await engine.dispose()


def test_export_requires_session():
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry, session_id=None), "/export", registry=registry))
    assert result.success is False
    assert result.error_code == "session_required"


def test_export_markdown_and_json(monkeypatch):
    """变异点：/export 的 content 与 data 格式不一致 → 本用例红。"""
    session = _fake_session(title="周报会话")
    # `_load_messages` 契约为"新 → 旧"；handler 截断后反转输出为时间正序。
    messages = [_fake_message("你好呀", "assistant"), _fake_message("你好", "user")]
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=session))
    monkeypatch.setattr(builtins_mod, "_load_messages", AsyncMock(return_value=messages))
    registry = _registry()
    import asyncio

    md = asyncio.run(execute(_ctx(registry), "/export", registry=registry))
    assert md.success is True
    assert md.content.startswith("# 周报会话")
    assert "你好呀" in md.content
    assert md.data["format"] == "markdown" and md.data["message_count"] == 2
    assert md.data["filename"].endswith(".md")

    as_json = asyncio.run(execute(_ctx(registry), "/export json", registry=registry))
    assert as_json.success is True
    payload = json.loads(as_json.content)
    assert payload["session_id"] == "sess-1"
    assert [m["role"] for m in payload["messages"]] == ["user", "assistant"]
    assert as_json.data["format"] == "json" and as_json.data["filename"].endswith(".json")


def test_export_truncation_keeps_newest_not_oldest(monkeypatch):
    """变异点：按旧→新截断（保留最旧 N 条却写"最近"）→ 本用例红（评审 m1）。"""
    session = _fake_session()
    newest_first = [_fake_message(f"msg-{i}", "user") for i in (5, 4, 3, 2, 1)]
    monkeypatch.setattr(builtins_mod, "MAX_EXPORT_MESSAGES", 3)
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=session))
    monkeypatch.setattr(builtins_mod, "_load_messages", AsyncMock(return_value=newest_first))
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry), "/export", registry=registry))
    assert result.success is True
    assert result.data["truncated"] is True and result.data["message_count"] == 3
    content = result.content
    assert "msg-5" in content and "msg-3" in content
    assert "msg-2" not in content and "msg-1" not in content, "截断必须保留最近的消息"
    assert content.index("msg-3") < content.index("msg-4") < content.index("msg-5"), (
        "导出正文应按时间正序"
    )


def test_export_system_messages_keep_system_role(monkeypatch):
    """issue #498：SYSTEM 消息导出为 role:"system"，不得伪装 assistant。"""
    session = _fake_session()
    newest_first = [
        _fake_message("助手回答", "assistant"),
        _fake_message("系统摘要", "system"),
        _fake_message("用户问题", "user"),
    ]
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=session))
    monkeypatch.setattr(builtins_mod, "_load_messages", AsyncMock(return_value=newest_first))
    registry = _registry()
    import asyncio

    as_json = asyncio.run(execute(_ctx(registry), "/export json", registry=registry))
    assert as_json.success is True
    payload = json.loads(as_json.content)
    assert [m["role"] for m in payload["messages"]] == ["user", "system", "assistant"]

    md = asyncio.run(execute(_ctx(registry), "/export", registry=registry))
    assert md.success is True
    assert "**系统**" in md.content and "系统摘要" in md.content


def test_export_over_1mb_is_result_too_large(monkeypatch):
    """变异点：不限制导出体积 → 本用例红（issue #498：超 1MB → 400 VALIDATION_ERROR）。"""
    session = _fake_session()
    huge = _fake_message("x" * (builtins_mod.MAX_EXPORT_BYTES + 1), "user")
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=session))
    monkeypatch.setattr(builtins_mod, "_load_messages", AsyncMock(return_value=[huge]))
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry), "/export", registry=registry))
    assert result.success is False
    assert result.error_code == "result_too_large"
    assert result.data["max_bytes"] == builtins_mod.MAX_EXPORT_BYTES
    assert result.data["content_bytes"] > builtins_mod.MAX_EXPORT_BYTES


def test_export_foreign_session_fails_closed(monkeypatch):
    """变异点：跳过会话归属校验（可导出他人会话）→ 本用例红。"""
    monkeypatch.setattr(builtins_mod, "_load_session", AsyncMock(return_value=None))
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry), "/export", registry=registry))
    assert result.success is False
    assert result.error_code == "session_not_found"


def test_export_invalid_format_is_field_level():
    registry = _registry()
    import asyncio

    result = asyncio.run(execute(_ctx(registry), "/export xml", registry=registry))
    assert result.success is False
    assert result.error_code == "invalid_argument" and result.error_field == "format"


def test_builtin_metadata_is_i18n_complete():
    """变异点：删除英文描述（中英漂移）→ 本用例红。"""
    registry = _registry()
    commands = {c.name: c for c in registry.list_commands()}
    for name in ("help", "status", "export", "tools"):
        assert name in commands
        desc = commands[name].description_i18n
        assert desc.get("zh") and desc.get("en"), f"{name} 缺少中/英描述"
        assert commands[name].usage.startswith("/")


def test_builtin_commands_are_registered_only_once():
    """变异点：重复注册内置命令（import 副作用）→ 本用例红。"""
    registry = CommandRegistry()
    register_builtin_commands(registry)
    register_builtin_commands(registry)  # 幂等：内部以名字判断跳过
    names = [c.name for c in registry.list_commands()]
    assert names == ["export", "help", "status", "tools"]
