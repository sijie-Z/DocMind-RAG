"""批次十四 C4：组织工具白名单强制执行（schema + 执行层 + 服务注入）。

覆盖：
- ``ToolRegistry.list_active_tools``：白名单非 None 只留规范名；None 不限制；
  ``[]`` = 无工具；与 tag/disabled/enabled_predicate 取交集；旧名不生效。
- ``Executor`` 执行层：planner 幻觉发出名单外工具 → 结构化
  ``tool_not_allowed``，不触达 registry；名单内工具正常执行；NULL 行为不变。
- ``AgentService.chat``：从组织设置解析白名单并 ``dataclasses.replace`` 注入
  本次运行；基配置/会话 Redis 持久化不被污染；NULL/坏 JSON/读取失败 fail-open；
  跨组织隔离。

变异判别力：
- 删掉 registry 白名单过滤 → schema 用例红；
- 删掉 executor 的 ``tool_whitelist is not None`` 分支 → 执行层用例红；
- 注入改成直接改 ``agent_config.tool_whitelist`` → 持久化未被污染用例红；
- 读取失败改成 re-raise → fail-open 用例红。
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.agent.service  # noqa: F401 — 触发全部工具注册
import app.agent.subagent as subagent
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import tool_registry
from app.agent.run_context import RunContext
from app.agent.service import AgentService
from app.core.database import Base
from app.models.organization_settings import OrganizationSettings

# ── schema 注入面 ──────────────────────────────────────────────────────


def _active_names(config: AgentConfig) -> set[str]:
    return {entry.name for entry in tool_registry.list_active_tools(config)}


def test_whitelist_filters_schema_to_canonical_names():
    names = _active_names(AgentConfig(tool_whitelist=["search_knowledge_base", "list_documents"]))
    assert names == {"search_knowledge_base", "list_documents"}


def test_whitelist_none_leaves_tool_surface_unchanged():
    names = _active_names(AgentConfig())
    assert "search_knowledge_base" in names and len(names) >= 5


def test_empty_whitelist_means_no_tools():
    """[] 是显式「无工具」，与 None（不限制）严格区分。"""
    assert _active_names(AgentConfig(tool_whitelist=[])) == set()


def test_whitelist_intersects_with_tags_and_disabled():
    tags_only = _active_names(
        AgentConfig(
            tool_tags=["analysis"], tool_whitelist=["search_knowledge_base", "extract_insights"]
        )
    )
    assert tags_only == {"extract_insights"}, "白名单不得绕过 tag 过滤"

    disabled = _active_names(
        AgentConfig(tool_whitelist=["list_documents"], disabled_tools=["list_documents"])
    )
    assert disabled == set(), "白名单不得绕过 disabled_tools"


def test_whitelist_does_not_accept_legacy_alias_names():
    """名单按规范名匹配：写旧名（vector_search）不生效（#509 口径）。"""
    assert _active_names(AgentConfig(tool_whitelist=["vector_search"])) == set()


def test_whitelist_intersects_with_enabled_predicate():
    assert _active_names(AgentConfig(tool_whitelist=["delegate_subtask"])) == set()
    assert _active_names(
        AgentConfig(tool_whitelist=["delegate_subtask"], enable_delegation=True)
    ) == {"delegate_subtask"}


# ── 执行层（planner 幻觉调用）────────────────────────────────────────────


def _client_with_tool_call(tool_name: str, raw_arguments: str | None = "{}"):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        tc = MagicMock()
        tc.id = "tc-x"
        tc.function.name = tool_name
        tc.function.arguments = raw_arguments
        msg.tool_calls = [tc]
        msg.content = ""
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _mk_executor(config: AgentConfig, client) -> Executor:
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    return Executor(client, config, memory)


async def _run(executor):
    step = PlanStep(id="s1", description="执行子任务")
    plan = Plan(id="p1", goal="g", steps=[step])
    events = []
    async for ev in executor._execute_step_once(
        step=step,
        plan=plan,
        history=None,
        organization_id=2,
        user_id=0,
        enable_thinking=False,
    ):
        events.append(ev)
    return events


class _Ok:
    success = True
    error = None

    def __str__(self):
        return "ok"


@pytest.mark.asyncio
async def test_whitelisted_context_rejects_hallucinated_tool():
    """模型凭空发出名单外工具：执行层拒绝且不触达 registry。"""
    client = _client_with_tool_call("delegate_subtask")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_whitelist=["search_knowledge_base"],
        max_tool_calls_per_turn=3,
    )
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events = await _run(_mk_executor(cfg, client))

    assert spy.await_count == 0, "名单外工具绝不能触达 registry"
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "tool_not_allowed"
    assert "delegate_subtask" in errs[0].content


@pytest.mark.asyncio
async def test_empty_whitelist_rejects_even_common_tool():
    client = _client_with_tool_call("search_knowledge_base")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_whitelist=[],
        max_tool_calls_per_turn=3,
    )
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events = await _run(_mk_executor(cfg, client))
    assert spy.await_count == 0
    assert any(e.type == "tool_error" and e.error_type == "tool_not_allowed" for e in events)


@pytest.mark.asyncio
async def test_whitelisted_tool_executes_normally():
    client = _client_with_tool_call("delegate_subtask")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_whitelist=["delegate_subtask"],
        enable_delegation=True,
        max_tool_calls_per_turn=3,
    )
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Ok())) as spy:
        events = await _run(_mk_executor(cfg, client))
    assert spy.await_count == 1, "名单内工具应正常执行"
    assert any(e.type == "tool_result" for e in events)


@pytest.mark.asyncio
async def test_whitelist_none_keeps_old_execution_behavior():
    client = _client_with_tool_call("delegate_subtask")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        enable_delegation=True,
        max_tool_calls_per_turn=3,
    )
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Ok())) as spy:
        await _run(_mk_executor(cfg, client))
    assert spy.await_count == 1, "NULL 白名单 = 不限制（旧行为不变）"


# ── AgentService 注入（不污染持久化配置）─────────────────────────────────


@pytest.fixture
async def db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


class _FakeLoop:
    """捕获 PERAgentLoop 实际收到的运行配置；不产生事件。"""

    captured: dict = {}

    def __init__(self, *, openai_client, config, organization_id, user_id):
        type(self).captured["config"] = config
        type(self).captured["organization_id"] = organization_id
        self.config = config
        self.last_execution_context = None
        self.run_failed = False
        self.run_failure = None
        self._final_commit_persisted = False
        self.cancel_check = None

    def _get_tools(self):
        return []

    async def run(self, query, **kwargs):
        if False:  # pragma: no cover - 保持 async generator 语义
            yield None
        return


async def _run_chat(session, *, organization_id: int, base: AgentConfig):
    service = AgentService(openai_client=MagicMock())
    events = []
    with patch("app.agent.service.PERAgentLoop", new=_FakeLoop):
        async for event in service.chat(
            query="q",
            organization_id=organization_id,
            user_id=1,
            session_id="s1",
            config=base,
            db=session,
        ):
            events.append(event)
    return events


@pytest.fixture(autouse=True)
def _reset_fake_loop():
    _FakeLoop.captured = {}
    yield
    _FakeLoop.captured = {}


async def test_agent_service_injects_org_whitelist_without_polluting_config(db, monkeypatch):
    db.add(
        OrganizationSettings(
            organization_id=1,
            tool_whitelist='["search_knowledge_base"]',
        )
    )
    await db.commit()

    saved: dict = {}

    async def _fake_save(self, session_id, ttl=86400 * 7):
        saved["config"] = self

    monkeypatch.setattr(AgentConfig, "save_to_redis", _fake_save)
    base = AgentConfig(enable_checkpoint=False)
    await _run_chat(db, organization_id=1, base=base)

    run_config = _FakeLoop.captured["config"]
    assert run_config is not base, "组织策略必须注入配置副本"
    assert run_config.tool_whitelist == ["search_knowledge_base"]
    assert base.tool_whitelist is None, "不得改写调用方/会话配置本体"
    assert saved["config"].tool_whitelist is None, "组织白名单不得写回会话 Redis 持久化配置"


async def test_agent_service_null_whitelist_is_unrestricted(db):
    db.add(OrganizationSettings(organization_id=1))
    await db.commit()
    await _run_chat(db, organization_id=1, base=AgentConfig(enable_checkpoint=False))
    assert _FakeLoop.captured["config"].tool_whitelist is None


async def test_agent_service_invalid_json_treated_as_unset(db, caplog):
    db.add(OrganizationSettings(organization_id=1, tool_whitelist="not-json"))
    await db.commit()
    with caplog.at_level(logging.WARNING):
        await _run_chat(db, organization_id=1, base=AgentConfig(enable_checkpoint=False))
    assert _FakeLoop.captured["config"].tool_whitelist is None
    assert any("不是合法 JSON" in record.message for record in caplog.records)


async def test_agent_service_read_failure_fails_open(db, caplog):
    broken = MagicMock()
    broken.execute = MagicMock(side_effect=RuntimeError("settings db down"))
    with caplog.at_level(logging.WARNING):
        await _run_chat(broken, organization_id=1, base=AgentConfig(enable_checkpoint=False))
    assert _FakeLoop.captured["config"].tool_whitelist is None
    assert any("fail-open" in record.message for record in caplog.records)


async def test_agent_service_cross_org_isolation(db):
    db.add(
        OrganizationSettings(
            organization_id=1,
            tool_whitelist='["search_knowledge_base"]',
        )
    )
    await db.commit()
    await _run_chat(db, organization_id=1, base=AgentConfig(enable_checkpoint=False))
    assert _FakeLoop.captured["config"].tool_whitelist == ["search_knowledge_base"]

    await _run_chat(db, organization_id=2, base=AgentConfig(enable_checkpoint=False))
    assert _FakeLoop.captured["config"].tool_whitelist is None, "B 组织不得继承 A 组织的白名单"


# ── 合并语义：结果永不比任一方更宽 ─────────────────────────────────────


def test_merge_tool_whitelists_never_widens_either_side():
    from app.services.organization_settings_service import OrganizationSettingsService

    merge = OrganizationSettingsService.merge_tool_whitelists
    assert merge(None, ["a"]) == ["a"], "org 未限制时保留调用方更严格名单"
    assert merge(["a", "b"], None) == ["a", "b"], "调用方未限制时采用组织名单"
    assert merge(["a", "b"], ["b", "c"]) == ["b"], "两边都有取交集"
    assert merge([], ["a"]) == [], "组织显式 [] 不得被调用方放宽"
    assert merge(["a"], []) == [], "调用方显式 [] 不得被组织放宽"
    assert merge(None, None) is None


async def test_agent_service_keeps_caller_whitelist_when_org_unset(db):
    base = AgentConfig(enable_checkpoint=False, tool_whitelist=["list_documents"])
    await _run_chat(db, organization_id=1, base=base)
    assert _FakeLoop.captured["config"].tool_whitelist == ["list_documents"]
    assert base.tool_whitelist == ["list_documents"], "不得改写调用方配置"


async def test_agent_service_intersects_caller_and_org_whitelist(db):
    db.add(
        OrganizationSettings(
            organization_id=1,
            tool_whitelist='["search_knowledge_base", "list_documents"]',
        )
    )
    await db.commit()
    base = AgentConfig(enable_checkpoint=False, tool_whitelist=["list_documents"])
    await _run_chat(db, organization_id=1, base=base)
    assert _FakeLoop.captured["config"].tool_whitelist == ["list_documents"], (
        "组织策略不得放宽调用方更严格的限制"
    )
    assert base.tool_whitelist == ["list_documents"]


async def test_agent_service_empty_org_whitelist_wins_over_caller(db):
    db.add(OrganizationSettings(organization_id=1, tool_whitelist="[]"))
    await db.commit()
    base = AgentConfig(enable_checkpoint=False, tool_whitelist=["list_documents"])
    await _run_chat(db, organization_id=1, base=base)
    assert _FakeLoop.captured["config"].tool_whitelist == [], (
        "组织显式「无工具」不得被调用方名单放宽"
    )


# ── M3：子代理白名单继承 + token 归因 ──────────────────────────────────


class _ChildConfigStub:
    captured: dict = {}

    def __init__(
        self, *, openai_client, config, organization_id, user_id, internal_system_task=False
    ):
        type(self).captured["config"] = config

    async def run(self, task):
        if False:  # pragma: no cover - async generator 语义
            yield None
        return


@pytest.mark.asyncio
async def test_child_inherits_parent_whitelist_and_cannot_see_web_search(monkeypatch):
    """父 ["search_knowledge_base","delegate_subtask"] → 子只看得到前者。"""
    _ChildConfigStub.captured = {}
    monkeypatch.setattr(subagent, "PERAgentLoop", _ChildConfigStub)
    monkeypatch.setattr(subagent, "_record_subagent_usage", AsyncMock())

    async for _ in subagent.delegate_task(
        client=MagicMock(),
        task="子任务",
        organization_id=1,
        user_id=9,
        tool_whitelist=["search_knowledge_base", "delegate_subtask"],
    ):
        pass

    child_config = _ChildConfigStub.captured["config"]
    assert child_config.tool_whitelist == ["search_knowledge_base", "delegate_subtask"]
    names = {entry.name for entry in tool_registry.list_active_tools(child_config)}
    assert "search_knowledge_base" in names
    assert "web_search" not in names, "父白名单未列出的工具子代理不得看到"
    assert "delegate_subtask" not in names, "tag 限制（递归防护）必须同时生效"


@pytest.mark.asyncio
async def test_child_without_parent_whitelist_keeps_old_tool_surface(monkeypatch):
    _ChildConfigStub.captured = {}
    monkeypatch.setattr(subagent, "PERAgentLoop", _ChildConfigStub)
    monkeypatch.setattr(subagent, "_record_subagent_usage", AsyncMock())

    async for _ in subagent.delegate_task(
        client=MagicMock(), task="子任务", organization_id=1, user_id=9
    ):
        pass

    child_config = _ChildConfigStub.captured["config"]
    assert child_config.tool_whitelist is None
    names = {entry.name for entry in tool_registry.list_active_tools(child_config)}
    assert "web_search" in names, "NULL = 父未限制，旧工具面不变"


@pytest.mark.asyncio
async def test_child_cannot_execute_web_search_under_whitelist():
    """执行层复核：tag+白名单交集后，模型幻觉发出的 web_search 被拒。"""
    client = _client_with_tool_call("web_search")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_tags=subagent.SUBAGENT_ALLOWED_TAGS,
        tool_whitelist=["search_knowledge_base", "delegate_subtask"],
        max_tool_calls_per_turn=3,
    )
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        events = await _run(_mk_executor(cfg, client))
    assert spy.await_count == 0
    assert any(e.type == "tool_error" and e.error_type == "tool_not_allowed" for e in events)


@pytest.mark.asyncio
async def test_executor_run_context_carries_tool_whitelist():
    client = _client_with_tool_call("search_knowledge_base")
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        tool_whitelist=["search_knowledge_base"],
        max_tool_calls_per_turn=3,
    )
    with patch.object(tool_registry, "execute_detailed", new=AsyncMock(return_value=_Ok())) as spy:
        await _run(_mk_executor(cfg, client))
    run_context = spy.await_args.kwargs["run_context"]
    assert isinstance(run_context, RunContext)
    assert run_context.extra["tool_whitelist"] == ["search_knowledge_base"]


@pytest.mark.asyncio
async def test_delegation_tool_forwards_whitelist_from_run_context(monkeypatch):
    captured: dict = {}

    async def _fake_parallel(**kwargs):
        captured.update(kwargs)
        return ["ok"]

    monkeypatch.setattr("app.agent.subagent.run_parallel_subtasks", _fake_parallel)
    monkeypatch.setattr(
        "app.dependencies.get_rag_pipeline",
        lambda: SimpleNamespace(openai_client=MagicMock()),
    )
    run_context = RunContext(
        user_id=9,
        organization_id=1,
        extra={"tool_whitelist": ["search_knowledge_base", "delegate_subtask"]},
    )
    result = await tool_registry.execute_detailed(
        "delegate_subtask",
        {"tasks": ["查资料"]},
        organization_id=1,
        user_id=9,
        run_context=run_context,
    )
    assert result.success, result.error
    assert captured["tool_whitelist"] == ["search_knowledge_base", "delegate_subtask"]


@pytest.mark.asyncio
async def test_subagent_usage_recorded_even_when_child_fails(monkeypatch):
    """子任务异常收尾：已发生的 token 仍要记账（finally 路径）。"""

    class _FailingChild:
        def __init__(self, **kwargs):
            self.last_execution_context = SimpleNamespace(usage_by_model={"m-child": (4, 6)})

        async def run(self, task):
            if False:  # pragma: no cover
                yield None
            raise RuntimeError("child boom")

    class _Ctx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(subagent, "PERAgentLoop", _FailingChild)
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: _Ctx())
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", _capture)

    with pytest.raises(RuntimeError, match="child boom"):
        async for _ in subagent.delegate_task(
            client=MagicMock(), task="t", organization_id=7, user_id=202
        ):
            pass

    assert len(calls) == 1
    assert calls[0]["source"] == "subagent"
    assert (calls[0]["input_tokens"], calls[0]["output_tokens"]) == (4, 6)


@pytest.mark.asyncio
async def test_subagent_usage_recorded_without_duplicating_parent(monkeypatch):
    """子代理独立 ExecutionContext 的用量写 source=subagent；每个 model 一次。"""

    class _UsageChild:
        def __init__(self, **kwargs):
            self.last_execution_context = SimpleNamespace(
                usage_by_model={"m-child": (10, 5), "m-child-2": (1, 2)}
            )

        async def run(self, task):
            if False:  # pragma: no cover
                yield None
            return

    class _Ctx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(subagent, "PERAgentLoop", _UsageChild)
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: _Ctx())
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", _capture)

    async for _ in subagent.delegate_task(
        client=MagicMock(), task="t", organization_id=7, user_id=202
    ):
        pass

    assert len(calls) == 2, "每个 model 一条记录"
    by_model = {c["model"]: c for c in calls}
    assert by_model["m-child"]["input_tokens"] == 10
    assert by_model["m-child"]["output_tokens"] == 5
    for call in calls:
        assert call["source"] == "subagent"
        assert call["feature"] == "subagent"
        assert call["organization_id"] == 7
        assert call["user_id"] == 202
