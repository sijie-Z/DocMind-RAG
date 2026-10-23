"""SSE 续传的 API 级全环（issue #480 §5.2 / §8）。

覆盖：
- 首连事件带 ``id: {stream_id}:{seq}``；
- 带 ``Last-Event-ID`` 重连回放缺失事件（顺序、id 一致、不重复）；
- 缓冲区淘汰 / 流键不匹配 → 流内 ``event: error`` + ``SSE_RESUME_GONE``（HTTP 200）；
- Redis ring buffer 写入（LTRIM/EXPIRE 由单元测试覆盖）。

基础设施：真实路由 + 假鉴权 + 假 agent_service.chat（不碰 LLM/DB）+ 内存 Redis。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.core.sse as sse_module
from app.agent.events import AgentEvent
from app.agent.service import agent_service
from app.core.security import get_current_user

STREAM_ID = "sess-resume-1"


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.kv[key] = str(int(self.kv.get(key, "0")) + 1)
        return int(self.kv[key])

    async def get(self, key: str):
        return self.kv.get(key)

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


def _fake_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        organization_id=1,
        is_active=True,
        is_superuser=False,
        token_organization_id=None,
    )


def _fake_agent_chat() -> object:
    async def _chat(**_kwargs):
        for i in range(1, 6):
            yield AgentEvent(type="chunk", content=f"e{i}", task_id="task-1")
        yield AgentEvent(type="done", content="", task_id="task-1")

    return _chat


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sse_module, "redis_client", fake)
    return fake


@pytest.fixture
async def client(fake_redis, monkeypatch):
    import app.api.v1.endpoints.agent as agent_api
    import app.api.v1.endpoints.chat as chat_api
    from app.main import app

    monkeypatch.setitem(app.dependency_overrides, get_current_user, lambda: _fake_user())
    monkeypatch.setattr(agent_service, "chat", _fake_agent_chat())
    monkeypatch.setattr(chat_api, "save_session_interaction", AsyncMock(return_value=None))
    monkeypatch.setattr(agent_api, "_load_session_messages", AsyncMock(return_value=[]))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://sse") as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


_BODY = {"content": "你好", "useAgent": True, "conversationId": STREAM_ID}


async def _open(client, headers: dict | None = None):
    r = await client.post("/api/v1/chat/stream", json=_BODY, headers=headers or {})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return _parse_frames(r.text)


@pytest.mark.asyncio
async def test_events_carry_ids_and_reconnect_replays_without_duplicates(client, fake_redis):
    first = await _open(client)
    chunk_frames = [f for f in first if f["event"] == "chunk"]
    assert [f["data"]["content"] for f in chunk_frames] == ["e1", "e2", "e3", "e4", "e5"]
    assert [f["id"] for f in chunk_frames] == [f"{STREAM_ID}:{i}" for i in range(1, 6)], (
        "事件 id 必须是 {stream_id}:{seq} 且单调递增"
    )
    assert all(f["id"] for f in first), "所有事件都必须带 id"

    resume_from = chunk_frames[1]["id"]  # 第 2 个事件
    second = await _open(client, headers={"Last-Event-ID": resume_from})

    # 回放前缀 = 原流第 3..N 个事件（id 与内容一致，不重复）。
    expected_prefix = [(f["event"], f["id"]) for f in first[2:]]
    actual_prefix = [(f["event"], f["id"]) for f in second[: len(expected_prefix)]]
    assert actual_prefix == expected_prefix

    ids = [f["id"] for f in second]
    assert len(ids) == len(set(ids)), "同一响应内事件 id 不得重复"
    replayed_chunks = [f for f in second[: len(expected_prefix)] if f["event"] == "chunk"]
    assert [f["data"]["content"] for f in replayed_chunks] == ["e3", "e4", "e5"]


@pytest.mark.asyncio
async def test_resume_gone_when_buffer_evicted(client, fake_redis):
    first = await _open(client)
    assert first
    # 模拟 ring buffer 整段淘汰：清空列表但保留 seq 计数。
    fake_redis.lists[f"sse:buf:{STREAM_ID}"] = []

    frames = await _open(client, headers={"Last-Event-ID": f"{STREAM_ID}:2"})
    assert len(frames) == 1, f"GONE 后必须立即关闭流：{frames}"
    assert frames[0]["event"] == "error"
    assert frames[0]["data"] == {"code": "SSE_RESUME_GONE"}


@pytest.mark.asyncio
async def test_resume_gone_on_stream_id_mismatch(client, fake_redis):
    await _open(client)
    frames = await _open(client, headers={"Last-Event-ID": "other-stream:1"})
    assert frames[0]["event"] == "error"
    assert frames[0]["data"] == {"code": "SSE_RESUME_GONE"}
