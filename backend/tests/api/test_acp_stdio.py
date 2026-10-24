"""ACP v1 stdio 端到端测试（issue #481 §8）。

架构：真实 ``AcpServer.run()`` 连接循环 + 内存管道（:class:`InMemoryAcpTransport`）
+ sqlite（``Base.metadata.create_all``）+ :class:`FakeAgentService`。fake service
只替代 LLM/PER 循环，ACP 侧的队列、串行 sender、审批双向请求、取消、DB 落库
全部走真实代码；真实 LLM 的 e2e 由具备 key 的环境执行（本文件不依赖网络）。

另含真实入口子进程用例：``python -m app --profile acp`` 无身份 → exit 2、
stdout 0 字节；有效身份（sqlite）→ 握手/new/close 全链、stdout 只有协议帧。
"""

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import jsonschema
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.acp.server import AcpServer, IdentityError, validate_identity
from app.agent import checkpoint
from app.agent.events import AgentEvent
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus
from app.models.chat import ChatMessage, ChatSession
from app.models.organization import Organization
from app.models.user import User

BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fixtures"))

from acp_client import AcpClient, InMemoryAcpTransport  # noqa: E402

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "acp_v1_schema.json"
ORG_ID = 1
USER_ID = 7


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# ── sqlite / fake service ───────────────────────────────────────────────────


class FakeRedis:
    def __bool__(self):
        return True

    def __init__(self):
        self.store: dict[str, str] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1


@pytest.fixture
async def sqlite_env(tmp_path, monkeypatch):
    db_path = tmp_path / "acp.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        user = User(
            id=USER_ID,
            username="acp-user",
            email="acp@example.com",
            hashed_password="x",
            is_active=True,
            organization_id=None,
        )
        db.add(user)
        await db.commit()
        db.add(Organization(id=ORG_ID, name="acp-org", owner_id=USER_ID))
        await db.commit()
        user.organization_id = ORG_ID
        await db.commit()
        inactive = User(
            id=USER_ID + 1,
            username="acp-inactive",
            email="inactive@example.com",
            hashed_password="x",
            is_active=False,
            organization_id=ORG_ID,
        )
        db.add(inactive)
        other = User(
            id=USER_ID + 2,
            username="acp-other",
            email="other@example.com",
            hashed_password="x",
            is_active=True,
            organization_id=ORG_ID,
        )
        db.add(other)
        await db.commit()
    # checkpoint 的真实收尾路径（mark_terminal/load_task/request_cancel）打到
    # 同一个 sqlite；Redis 用内存替身，绝不触网。
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    monkeypatch.setattr(checkpoint, "redis_client", FakeRedis())
    yield SimpleNamespace(factory=factory, path=db_path, engine=engine)
    await engine.dispose()


def script(*events):
    """固定事件脚本 → async generator 工厂。"""

    async def _gen():
        for event in events:
            yield event

    return _gen


class FakeAgentService:
    """替代 AgentService：脚本化事件流；不碰 LLM / 真实工具。

    额外记录「生成器是否被消费到尽」：service.chat 的真实实现在 yield 之后
    还要执行任务收尾（COMPLETED/CANCELLED/FAILED）；消费者若提前 return，
    收尾永不执行且 GC 会走 GeneratorExit 兜底——`chat_exhausted` /
    `chat_closed_early` 用于钉住「必须排干事件流」的纪律。
    """

    def __init__(self):
        self.chat_factories: list = []
        self.resume_factories: list = []
        self.chat_calls: list[dict] = []
        self.resume_calls: list[dict] = []
        self.prepare_calls: list[dict] = []
        self.chat_exhausted = False
        self.chat_closed_early = False
        self.resume_started_with_chat_exhausted: bool | None = None

    def add_chat(self, factory) -> None:
        self.chat_factories.append(factory)

    def add_resume(self, factory) -> None:
        self.resume_factories.append(factory)

    def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        factory = self.chat_factories.pop(0)
        service = self

        async def _gen():
            try:
                async for event in factory():
                    yield event
            except GeneratorExit:
                service.chat_closed_early = True
                raise
            else:
                service.chat_exhausted = True

        return _gen()

    def resume_chat(self, task_id, user_id, *, config=None, claim=True, document_context=None):
        self.resume_calls.append({"task_id": task_id, "claim": claim})
        factory = self.resume_factories.pop(0)
        service = self

        async def _gen():
            # 续跑流开始迭代时，旧 chat 流必须已经被消费到尽（否则旧生成器
            # 的断流兜底会在 resume 开跑后覆盖快照/误判 FAILED）。
            service.resume_started_with_chat_exhausted = service.chat_exhausted
            async for event in factory():
                yield event

        return _gen()

    async def prepare_resume(self, task_id, user_id, *, organization_id=None):
        self.prepare_calls.append({"task_id": task_id, "organization_id": organization_id})
        return (None, None)


@asynccontextmanager
async def acp_env(
    sqlite_env,
    *,
    service: FakeAgentService | None = None,
    permission_timeout: float = 5.0,
    plan_approval_mode: str = "reject",
):
    transport = InMemoryAcpTransport()
    server = AcpServer(
        organization_id=ORG_ID,
        user_id=USER_ID,
        db_factory=sqlite_env.factory,
        agent_service_obj=service or FakeAgentService(),
        stdin=transport.server_stdin,
        stdout=transport.server_stdout,
        redirect_stdout=False,
        install_signal_handlers=False,
        permission_timeout=permission_timeout,
        plan_approval_mode=plan_approval_mode,
    )
    server_task = asyncio.create_task(server.run())
    client = AcpClient(write_line=transport.client_write, read_line=transport.client_read)
    try:
        yield SimpleNamespace(
            server=server, client=client, transport=transport, service=server.service
        )
    finally:
        transport.close_stdin()
        try:
            await asyncio.wait_for(server_task, timeout=15)
        except TimeoutError:  # pragma: no cover - 测试收尾兜底
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        transport.close()


async def _new_session(client: AcpClient) -> str:
    response = await client.request("session/new", {"cwd": "/tmp", "mcpServers": []})
    return response["result"]["sessionId"]


def _validate_update(schema: dict, update: dict) -> None:
    variant = update["sessionUpdate"]
    jsonschema.validate(update, schema["sessionUpdates"][variant])


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


# ── 握手 / 会话方法 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_new_list_close(sqlite_env, schema):
    async with acp_env(sqlite_env) as env:
        client = env.client
        handshake = await client.initialize()
        jsonschema.validate(handshake["result"], schema["methods"]["initialize"]["response"])

        new_response = await client.request("session/new", {"cwd": "/tmp", "mcpServers": []})
        jsonschema.validate(new_response["result"], schema["methods"]["session/new"]["response"])
        session_id = new_response["result"]["sessionId"]
        async with sqlite_env.factory() as db:
            row = await db.get(ChatSession, session_id)
        assert row is not None and row.user_id == USER_ID and row.organization_id == ORG_ID

        listed = await client.request("session/list", {})
        jsonschema.validate(listed["result"], schema["methods"]["session/list"]["response"])
        assert session_id in [item["sessionId"] for item in listed["result"]["sessions"]]

        closed = await client.request("session/close", {"sessionId": session_id})
        jsonschema.validate(closed["result"], schema["methods"]["session/close"]["response"])

        # 关闭只解除映射：DB 行仍在；再 prompt → -32004。
        async with sqlite_env.factory() as db:
            assert await db.get(ChatSession, session_id) is not None
        response = await client.prompt(session_id, "hi")
        assert response["error"]["code"] == -32004

        loaded = await client.request("session/load", {"sessionId": session_id})
        assert loaded["error"]["code"] == -32601
        assert env.service.chat_calls == []


@pytest.mark.asyncio
async def test_set_config_option_persists_model_and_plan_mode(sqlite_env, schema):
    async with acp_env(sqlite_env) as env:
        session_id = await _new_session(env.client)
        response = await env.client.request(
            "session/set_config_option",
            {"sessionId": session_id, "option": "model", "value": "deepseek-v4-pro"},
        )
        jsonschema.validate(
            response["result"], schema["methods"]["session/set_config_option"]["response"]
        )
        await env.client.request(
            "session/set_config_option",
            {"sessionId": session_id, "option": "plan_mode", "value": "always"},
        )
        async with sqlite_env.factory() as db:
            row = await db.get(ChatSession, session_id)
        assert row.settings["model"] == "deepseek-v4-pro"
        assert row.settings["plan_approval_mode"] == "always"

        bad = await env.client.request(
            "session/set_config_option",
            {"sessionId": session_id, "option": "temperature", "value": 1},
        )
        assert bad["error"]["code"] == -32602
        bad_value = await env.client.request(
            "session/set_config_option",
            {"sessionId": session_id, "option": "plan_mode", "value": "sometimes"},
        )
        assert bad_value["error"]["code"] == -32602

        # 下一轮 prompt 读取：model / plan_approval_mode 进入本次 AgentConfig。
        env.service.add_chat(script(AgentEvent(type="done")))
        response = await env.client.prompt(session_id, "配置生效吗")
        assert response["result"] == {"stopReason": "end_turn"}
        config = env.service.chat_calls[-1]["config"]
        assert config.model == "deepseek-v4-pro"
        assert config.plan_approval_mode == "always"


# ── prompt：流式更新顺序 / 持久化 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_prompt_streams_updates_in_order_and_persists(sqlite_env, schema):
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(type="chunk", content="你好"),
            AgentEvent(type="tool_call", tool_name="code_execution", tool_call_id="call-1"),
            AgentEvent(
                type="tool_result",
                tool_name="code_execution",
                tool_call_id="call-1",
                content="42",
            ),
            AgentEvent(type="thinking", content="整理答案"),
            AgentEvent(type="done"),
        )
    )
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        response = await env.client.prompt(session_id, "问题")
        assert response["result"] == {"stopReason": "end_turn"}
        jsonschema.validate(response["result"], schema["methods"]["session/prompt"]["response"])
        # service 的收尾（持久化/终态）在 yield done 之后执行；必须排干事件流。
        assert service.chat_exhausted is True
        assert service.chat_closed_early is False

        variants = [update["sessionUpdate"] for update in env.client.updates]
        assert variants == [
            "agent_message_chunk",
            "tool_call",
            "tool_call_update",
            "agent_thought_chunk",
        ], "更新必须按事件顺序串行到达（消息 → 工具 pending → completed）"
        for update in env.client.updates:
            _validate_update(schema, update)
        assert env.client.updates[1]["toolCallId"] == "call-1"
        assert env.client.updates[2]["toolCallId"] == "call-1"
        assert env.client.updates[2]["status"] == "completed"

        async with sqlite_env.factory() as db:
            messages = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session_id)
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
        assert [(m.message_type.value, m.content) for m in messages] == [
            ("user", "问题"),
            ("assistant", "你好"),
        ]


@pytest.mark.asyncio
async def test_history_uses_most_recent_20_messages(sqlite_env):
    from app.acp.session import load_history
    from app.models.chat import ChatSessionStatus, MessageType

    async with sqlite_env.factory() as db:
        db.add(
            ChatSession(
                id="hist-session",
                user_id=USER_ID,
                organization_id=ORG_ID,
                status=ChatSessionStatus.ACTIVE,
            )
        )
        for index in range(25):
            db.add(
                ChatMessage(
                    id=f"m{index}",
                    session_id="hist-session",
                    seq=index,
                    content=f"msg-{index}",
                    message_type=MessageType.USER if index % 2 == 0 else MessageType.ASSISTANT,
                )
            )
        await db.commit()
    history = await load_history(sqlite_env.factory, "hist-session")
    assert len(history) == 20
    assert history[0]["content"] == "msg-5", "必须取最近 20 条（不是最早 20 条）"
    assert history[-1]["content"] == "msg-24"
    assert history[-1]["role"] == "user"


@pytest.mark.asyncio
async def test_prompt_error_event_returns_internal_error(sqlite_env):
    service = FakeAgentService()
    service.add_chat(
        script(AgentEvent(type="error", content="LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。"))
    )
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        response = await env.client.prompt(session_id, "hi")
        assert response["error"]["code"] == -32603
        assert "DEEPSEEK_API_KEY" in response["error"]["message"]
        assert service.chat_exhausted is True


@pytest.mark.asyncio
async def test_concurrent_prompt_returns_conflict(sqlite_env):
    service = FakeAgentService()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _blocking():
        started.set()
        await release.wait()
        yield AgentEvent(type="done")

    service.add_chat(_blocking)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        first = asyncio.create_task(env.client.prompt(session_id, "第一问"))
        await asyncio.wait_for(started.wait(), timeout=5)
        second = await env.client.prompt(session_id, "第二问")
        assert second["error"]["code"] == -32009
        release.set()
        assert (await first)["result"] == {"stopReason": "end_turn"}


@pytest.mark.asyncio
async def test_cancel_drops_late_updates_and_returns_cancelled(sqlite_env):
    service = FakeAgentService()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _cancel_script():
        yield AgentEvent(type="chunk", content="第一段")
        started.set()
        await release.wait()
        yield AgentEvent(type="chunk", content="迟到的内容")
        yield AgentEvent(type="run_cancelled")

    service.add_chat(_cancel_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "长任务"))
        await asyncio.wait_for(started.wait(), timeout=5)
        first_update = await env.client.wait_for_update()
        assert first_update["content"]["text"] == "第一段"

        await env.client.cancel(session_id)  # 通知，无 result
        await _wait_until(lambda: env.server.sessions.get(session_id).cancel_event.is_set())
        release.set()
        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}
        # 取消后不得再收到该会话的 update（迟到的 chunk 被丢弃）。
        texts = [
            update.get("content", {}).get("text")
            for update in env.client.updates
            if update["sessionUpdate"] == "agent_message_chunk"
        ]
        assert texts == ["第一段"]
        assert "迟到的内容" not in texts
        assert service.chat_exhausted is True
        assert service.chat_closed_early is False


@pytest.mark.asyncio
async def test_cancel_converges_db_to_cancelled_without_late_updates(sqlite_env, monkeypatch):
    """Given prompt 运行中，When 发 session/cancel，Then 5s 内收敛、DB CANCELLED、
    prompt stopReason=cancelled、无迟到 update（issue #494 验收条款）。"""
    # mark_terminal 的真实路径会顺带收敛工具账目/转向缓冲；本用例只关心任务
    # 状态机，跨模块旁路以 no-op 替身隔离（同断连用例）。
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())

    task_id = "task-cancel-live"
    service = FakeAgentService()
    started = asyncio.Event()

    async def _cancel_script():
        yield AgentEvent(type="chunk", content="第一段", task_id=task_id)
        started.set()
        # 模拟真实 AgentService：在检查点观察取消旗 → 落 CANCELLED 终态 → 终局。
        while not await checkpoint.is_cancel_requested(task_id):
            await asyncio.sleep(0.01)
        # 模拟"下一个检查点才收尾"的真实延迟：若 session/cancel 不等待 DB 收敛，
        # 取消响应会先于本行返回——DB 断言因此有判别力（变异试验会红）。
        await asyncio.sleep(0.3)
        await checkpoint.mark_terminal(
            task_id,
            TaskStatus.CANCELLED,
            error="用户取消（协作式取消在检查点收敛）",
            error_type="user_cancelled",
        )
        yield AgentEvent(type="chunk", content="迟到的内容", task_id=task_id)
        yield AgentEvent(type="run_cancelled", task_id=task_id)

    service.add_chat(_cancel_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.RUNNING,
                    query="长任务",
                )
            )
            await db.commit()

        prompt_task = asyncio.create_task(env.client.prompt(session_id, "长任务"))
        await asyncio.wait_for(started.wait(), timeout=5)
        first_update = await env.client.wait_for_update()
        assert first_update["content"]["text"] == "第一段"

        started_at = time.perf_counter()
        # 带 id 的请求形式：cancel 的诚实结果必须可观测（通知形态无响应）。
        cancel_response = await env.client.request(
            "session/cancel", {"sessionId": session_id}, timeout=10
        )
        elapsed = time.perf_counter() - started_at
        assert cancel_response["result"] == {"cancelled": True}
        assert elapsed < 5.5, f"cancel 必须在 5s 收敛窗口内返回（实测 {elapsed:.2f}s）"
        # 关键判别：cancel 返回时 DB **必须已经**是 CANCELLED——只置旗不等收敛
        # 的实现（修前行为）会在这里红（service 还在检查点之间跑）。
        async with sqlite_env.factory() as db:
            task_at_return = await db.get(AgentTask, task_id)
        assert task_at_return is not None and task_at_return.status == TaskStatus.CANCELLED, (
            "session/cancel 返回前必须等到 DB 收敛为 cancelled（不得只置旗就报成功）"
        )

        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}
        texts = [
            update.get("content", {}).get("text")
            for update in env.client.updates
            if update["sessionUpdate"] == "agent_message_chunk"
        ]
        assert texts == ["第一段"], "取消后不得再收到迟到 update"
        assert service.chat_exhausted is True, "取消也必须排干事件流（服务终态收尾）"
        assert service.chat_closed_early is False

    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, task_id)
    assert task is not None and task.status == TaskStatus.CANCELLED, (
        "session/cancel 必须真正让任务落库 cancelled（不是 COMPLETED）"
    )


@pytest.mark.asyncio
async def test_cancel_reports_32603_when_cancel_channel_unavailable(sqlite_env, monkeypatch):
    """Given 取消旗通道不可用（request_cancel False），When cancel，Then 如实 -32603。

    issue #494：不得像修前那样无条件返回 ``{"cancelled": true}`` 假报成功。
    """

    async def _request_cancel_false(_task_id: str) -> bool:
        return False

    monkeypatch.setattr(checkpoint, "request_cancel", _request_cancel_false)

    async with acp_env(sqlite_env) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-cancel-broken",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.RUNNING,
                    query="无法取消的任务",
                )
            )
            await db.commit()

        response = await env.client.request("session/cancel", {"sessionId": session_id})
        assert response["error"]["code"] == -32603
        assert "Redis" in response["error"]["message"]

    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, "task-cancel-broken")
    assert task is not None and task.status == TaskStatus.RUNNING, "不得假报取消成功"


@pytest.mark.asyncio
async def test_cancel_waits_for_task_row_registered_after_prompt_start(sqlite_env, monkeypatch):
    """Given prompt 已受理但任务行尚未创建，When cancel，Then 等行出现后真取消（评审 B1）。"""
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())

    task_id = "task-late-row"
    service = FakeAgentService()
    entered = asyncio.Event()
    allow_register = asyncio.Event()

    async def _late_script():
        # 模拟真实 service：生成器已开跑（prompt 锁被持有），但任务行尚未登记
        # （真实代码在 create_task 前还有 DB/Redis await）。
        entered.set()
        await allow_register.wait()
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=current_session_id,
                    status=TaskStatus.RUNNING,
                    query="晚登记的任务",
                )
            )
            await db.commit()
        yield AgentEvent(type="chunk", content="开始", task_id=task_id)
        while not await checkpoint.is_cancel_requested(task_id):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        await checkpoint.mark_terminal(
            task_id,
            TaskStatus.CANCELLED,
            error="用户取消（协作式取消在检查点收敛）",
            error_type="user_cancelled",
        )
        yield AgentEvent(type="run_cancelled", task_id=task_id)

    current_session_id = ""
    service.add_chat(_late_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        current_session_id = session_id
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "晚登记的任务"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        state = env.server.sessions.get(session_id)
        assert state is not None and state.lock.locked(), "prompt 必须已在运行"
        assert await env.server._latest_task_id(session_id) is None, "任务行此时确实还没登记"

        cancel_task = asyncio.create_task(
            env.client.request("session/cancel", {"sessionId": session_id}, timeout=10)
        )
        await asyncio.sleep(0.5)
        assert not cancel_task.done(), (
            "cancel 必须等待任务行登记，不得在'没有任务'时立即假报成功（评审 B1）"
        )
        allow_register.set()
        cancel_response = await cancel_task
        assert cancel_response["result"] == {"cancelled": True}

        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}

    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, task_id)
    assert task is not None and task.status == TaskStatus.CANCELLED, (
        "晚登记的任务也必须真正取消（不是跑完 COMPLETED）"
    )


@pytest.mark.asyncio
async def test_cancel_after_natural_completion_returns_not_found_and_end_turn(
    sqlite_env, monkeypatch
):
    """Given 任务已自然完成、prompt 仍在收尾，When cancel，Then 不假报成功、stopReason 不改写。

    评审 B2：终态任务对 ``get_latest_task_id`` 不可见，旧实现会（1）cancel 报成功、
    （2）把自然完成的 end_turn 改写成 cancelled。
    """
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())

    task_id = "task-natural-done"
    service = FakeAgentService()
    midway = asyncio.Event()
    release = asyncio.Event()

    async def _done_script():
        yield AgentEvent(type="chunk", content="前半段", task_id=task_id)
        # 真实 service 在 done yield 之后才 mark_terminal；这里在校验点之间落终态，
        # 等价构造「服务已过最后一个取消检查点、仍在产出」的输掉竞态窗口。
        await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED, error=None, error_type=None)
        midway.set()
        await release.wait()  # 等 cancel 请求处理完（-32004）
        yield AgentEvent(type="chunk", content="后半段", task_id=task_id)
        yield AgentEvent(type="done", task_id=task_id)

    service.add_chat(_done_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.RUNNING,
                    query="自然完成的任务",
                )
            )
            await db.commit()
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "自然完成的任务"))
        await asyncio.wait_for(midway.wait(), timeout=5)

        # 任务已终态：cancel 必须立即如实报错（不能等到锁释放/假报成功）。
        cancel_response = await env.client.request(
            "session/cancel", {"sessionId": session_id}, timeout=10
        )
        assert cancel_response["error"]["code"] == -32004, (
            "任务已自然完成 → cancel 必须如实报'没有进行中的任务'，不得假报成功"
        )
        release.set()
        response = await prompt_task
        assert response["result"] == {"stopReason": "end_turn"}, (
            "取消输掉竞态时 prompt 必须如实报 end_turn（不得改写为 cancelled）"
        )
        # 评审 B4：取消失败后 update 恢复外发，end_turn 落库必须是完整答案。
        texts = [
            update.get("content", {}).get("text")
            for update in env.client.updates
            if update["sessionUpdate"] == "agent_message_chunk"
        ]
        assert texts == ["前半段", "后半段"], "取消失败后不得继续抑制 update"
        async with sqlite_env.factory() as db:
            messages = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session_id)
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
        assistant = [m.content for m in messages if m.message_type.value == "assistant"]
        assert assistant == ["前半段后半段"], "取消输掉竞态时 end_turn 落库不得截断（评审 B4）"
    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, task_id)
    assert task is not None and task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancel_with_only_terminal_task_returns_not_found(sqlite_env):
    """评审 B2（probe4）：会话只有终态任务 → cancel 如实 -32004，不假报成功。"""
    async with acp_env(sqlite_env) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-already-done",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.COMPLETED,
                    query="已完成",
                )
            )
            await db.commit()
        response = await env.client.request("session/cancel", {"sessionId": session_id})
        assert response["error"]["code"] == -32004


@pytest.mark.asyncio
async def test_failed_cancel_resumes_updates_and_saves_full_answer(sqlite_env, monkeypatch):
    """Given 任务行在 cancel 预算内未登记，When cancel 诚实失败，Then run 仍可见且答案完整落库。

    评审 B5：取消失败后不得继续抑制 update（客户端静默、答案丢失）。
    """
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())

    task_id = "task-late-register-fail"
    service = FakeAgentService()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _script():
        # prompt 已持锁、生成器已开跑，但任务行在 cancel 的 5s 预算内都不登记
        # （真实等价：service 在 create_task 前的 Redis await 被拖长）。
        entered.set()
        await release.wait()
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=current_session_id,
                    status=TaskStatus.RUNNING,
                    query="晚登记且取消失败的任务",
                )
            )
            await db.commit()
        yield AgentEvent(type="chunk", content="前半段", task_id=task_id)
        await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED, error=None, error_type=None)
        yield AgentEvent(type="chunk", content="后半段", task_id=task_id)
        yield AgentEvent(type="done", task_id=task_id)

    current_session_id = ""
    service.add_chat(_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        current_session_id = session_id
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "q", timeout=30))
        await asyncio.wait_for(entered.wait(), timeout=5)

        started_at = time.perf_counter()
        cancel_response = await env.client.request(
            "session/cancel", {"sessionId": session_id}, timeout=10
        )
        elapsed = time.perf_counter() - started_at
        assert cancel_response["error"]["code"] == -32603
        assert "尚未登记" in cancel_response["error"]["message"]
        assert elapsed < 6.0, f"cancel 必须在 5s 预算内返回（实测 {elapsed:.2f}s）"

        release.set()
        response = await prompt_task
        assert response["result"] == {"stopReason": "end_turn"}
        texts = [
            update.get("content", {}).get("text")
            for update in env.client.updates
            if update["sessionUpdate"] == "agent_message_chunk"
        ]
        assert texts == ["前半段", "后半段"], (
            "cancel 诚实失败后 run 仍在跑：update 必须恢复外发（评审 B5）"
        )
        async with sqlite_env.factory() as db:
            messages = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session_id)
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
        assistant = [m.content for m in messages if m.message_type.value == "assistant"]
        assert assistant == ["前半段后半段"], "cancel 失败后自然完成的答案必须完整落库（评审 B5）"

    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, task_id)
    assert task is not None and task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancel_second_round_ignores_previous_terminal_task(sqlite_env, monkeypatch):
    """Given 会话已有上一轮 COMPLETED 任务，When 新一轮登记窗口内 cancel，
    Then 必须等本轮任务出现后真取消（评审 B6：上一轮终态任务不得被当成本轮任务）。"""
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())

    old_task_id = "task-old-round"
    new_task_id = "task-new-round"
    service = FakeAgentService()
    entered = asyncio.Event()
    allow_register = asyncio.Event()

    async def _script():
        entered.set()
        await allow_register.wait()
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=new_task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=current_session_id,
                    status=TaskStatus.RUNNING,
                    query="第二轮任务",
                )
            )
            await db.commit()
        yield AgentEvent(type="chunk", content="开始", task_id=new_task_id)
        while not await checkpoint.is_cancel_requested(new_task_id):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        await checkpoint.mark_terminal(
            new_task_id,
            TaskStatus.CANCELLED,
            error="用户取消（协作式取消在检查点收敛）",
            error_type="user_cancelled",
        )
        yield AgentEvent(type="run_cancelled", task_id=new_task_id)

    current_session_id = ""
    service.add_chat(_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        current_session_id = session_id
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=old_task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.COMPLETED,
                    query="上一轮",
                )
            )
            await db.commit()
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "第二轮任务"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        state = env.server.sessions.get(session_id)
        assert state is not None and state.lock.locked()

        cancel_task = asyncio.create_task(
            env.client.request("session/cancel", {"sessionId": session_id}, timeout=10)
        )
        await asyncio.sleep(0.5)
        assert not cancel_task.done(), "上一轮终态任务不得让本轮 cancel 立即放弃（评审 B6）"
        allow_register.set()
        cancel_response = await cancel_task
        assert cancel_response["result"] == {"cancelled": True}
        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}
    async with sqlite_env.factory() as db:
        old_task = await db.get(AgentTask, old_task_id)
        new_task = await db.get(AgentTask, new_task_id)
    assert old_task is not None and old_task.status == TaskStatus.COMPLETED
    assert new_task is not None and new_task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_with_unknown_baseline_still_waits_for_current_task(sqlite_env, monkeypatch):
    """评审 nit（probe12/13）：基线查询未知（None）时不得把上一轮终态任务当本轮任务。"""
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())

    async def _unknown_baseline(_self, _session_id):
        return None

    import app.acp.server as acp_server_module

    monkeypatch.setattr(acp_server_module.AcpServer, "_pre_prompt_baseline", _unknown_baseline)

    old_task_id = "task-old-round-unknown"
    new_task_id = "task-new-round-unknown"
    service = FakeAgentService()
    entered = asyncio.Event()
    allow_register = asyncio.Event()

    async def _script():
        entered.set()
        await allow_register.wait()
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=new_task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=current_session_id,
                    status=TaskStatus.RUNNING,
                    query="未知基线的第二轮任务",
                )
            )
            await db.commit()
        yield AgentEvent(type="chunk", content="开始", task_id=new_task_id)
        while not await checkpoint.is_cancel_requested(new_task_id):
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        await checkpoint.mark_terminal(
            new_task_id,
            TaskStatus.CANCELLED,
            error="用户取消（协作式取消在检查点收敛）",
            error_type="user_cancelled",
        )
        yield AgentEvent(type="run_cancelled", task_id=new_task_id)

    current_session_id = ""
    service.add_chat(_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        current_session_id = session_id
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=old_task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.COMPLETED,
                    query="上一轮",
                )
            )
            await db.commit()
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "第二轮任务"))
        await asyncio.wait_for(entered.wait(), timeout=5)
        state = env.server.sessions.get(session_id)
        assert state is not None and state.lock.locked()
        assert state.pre_prompt_task_id is None, "基线查询被替身为未知"

        cancel_task = asyncio.create_task(
            env.client.request("session/cancel", {"sessionId": session_id}, timeout=10)
        )
        await asyncio.sleep(0.5)
        assert not cancel_task.done(), (
            "基线未知时上一轮终态任务不得让本轮 cancel 提前放弃（评审 nit）"
        )
        allow_register.set()
        cancel_response = await cancel_task
        assert cancel_response["result"] == {"cancelled": True}
        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}
    async with sqlite_env.factory() as db:
        old_task = await db.get(AgentTask, old_task_id)
        new_task = await db.get(AgentTask, new_task_id)
    assert old_task is not None and old_task.status == TaskStatus.COMPLETED
    assert new_task is not None and new_task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_db_lookup_failure_resumes_updates(sqlite_env, monkeypatch):
    """评审 B7：任务查询读故障（-32603）后必须恢复 update 外发，答案完整落库。"""
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())
    import app.services.session_service as session_service_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(session_service_module, "get_latest_task_id", _boom)

    task_id = "task-lookup-fail"
    service = FakeAgentService()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _script():
        entered.set()
        await release.wait()
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id=task_id,
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=current_session_id,
                    status=TaskStatus.RUNNING,
                    query="查询故障的任务",
                )
            )
            await db.commit()
        yield AgentEvent(type="chunk", content="前半段", task_id=task_id)
        await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED, error=None, error_type=None)
        yield AgentEvent(type="chunk", content="后半段", task_id=task_id)
        yield AgentEvent(type="done", task_id=task_id)

    current_session_id = ""
    service.add_chat(_script)
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        current_session_id = session_id
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "q", timeout=30))
        await asyncio.wait_for(entered.wait(), timeout=5)

        started_at = time.perf_counter()
        cancel_response = await env.client.request(
            "session/cancel", {"sessionId": session_id}, timeout=10
        )
        elapsed = time.perf_counter() - started_at
        assert cancel_response["error"]["code"] == -32603
        assert elapsed < 3.0, "读故障应立即如实失败（不占满 5s 预算）"

        release.set()
        response = await prompt_task
        assert response["result"] == {"stopReason": "end_turn"}
        texts = [
            update.get("content", {}).get("text")
            for update in env.client.updates
            if update["sessionUpdate"] == "agent_message_chunk"
        ]
        assert texts == ["前半段", "后半段"], (
            "读故障导致取消失败后，run 的 update 必须恢复外发（评审 B7）"
        )
        async with sqlite_env.factory() as db:
            messages = (
                (
                    await db.execute(
                        select(ChatMessage)
                        .where(ChatMessage.session_id == session_id)
                        .order_by(ChatMessage.seq.asc())
                    )
                )
                .scalars()
                .all()
            )
        assistant = [m.content for m in messages if m.message_type.value == "assistant"]
        assert assistant == ["前半段后半段"]


@pytest.mark.asyncio
async def test_cancel_hanging_cancel_channel_returns_32603_within_budget(sqlite_env, monkeypatch):
    """评审 B3：Redis 失联（request_cancel 挂起）时 cancel 也必须在 5s 预算内 -32603。"""

    async def _hanging_request_cancel(_task_id: str) -> bool:
        await asyncio.sleep(30.0)
        return True

    monkeypatch.setattr(checkpoint, "request_cancel", _hanging_request_cancel)

    async with acp_env(sqlite_env) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-cancel-hang",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.RUNNING,
                    query="取消通道挂起",
                )
            )
            await db.commit()
        started_at = time.perf_counter()
        response = await env.client.request("session/cancel", {"sessionId": session_id}, timeout=10)
        elapsed = time.perf_counter() - started_at
        assert response["error"]["code"] == -32603
        assert elapsed < 6.0, f"cancel 必须在 5s 预算内返回（实测 {elapsed:.2f}s）"

    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, "task-cancel-hang")
    assert task is not None and task.status == TaskStatus.RUNNING, "不得假报取消成功"


@pytest.mark.asyncio
async def test_cancel_convergence_timeout_returns_32603(sqlite_env, monkeypatch):
    """评审建议：收敛超时（cancel_task_and_wait TimeoutError）→ 如实 -32603。"""
    import app.acp.server as acp_server_module

    async def _timeout(*_args, **_kwargs):
        raise TimeoutError("取消已受理，但任务未在 5 秒内收敛为 cancelled")

    monkeypatch.setattr(acp_server_module, "cancel_task_and_wait", _timeout)

    async with acp_env(sqlite_env) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-cancel-timeout",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.RUNNING,
                    query="收敛超时",
                )
            )
            await db.commit()
        response = await env.client.request("session/cancel", {"sessionId": session_id})
        assert response["error"]["code"] == -32603
        assert "5 秒" in response["error"]["message"]


@pytest.mark.asyncio
async def test_cancel_during_pending_approval_marks_task_cancelled(sqlite_env, monkeypatch):
    """Given 审批挂起，When 显式 session/cancel，Then 立即 CANCELLED、不写审批决定。"""
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())
    decisions = _patch_approval(monkeypatch)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="approval_required",
                content="需审批",
                tool_name="code_execution",
                tool_call_id="call-ap",
                task_id="task-ap",
            )
        )
    )
    async with acp_env(sqlite_env, service=service, permission_timeout=30.0) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-ap",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.WAITING_APPROVAL,
                    query="等待审批的任务",
                )
            )
            await db.commit()
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "运行代码", timeout=15))
        await env.client.wait_for_server_request("session/request_permission")
        cancel_response = await env.client.request(
            "session/cancel", {"sessionId": session_id}, timeout=10
        )
        assert cancel_response["result"] == {"cancelled": True}
        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}
    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, "task-ap")
    assert task is not None and task.status == TaskStatus.CANCELLED
    assert decisions == [], "取消优先：不得写 allow/deny 审批决定"


@pytest.mark.asyncio
async def test_run_cancelled_event_converges_db_without_service_finalize(sqlite_env, monkeypatch):
    """服务以 run_cancelled 收尾但没写终态（终态收尾失败）→ ACP 兜底落 CANCELLED。"""
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())
    service = FakeAgentService()
    service.add_chat(
        script(AgentEvent(type="chunk", content="半截"), AgentEvent(type="run_cancelled"))
    )
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-cancel-fallback",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.RUNNING,
                    query="外部取消的任务",
                )
            )
            await db.commit()
        response = await env.client.prompt(session_id, "外部取消的任务")
        assert response["result"] == {"stopReason": "cancelled"}
    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, "task-cancel-fallback")
    assert task is not None and task.status == TaskStatus.CANCELLED, (
        "事件流以 cancelled 结束 = run 已死；ACP 必须兜底把任务落 CANCELLED"
    )


@pytest.mark.asyncio
async def test_cancel_request_cancels_pending_permission(sqlite_env, monkeypatch):
    """$/cancel_request 取消指定服务端请求：审批请求收起、会话按取消收敛。"""
    decisions = _patch_approval(monkeypatch)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="approval_required",
                content="需审批",
                tool_name="code_execution",
                tool_call_id="call-cr",
                task_id="task-cr",
            )
        )
    )
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "运行代码"))
        request = await env.client.wait_for_server_request("session/request_permission")
        await env.client.notify("$/cancel_request", {"requestId": request["id"]})
        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}
    assert decisions == [], "取消优先：不写 allow/deny 决定"


@pytest.mark.asyncio
async def test_client_disconnect_denies_pending_approval_and_cancels_task(sqlite_env, monkeypatch):
    """Given 审批 pending，When 客户端断连，Then 审批收起、任务终态 CANCELLED。"""
    # mark_terminal 的真实路径会顺带收敛工具账目/转向缓冲；本用例只关心
    # 任务状态机与 ACP 断连收尾，跨模块的旁路以 no-op 替身隔离。
    monkeypatch.setattr(checkpoint.tool_ledger, "synthesize_terminal", AsyncMock())
    monkeypatch.setattr(checkpoint.steering, "clear_steers", AsyncMock())

    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="approval_required",
                content="需审批",
                tool_name="code_execution",
                tool_call_id="call-dc",
                task_id="task-dc",
            )
        )
    )
    async with acp_env(sqlite_env, service=service, permission_timeout=30.0) as env:
        session_id = await _new_session(env.client)
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-dc",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.WAITING_APPROVAL,
                    query="等待审批的任务",
                )
            )
            await db.commit()
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "运行代码", timeout=15))
        await env.client.wait_for_server_request("session/request_permission")
        # 断连收尾（Dispatcher.close 在 run() 的 finally 里同样会调用它）。
        await env.server.on_disconnect()
        response = await prompt_task
        assert response["result"] == {"stopReason": "cancelled"}
    async with sqlite_env.factory() as db:
        task = await db.get(AgentTask, "task-dc")
    assert task is not None and task.status == TaskStatus.CANCELLED, (
        "断连后待审批任务必须收敛为 CANCELLED（不悬空）"
    )


# ── 审批：allow / reject / 超时 ────────────────────────────────────────────


def _patch_approval(monkeypatch) -> list[dict]:
    decisions: list[dict] = []

    async def _fake_apply(
        *,
        task_id,
        user_id,
        organization_id=None,
        decision,
        modified_instruction=None,
        note=None,
    ):
        decisions.append(
            {
                "task_id": task_id,
                "decision": decision,
                "note": note,
                "organization_id": organization_id,
            }
        )
        return (None, None, {})

    monkeypatch.setattr(checkpoint, "apply_approval_decision", _fake_apply)
    return decisions


@pytest.mark.asyncio
async def test_permission_allow_once_completes(sqlite_env, schema, monkeypatch):
    decisions = _patch_approval(monkeypatch)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="approval_required",
                content="高风险工具需审批",
                tool_name="code_execution",
                tool_call_id="call-9",
                task_id="task-1",
            )
        )
    )
    service.add_resume(script(AgentEvent(type="chunk", content="已执行"), AgentEvent(type="done")))
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "运行代码"))
        request = await env.client.wait_for_server_request("session/request_permission")
        params = request["params"]
        jsonschema.validate(
            params, schema["serverRequests"]["session/request_permission"]["request"]
        )
        assert params["sessionId"] == session_id
        assert params["toolCall"]["toolCallId"] == "call-9"
        assert [option["optionId"] for option in params["options"]] == [
            "allow_once",
            "reject_once",
        ]
        await env.client.respond(
            request["id"], {"outcome": {"outcome": "selected", "optionId": "allow_once"}}
        )
        response = await prompt_task
        assert response["result"] == {"stopReason": "end_turn"}
    assert service.resume_started_with_chat_exhausted is True, (
        "resume 开跑前旧 chat 流必须已排干（防 GC 断流兜底覆盖新 run 快照）"
    )
    assert service.chat_closed_early is False
    assert decisions == [
        {
            "task_id": "task-1",
            "decision": "approve",
            "note": "acp:request_permission",
            "organization_id": ORG_ID,
        }
    ]
    assert service.resume_calls[0]["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_permission_reject_once_denies(sqlite_env, monkeypatch):
    decisions = _patch_approval(monkeypatch)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="approval_required",
                content="需审批",
                tool_name="code_execution",
                tool_call_id="call-9",
                task_id="task-1",
            )
        )
    )
    service.add_resume(script(AgentEvent(type="chunk", content="好的"), AgentEvent(type="done")))
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "运行代码"))
        request = await env.client.wait_for_server_request("session/request_permission")
        await env.client.respond(
            request["id"], {"outcome": {"outcome": "selected", "optionId": "reject_once"}}
        )
        response = await prompt_task
        assert response["result"] == {"stopReason": "end_turn"}
    assert decisions[0]["decision"] == "deny"


@pytest.mark.asyncio
async def test_permission_timeout_denies_and_task_not_dangling(sqlite_env, monkeypatch):
    decisions = _patch_approval(monkeypatch)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="approval_required",
                content="需审批",
                tool_name="code_execution",
                tool_call_id="call-9",
                task_id="task-timeout",
            )
        )
    )
    service.add_resume(
        script(AgentEvent(type="chunk", content="超时后拒绝"), AgentEvent(type="done"))
    )
    async with acp_env(sqlite_env, service=service, permission_timeout=0.15) as env:
        session_id = await _new_session(env.client)
        response = await env.client.prompt(session_id, "运行代码")  # 不响应审批
        assert response["result"] == {"stopReason": "end_turn"}
    assert decisions[0]["decision"] == "deny"
    assert decisions[0]["note"] == "acp:permission_timeout"
    assert service.resume_calls, "超时后必须 deny 并续跑，任务不得悬空"
    assert service.resume_started_with_chat_exhausted is True


@pytest.mark.asyncio
async def test_permission_client_error_denied_but_not_labelled_timeout(sqlite_env, monkeypatch):
    """客户端以 JSON-RPC error 回审批：按拒绝收尾，但审计不得误标 timeout。"""
    decisions = _patch_approval(monkeypatch)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="approval_required",
                content="需审批",
                tool_name="code_execution",
                tool_call_id="call-err",
                task_id="task-err",
            )
        )
    )
    service.add_resume(
        script(AgentEvent(type="chunk", content="拒绝后继续"), AgentEvent(type="done"))
    )
    async with acp_env(sqlite_env, service=service, permission_timeout=5.0) as env:
        session_id = await _new_session(env.client)
        prompt_task = asyncio.create_task(env.client.prompt(session_id, "运行代码"))
        request = await env.client.wait_for_server_request("session/request_permission")
        await env.client.respond_error(request["id"], -32603, "client cannot approve")
        response = await prompt_task
        assert response["result"] == {"stopReason": "end_turn"}
    assert decisions[0]["decision"] == "deny"
    assert decisions[0]["note"] == "acp:permission_cancelled", "不得误标为超时"


# ── 计划审批：reject / auto ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plan_approval_reject_returns_conflict(sqlite_env, monkeypatch):
    terminals: list[dict] = []

    async def _fake_mark_terminal(task_id, to, *, error=None, error_type=None):
        terminals.append({"task_id": task_id, "to": to, "error_type": error_type})
        return True

    monkeypatch.setattr(checkpoint, "mark_terminal", _fake_mark_terminal)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="plan_approval_required",
                content="计划待审批",
                plan_id="p-1",
                task_id="task-plan",
            )
        )
    )
    async with acp_env(sqlite_env, service=service, plan_approval_mode="reject") as env:
        session_id = await _new_session(env.client)
        response = await env.client.prompt(session_id, "复杂任务")
        assert response["error"]["code"] == -32009
        assert "计划审批" in response["error"]["message"]
    assert terminals == [
        {
            "task_id": "task-plan",
            "to": TaskStatus.REJECTED,
            "error_type": "plan_approval_unsupported",
        }
    ], "reject 模式必须把任务推进到 REJECTED 终态（不悬空）"
    assert service.chat_exhausted is True, "拒绝也要先排干旧流再回 -32009"


@pytest.mark.asyncio
async def test_plan_approval_auto_approves_and_resumes(sqlite_env, monkeypatch):
    approvals: list[dict] = []

    async def _fake_apply_plan(*, task_id, user_id, organization_id=None, decision, note=None):
        approvals.append({"task_id": task_id, "decision": decision, "note": note})
        return (None, None, {})

    monkeypatch.setattr(checkpoint, "apply_plan_approval_decision", _fake_apply_plan)
    service = FakeAgentService()
    service.add_chat(
        script(
            AgentEvent(
                type="plan_approval_required",
                content="计划待审批",
                plan_id="p-1",
                task_id="task-plan",
            )
        )
    )
    service.add_resume(
        script(AgentEvent(type="chunk", content="执行完毕"), AgentEvent(type="done"))
    )
    async with acp_env(sqlite_env, service=service, plan_approval_mode="auto") as env:
        session_id = await _new_session(env.client)
        response = await env.client.prompt(session_id, "复杂任务")
        assert response["result"] == {"stopReason": "end_turn"}
    assert approvals == [{"task_id": "task-plan", "decision": "approve", "note": "acp:auto"}]
    assert service.resume_started_with_chat_exhausted is True


# ── resume ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_resume_replays_no_history_and_streams(sqlite_env, schema):
    service = FakeAgentService()
    service.add_resume(
        script(AgentEvent(type="chunk", content="恢复输出"), AgentEvent(type="done"))
    )
    async with acp_env(sqlite_env, service=service) as env:
        session_id = await _new_session(env.client)
        # 造一个"中断任务"，让 get_latest_task_id 能找到。
        async with sqlite_env.factory() as db:
            db.add(
                AgentTask(
                    id="task-resume",
                    user_id=USER_ID,
                    organization_id=ORG_ID,
                    session_id=session_id,
                    status=TaskStatus.PENDING,
                    query="被中断的问题",
                )
            )
            await db.commit()
        response = await env.client.request("session/resume", {"sessionId": session_id})
        assert response["result"] == {"sessionId": session_id, "status": "running"}
        jsonschema.validate(response["result"], schema["methods"]["session/resume"]["response"])
        await _wait_until(
            lambda: any(u["sessionUpdate"] == "agent_message_chunk" for u in env.client.updates)
        )
        await _wait_until(lambda: len(service.resume_calls) == 1)
        assert service.prepare_calls[0]["task_id"] == "task-resume"
        assert service.resume_calls[0]["claim"] is False
        texts = [
            u["content"]["text"]
            for u in env.client.updates
            if u["sessionUpdate"] == "agent_message_chunk"
        ]
        assert texts == ["恢复输出"], "resume 不回放历史（只发本轮新事件）"


@pytest.mark.asyncio
async def test_session_resume_without_task_returns_not_found(sqlite_env):
    async with acp_env(sqlite_env) as env:
        session_id = await _new_session(env.client)
        response = await env.client.request("session/resume", {"sessionId": session_id})
        assert response["error"]["code"] == -32004


# ── 身份校验（直接调用 + 真实入口子进程）──────────────────────────────────


@pytest.mark.asyncio
async def test_validate_identity_rejects_wrong_org_and_inactive_users(sqlite_env):
    user = await validate_identity(sqlite_env.factory, organization_id=ORG_ID, user_id=USER_ID)
    assert user.id == USER_ID

    with pytest.raises(IdentityError):
        await validate_identity(sqlite_env.factory, organization_id=2, user_id=USER_ID)
    with pytest.raises(IdentityError):
        await validate_identity(sqlite_env.factory, organization_id=ORG_ID, user_id=USER_ID + 1)
    with pytest.raises(IdentityError):
        await validate_identity(sqlite_env.factory, organization_id=ORG_ID, user_id=999)


@pytest.mark.asyncio
async def test_validate_identity_api_key_must_match_user_and_org(sqlite_env):
    from app.services.api_key_service import api_key_service

    async with sqlite_env.factory() as db:
        user = await db.get(User, USER_ID)
        _, good = await api_key_service.create_key(db, user=user, name="good", org_id=ORG_ID)
        _, other_user_key = await api_key_service.create_key(
            db, user=await db.get(User, USER_ID + 2), name="other", org_id=ORG_ID
        )
        _, wrong_org_key = await api_key_service.create_key(
            db, user=user, name="wrong-org", org_id=2
        )

    matched = await validate_identity(
        sqlite_env.factory, organization_id=ORG_ID, user_id=USER_ID, api_key=good
    )
    assert matched.id == USER_ID
    with pytest.raises(IdentityError):
        await validate_identity(
            sqlite_env.factory, organization_id=ORG_ID, user_id=USER_ID, api_key=other_user_key
        )
    with pytest.raises(IdentityError):
        await validate_identity(
            sqlite_env.factory, organization_id=ORG_ID, user_id=USER_ID, api_key=wrong_org_key
        )


@pytest.mark.asyncio
async def test_entrypoint_without_identity_exits_2_with_empty_stdout():
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app",
        "--profile",
        "acp",
        cwd=str(BACKEND_DIR),
        env=dict(os.environ),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    assert process.returncode == 2
    assert stdout == b"", "缺失身份时 stdout 不得有任何协议帧/输出"
    assert "org-id" in stderr.decode("utf-8", errors="replace")


@pytest.mark.asyncio
async def test_entrypoint_wrong_org_exits_2_with_empty_stdout(sqlite_env):
    """Given 用户不属于 org，When 启动，Then exit 2、stderr 含原因、stdout 无帧。

    issue #494 复现面：SQL_ECHO=true（SQLAlchemy echo handler 默认绑 stdout）
    时错误路径也不得向 stdout 泄漏任何字节——这是修复前 12587 字节 SQL 日志
    的真实故障形态。
    """
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app",
        "--profile",
        "acp",
        "--org-id",
        "2",  # 用户属于 org 1
        "--user-id",
        str(USER_ID),
        cwd=str(BACKEND_DIR),
        env={
            **os.environ,
            "DATABASE_URL": f"sqlite+aiosqlite:///{sqlite_env.path}",
            "PYTHONUTF8": "1",
            "SQL_ECHO": "true",
            "DEBUG": "true",
        },
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    stderr_text = stderr.decode("utf-8", errors="replace")
    assert process.returncode == 2
    assert stdout == b"", "错误路径 stdout 必须 0 字节（SQL 日志只能走 stderr）"
    assert "不属于组织" in stderr_text
    assert "sqlalchemy.engine.Engine" in stderr_text, (
        "SQL_ECHO 必须真的生效（否则本用例不再覆盖 issue #494 的泄漏路径）"
    )


@pytest.mark.asyncio
async def test_entrypoint_module_import_does_not_create_db_engine():
    """issue #494：入口模块顶层只允许标准库——import 不得创建 DB engine。

    变异点：把 ``from app.rpc.server import ...``（chain-import
    ``app.core.database``，创建 SQLAlchemy engine 并按 echo 绑 stdout）挪回
    模块顶层 → 本用例红。
    """
    code = (
        "import sys\n"
        "import app.entrypoints.acp\n"
        "bad = [m for m in ('app.core.database', 'app.rpc.server') if m in sys.modules]\n"
        "assert not bad, f'entrypoint module import pulled in: {bad}'\n"
        "print('ok')\n"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        cwd=str(BACKEND_DIR),
        env=dict(os.environ),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=60)
    assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert stdout.strip() == b"ok"


@pytest.mark.asyncio
async def test_entrypoint_subprocess_handshake_with_sqlite_identity(sqlite_env, schema):
    from app.models.chat import ChatSessionStatus

    # 中文 + emoji 标题：子进程 stdout 必须是 UTF-8（Windows 中文 locale 的
    # cp936 无法编码 ✅，未重配会让 session/list 响应消失）。
    async with sqlite_env.factory() as db:
        db.add(
            ChatSession(
                id="utf8-session",
                user_id=USER_ID,
                organization_id=ORG_ID,
                status=ChatSessionStatus.ACTIVE,
                title="中文会话 ✅",
            )
        )
        await db.commit()

    client = await AcpClient.spawn(
        backend_dir=str(BACKEND_DIR),
        org_id=ORG_ID,
        user_id=USER_ID,
        # 故意不设 PYTHONUTF8：在 Windows 中文 locale 下由入口自身的
        # stdout UTF-8 重配保证中文/emoji 帧可往返（本测试因此有判别力）。
        # SQL_ECHO=true（issue #494）：SQL 日志不得混入 stdout 协议流——客户端的
        # 逐行 json.loads 会立刻炸掉/丢响应，握手因此超时。
        env={
            "DATABASE_URL": f"sqlite+aiosqlite:///{sqlite_env.path}",
            "SQL_ECHO": "true",
        },
    )
    started_at = time.perf_counter()
    try:
        handshake = await client.request(
            "initialize", {"protocolVersion": 1, "clientCapabilities": {}}, timeout=60
        )
        jsonschema.validate(handshake["result"], schema["methods"]["initialize"]["response"])
        session_id = await _new_session(client)
        listed = await client.request("session/list", {}, timeout=30)
        sessions = {item["sessionId"]: item for item in listed["result"]["sessions"]}
        assert session_id in sessions
        assert sessions["utf8-session"]["title"] == "中文会话 ✅"
        closed = await client.request("session/close", {"sessionId": session_id}, timeout=30)
        assert closed["result"] == {"ok": True}
    finally:
        exit_code, stderr = await client.terminate()
    elapsed = time.perf_counter() - started_at
    assert exit_code == 0, stderr
    assert elapsed < 30.0, f"握手全链必须在 30s 内完成（实测 {elapsed:.1f}s，issue #494）"
