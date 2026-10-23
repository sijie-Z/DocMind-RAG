"""SSE 续传的 API 级全环（issue #480 §5.2 / §8）。

覆盖：
- 首连事件带 ``id: {stream_id}:{seq}``；
- 带 ``Last-Event-ID`` 重连**只回放**缺失事件（顺序、id 一致、不重复、不重跑）；
- 缓冲区淘汰 / 流键不匹配 → 流内 ``event: error`` + ``SSE_RESUME_GONE``；
- 跨用户回放被归属校验拦截（B2）；
- ``payload.clientMessageId`` REST 幂等（B4/M5：重复不产生第二条用户消息）。

基础设施：真实路由 + 可切换假鉴权 + sqlite 会话行 + 假 agent_service.chat +
内存 Redis。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.core.sse as sse_module
import app.models  # noqa: F401 — create_all 需要全部表
from app.agent.events import AgentEvent
from app.agent.service import agent_service
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.chat import ChatSession, ChatSessionStatus
from app.models.user import User

STREAM_ID = "sess-resume-1"
OWNER_ID = 7
ATTACKER_ID = 8


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    def __bool__(self) -> bool:
        return True

    async def incr(self, key: str) -> int:
        self.kv[key] = str(int(self.kv.get(key, "0")) + 1)
        return int(self.kv[key])

    async def get(self, key: str):
        return self.kv.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def lpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])

    async def ltrim(self, key: str, start: int, end: int) -> None:
        values = self.lists.get(key, [])
        self.lists[key] = values[start:] if end == -1 else values[start : end + 1]

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        values = self.lists.get(key, [])
        return values[start:] if end == -1 else values[start : end + 1]

    async def expire(self, key: str, ttl: int) -> None:
        self.expires[key] = ttl


def _parse_frames(text: str) -> list[dict]:
    frames: list[dict] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = None
        event_id = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        frames.append(
            {
                "event": event,
                "id": event_id,
                "data": json.loads("\n".join(data_lines)) if data_lines else None,
            }
        )
    return frames


def _fake_user(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        organization_id=1,
        is_active=True,
        is_superuser=False,
        token_organization_id=None,
    )


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'sse_resume.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(User(id=OWNER_ID, username="owner", email="o@e.com", hashed_password="x"))
        session.add(User(id=ATTACKER_ID, username="attacker", email="a@e.com", hashed_password="x"))
        session.add(
            ChatSession(
                id=STREAM_ID,
                user_id=OWNER_ID,
                title="t",
                status=ChatSessionStatus.ACTIVE,
                root_session_id=STREAM_ID,
            )
        )
        await session.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sse_module, "redis_client", fake)
    return fake


@pytest.fixture
async def client(db_factory, fake_redis, monkeypatch):
    import app.api.v1.endpoints.agent as agent_api
    import app.api.v1.endpoints.chat as chat_api
    from app.main import app

    current = {"user": _fake_user(OWNER_ID)}
    calls = {"n": 0}

    async def fake_chat(**_kwargs):
        calls["n"] += 1
        for i in range(1, 6):
            yield AgentEvent(type="chunk", content=f"e{i}", task_id="task-1")
        yield AgentEvent(type="done", content="", task_id="task-1")

    async def _override_db():
        async with db_factory() as session:
            yield session

    app.dependency_overrides[get_current_user] = lambda: current["user"]
    app.dependency_overrides[get_db] = _override_db
    monkeypatch.setattr(agent_service, "chat", fake_chat)
    monkeypatch.setattr(chat_api, "save_session_interaction", AsyncMock(return_value=None))
    monkeypatch.setattr(chat_api, "redis_client", fake_redis)
    monkeypatch.setattr(agent_api, "_load_session_messages", AsyncMock(return_value=[]))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://sse") as c:
        c.current = current  # type: ignore[attr-defined]
        c.agent_calls = calls  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


_BODY = {"content": "你好", "useAgent": True, "conversationId": STREAM_ID}


async def _open(client, body: dict | None = None, headers: dict | None = None):
    r = await client.post("/api/v1/chat/stream", json=body or _BODY, headers=headers or {})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return _parse_frames(r.text)


@pytest.mark.asyncio
async def test_events_carry_ids_and_reconnect_replays_without_rerun(client, fake_redis):
    first = await _open(client)
    chunk_frames = [f for f in first if f["event"] == "chunk"]
    assert [f["data"]["content"] for f in chunk_frames] == ["e1", "e2", "e3", "e4", "e5"]
    assert [f["id"] for f in chunk_frames] == [f"{STREAM_ID}:{i}" for i in range(1, 6)]
    assert all(f["id"] for f in first), "所有事件都必须带 id"
    assert client.agent_calls["n"] == 1

    resume_from = chunk_frames[1]["id"]  # 第 2 个事件
    second = await _open(client, headers={"Last-Event-ID": resume_from})

    # B4：续传只回放，不再跑一轮——响应恰好等于原流第 3..N 个事件。
    assert [(f["event"], f["id"]) for f in second] == [(f["event"], f["id"]) for f in first[2:]]
    ids = [f["id"] for f in second]
    assert len(ids) == len(set(ids)), "回放不得重复"
    assert client.agent_calls["n"] == 1, "带 Last-Event-ID 的重连绝不能重跑 pipeline"


@pytest.mark.asyncio
async def test_resume_gone_when_buffer_evicted(client, fake_redis):
    first = await _open(client)
    assert first
    fake_redis.lists[f"sse:buf:{STREAM_ID}"] = []

    frames = await _open(client, headers={"Last-Event-ID": f"{STREAM_ID}:2"})
    assert len(frames) == 1, f"GONE 后必须立即关闭流：{frames}"
    assert frames[0]["event"] == "error"
    assert frames[0]["data"] == {"code": "SSE_RESUME_GONE"}


@pytest.mark.asyncio
async def test_resume_gone_on_stream_id_mismatch(client, fake_redis):
    await _open(client)
    frames = await _open(
        client, body={"content": "x", "useAgent": True}, headers={"Last-Event-ID": f"{STREAM_ID}:1"}
    )
    assert frames[0]["event"] == "error"
    assert frames[0]["data"] == {"code": "SSE_RESUME_GONE"}


@pytest.mark.asyncio
async def test_cross_user_replay_is_blocked(client, fake_redis):
    """B2：会话 UUID 不是授权凭证——他人带对话 id + Last-Event-ID 回放必须失败。"""
    await _open(client)
    calls_before = client.agent_calls["n"]

    client.current["user"] = _fake_user(ATTACKER_ID)
    frames = await _open(client, headers={"Last-Event-ID": f"{STREAM_ID}:2"})
    assert len(frames) == 1
    assert frames[0]["event"] == "error"
    assert frames[0]["data"] == {"content": "会话不存在或无权访问"}
    assert "e4" not in json.dumps(frames), "不得回放受害者缓冲内容"
    assert client.agent_calls["n"] == calls_before


@pytest.mark.asyncio
async def test_client_message_id_is_idempotent_on_rest_stream(client, fake_redis):
    """M5：同一 clientMessageId 第二次请求只回 done(duplicate)，不重跑 LLM。"""
    body = {**_BODY, "payload": {"clientMessageId": "msg-1"}}
    first = await _open(client, body=body)
    assert any(f["event"] == "chunk" for f in first)
    assert client.agent_calls["n"] == 1

    second = await _open(client, body=body)
    assert len(second) == 1
    assert second[0]["event"] == "done"
    assert second[0]["data"]["duplicate"] is True
    assert client.agent_calls["n"] == 1, "幂等命中不得产生第二次 run"


@pytest.mark.asyncio
async def test_chat_stream_enforces_chat_write_scope(client):
    """B3：dm_ key 缺 chat:write → 403 AUTHORIZATION_ERROR；有则放行。"""
    scoped = _fake_user(OWNER_ID)
    scoped.api_key_id = "k-1"
    scoped.api_key_scopes = ["rpc"]
    client.current["user"] = scoped

    r = await client.post("/api/v1/chat/stream", json=_BODY)
    assert r.status_code == 403
    assert r.json()["error_code"] == "AUTHORIZATION_ERROR"

    allowed = _fake_user(OWNER_ID)
    allowed.api_key_id = "k-2"
    allowed.api_key_scopes = ["chat:write"]
    client.current["user"] = allowed
    ok = await client.post("/api/v1/chat/stream", json=_BODY)
    assert ok.status_code == 200
