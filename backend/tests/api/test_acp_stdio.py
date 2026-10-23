"""ACP v1 stdio 端到端测试（issue #481 §8）。

架构：真实 ``AcpServer.run()`` 连接循环 + 内存管道（:class:`InMemoryAcpTransport`）
+ sqlite（``Base.metadata.create_all``）+ :class:`FakeAgentService`。fake service
只替代 LLM/PER 循环，ACP 侧的队列、串行 sender、审批双向请求、取消、DB 落库
全部走真实代码；真实 LLM 的 e2e 由监督方在有 key 的环境跑（本文件不依赖网络）。

另含真实入口子进程用例：``python -m app --profile acp`` 无身份 → exit 2、
stdout 0 字节；有效身份（sqlite）→ 握手/new/close 全链、stdout 只有协议帧。
"""

import asyncio
import json
import os
import sys
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
    """Given 用户不属于 org，When 启动，Then exit 2、stderr 含原因、stdout 无帧。"""
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
        },
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=120)
    assert process.returncode == 2
    assert stdout == b""
    assert "不属于组织" in stderr.decode("utf-8", errors="replace")


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
        env={"DATABASE_URL": f"sqlite+aiosqlite:///{sqlite_env.path}"},
    )
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
    assert exit_code == 0, stderr
