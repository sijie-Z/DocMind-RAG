"""批次七集成：工具结果里的显式工件载荷 → 执行器落库 + SSE artifact 事件。

判别点（变异点写在各用例里）：
- 同一工具调用重复执行（resume 重放）只落**一行**工件，且第二次不再发
  「新建」事件（去掉 (task, call) 幂等 → 红）；
- 普通长工具结果**不**产生工件，且 tool_ledger 的 500 字符截断语义保持不变
  （把普通结果也当工件落库 / 或把 RESULT_CAP 放大 → 红）；
- 工件行挂上 task_id / conversation_id / source_call_id / source_tool；
- 存储故障时不打断 run（tool_result 照发，artifact 事件缺席）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agent.service  # noqa: F401 — 触发全部工具注册
import app.models  # noqa: F401
from app.agent import checkpoint, tool_ledger
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolMeta, ToolResult, tool_registry
from app.core.database import Base
from app.models.artifact import AgentArtifact
from app.models.organization import Organization
from app.services import artifact_service

ORG = 5
USER = 9


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def __bool__(self):
        return True

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        return key in self.store

    async def get(self, key):
        return self.store.get(key)


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifact-exec.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=USER))
        await db.commit()
    monkeypatch.setattr(tool_ledger, "AsyncSessionLocal", factory)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    monkeypatch.setattr(checkpoint, "redis_client", _FakeRedis())
    # 执行器（_record_artifact）用模块级 AsyncSessionLocal 自建会话。
    import app.core.database as db_module

    monkeypatch.setattr(db_module, "AsyncSessionLocal", factory)
    monkeypatch.setattr("app.agent.executor.AsyncSessionLocal", factory, raising=False)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    store: dict[str, bytes] = {}

    def _put(object_name, data, length, content_type="application/octet-stream", **kwargs):
        store[object_name] = data.read()
        return object_name

    monkeypatch.setattr(artifact_service.minio_client, "put_object", _put)
    return store


def _client_with_tool_call(
    tool_name: str, raw_arguments: str, call_id: str = "tc-artifact"
) -> MagicMock:
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        tc = MagicMock()
        tc.id = call_id
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


def _payload_arguments() -> str:
    return json.dumps(
        {
            "title": "季度报告",
            "content": "# 季度报告\n\n正文内容",
            "kind": "markdown",
        },
        ensure_ascii=False,
    )


async def _run_executor(
    db_factory, *, tool_name: str, arguments: str, task_id: str, call_id: str = "tc-artifact"
):
    # tool_tags 显式 opt-in：create_artifact 是扩展面工具（enabled_predicate），
    # 不显式解锁时执行层会 fail-closed 拒绝——测试必须走真实生产门控语义。
    cfg = AgentConfig(
        enable_planning=False,
        enable_reflection=False,
        max_tool_calls_per_turn=3,
        tool_tags=["artifact"],
    )
    client = _client_with_tool_call(tool_name, arguments, call_id=call_id)
    # 生产口径：service.chat 把 config.agent_id 设为 session:<会话 id>，执行器据此
    # 取 conversation_id（_hook_session_id 剥前缀）。
    cfg.agent_id = "session:conv-artifact"
    executor = Executor(
        client,
        cfg,
        AgentMemoryBridge(agent_id=cfg.agent_id, organization_id=ORG, user_id=USER),
    )
    executor.tool_ledger_task_id = task_id
    executor.internal_system_task = False
    step = PlanStep(id="s1", description="产出工件")
    plan = Plan(id="p1", goal="g", steps=[step])
    events = []
    async for ev in executor._execute_step_once(
        step=step,
        plan=plan,
        history=None,
        organization_id=ORG,
        user_id=USER,
        enable_thinking=False,
    ):
        events.append(ev)
    return events


async def _artifacts(factory) -> list[AgentArtifact]:
    async with factory() as db:
        return list((await db.execute(select(AgentArtifact))).scalars().all())


async def test_explicit_payload_persists_one_artifact_and_emits_event(db_factory):
    """显式工件载荷 → 恰好一行工件 + 一枚 artifact 事件（挂 task/call/tool 溯源）。"""
    task_id = await checkpoint.create_task(user_id=USER, organization_id=ORG, query="产出报告")

    events = await _run_executor(
        db_factory, tool_name="create_artifact", arguments=_payload_arguments(), task_id=task_id
    )

    artifact_events = [e for e in events if e.type == "artifact"]
    assert len(artifact_events) == 1, "一次显式载荷只发一枚 artifact 事件"
    evt = artifact_events[0]
    assert evt.artifact_title == "季度报告"
    assert evt.artifact_kind == "markdown"
    assert evt.artifact_version == 1
    assert evt.artifact_source_tool == "create_artifact"
    assert evt.artifact_origin == "tool"
    assert evt.artifact_created is True

    rows = await _artifacts(db_factory)
    assert len(rows) == 1, "一次性落盘：既不能漏行，也不能双写"
    row = rows[0]
    assert row.id == evt.artifact_id
    assert row.task_id == task_id
    assert row.source_call_id, "必须挂 call_id（幂等键的一半）"
    assert row.source_tool == "create_artifact"
    assert row.conversation_id == "conv-artifact"
    assert row.content_sha256 == artifact_service.content_sha256("# 季度报告\n\n正文内容")

    # 普通工具结果照常外发（artifact 事件是追加，不是替代）。
    assert any(e.type == "tool_result" for e in events)


async def test_replay_same_call_does_not_duplicate_artifact(db_factory):
    """同一工具调用 replay：第二次不新建版本、只回发既有工件（幂等键生效）。"""
    task_id = await checkpoint.create_task(user_id=USER, organization_id=ORG, query="重放")

    first = await _run_executor(
        db_factory, tool_name="create_artifact", arguments=_payload_arguments(), task_id=task_id
    )
    second = await _run_executor(
        db_factory, tool_name="create_artifact", arguments=_payload_arguments(), task_id=task_id
    )

    first_evt = next(e for e in first if e.type == "artifact")
    second_evt = next(e for e in second if e.type == "artifact")
    assert second_evt.artifact_id == first_evt.artifact_id
    assert second_evt.artifact_created is False

    rows = await _artifacts(db_factory)
    assert len(rows) == 1, "replay 不得重复落库（(task_id, source_call_id) 幂等）"


async def test_plain_long_tool_result_still_clips_at_500_and_makes_no_artifact(db_factory):
    """回归守卫：普通长结果既不落工件，也保持 tool_ledger 的 500 字符截断。"""
    task_id = await checkpoint.create_task(user_id=USER, organization_id=ORG, query="普通结果")
    long_text = "普通工具输出 " * 200  # 远超 500 字符，但**没有**显式工件标记

    async def _fake_execute(name, arguments, **context):
        return ToolResult.ok(data=long_text)

    with patch.object(tool_registry, "execute_detailed", new=_fake_execute):
        events = await _run_executor(
            db_factory,
            tool_name="create_artifact",  # 工具名不重要：载荷才是判据
            arguments=json.dumps({"title": "x", "content": "y"}),
            task_id=task_id,
        )

    assert not [e for e in events if e.type == "artifact"], "普通结果不得产生工件事件"
    assert await _artifacts(db_factory) == []

    # tool_ledger 的 500 字符截断语义不变（RESULT_CAP）。
    async with db_factory() as db:
        from app.models.agent_tool_call import AgentToolResult

        results = list((await db.execute(select(AgentToolResult))).scalars().all())
    assert len(results) == 1
    assert len(results[0].result_summary) == tool_ledger.RESULT_CAP
    assert tool_ledger.RESULT_CAP == 500


async def test_storage_failure_does_not_break_run(db_factory, monkeypatch):
    """存储故障：run 不被打断（tool_result 照发），artifact 事件缺席（fail-open）。"""
    task_id = await checkpoint.create_task(user_id=USER, organization_id=ORG, query="存储故障")

    def _boom(*args, **kwargs):
        raise ConnectionError("minio down")

    monkeypatch.setattr(artifact_service.minio_client, "put_object", _boom)

    events = await _run_executor(
        db_factory, tool_name="create_artifact", arguments=_payload_arguments(), task_id=task_id
    )

    assert any(e.type == "tool_result" for e in events), "工具成功结果必须照常外发"
    assert not [e for e in events if e.type == "artifact"]
    assert await _artifacts(db_factory) == []


async def test_version_two_through_executor_chain(db_factory):
    """同标题不同内容的两枚不同调用 → v1/v2 链（replaces_artifact_id 串起来）。"""
    task_id = await checkpoint.create_task(user_id=USER, organization_id=ORG, query="改版")
    first = await _run_executor(
        db_factory, tool_name="create_artifact", arguments=_payload_arguments(), task_id=task_id
    )

    changed = json.dumps(
        {"title": "季度报告", "content": "# 季度报告 v2", "kind": "markdown"},
        ensure_ascii=False,
    )

    # 第二个调用：换 call_id（不同工具调用），执行器会分配新的 call id。
    client_calls = {"n": 0}

    async def _fake_execute(name, arguments, **context):
        client_calls["n"] += 1
        return ToolResult.ok(
            data="ok",
            meta=ToolMeta(
                extra={
                    "artifact": {
                        "kind": "artifact/v1",
                        "artifact_kind": "markdown",
                        "title": "季度报告",
                        "content": "# 季度报告 v2",
                    }
                }
            ),
        )

    with patch.object(tool_registry, "execute_detailed", new=_fake_execute):
        second = await _run_executor(
            db_factory,
            tool_name="create_artifact",
            arguments=changed,
            task_id=task_id,
            call_id="tc-artifact-2",  # 不同工具调用 → 允许新版本
        )

    first_evt = next(e for e in first if e.type == "artifact")
    second_evt = next(e for e in second if e.type == "artifact")
    assert second_evt.artifact_version == 2
    assert second_evt.artifact_id != first_evt.artifact_id
    rows = {row.id: row for row in await _artifacts(db_factory)}
    assert rows[second_evt.artifact_id].replaces_artifact_id == first_evt.artifact_id


async def test_unwired_tool_persists_itself(db_factory):
    """未接线（无 task_id）路径：工具自行落库，执行器按 meta.artifactId 读回、不重复落库。"""
    executor = Executor(
        MagicMock(),
        AgentConfig(enable_planning=False, enable_reflection=False),
        AgentMemoryBridge(agent_id="session:conv-direct", organization_id=ORG, user_id=USER),
    )
    # 不设置 tool_ledger_task_id → 工具走自行落库分支。
    payload = {
        "kind": "artifact/v1",
        "artifact_kind": "json",
        "title": "数据文件",
        "content": '{"a": 1}',
    }
    raw = SimpleNamespace(meta=ToolMeta(extra={"artifact": payload}), data=None)
    events = []
    evt = await executor._record_artifact(
        tool_name="create_artifact",
        call_id="",
        raw_result=raw,
        organization_id=ORG,
        user_id=USER,
    )
    # 执行器看到 strip 后的载荷仍会落库（meta 通道没有 artifactId 时）。
    if evt is not None:
        events.append(evt)
    rows = await _artifacts(db_factory)
    assert len(rows) == 1, "meta 通道载荷必须被落库一次"
    assert rows[0].kind == "json"
    assert evt is not None and evt.artifact_id == rows[0].id
