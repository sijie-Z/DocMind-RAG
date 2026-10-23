"""SSE 编码器 / ring buffer / 续传判定的单元测试（issue #480 §5.2）。"""

import asyncio
import json

import pytest

import app.core.sse as sse_module
from app.core.sse import SSEStream, format_sse, keepalive_stream


class FakeRedis:
    """SSE 所需最小 Redis 面（incr/get/lpush/ltrim/expire/lrange）。"""

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


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(sse_module, "redis_client", fake)
    return fake


def test_format_sse_without_id():
    frame = format_sse("chunk", {"content": "hi"})
    assert frame == 'event: chunk\ndata: {"content": "hi"}\n\n'


def test_format_sse_with_id_and_multiline_data():
    frame = format_sse("error", {"content": "a\nb"}, event_id="s:3")
    lines = frame.split("\n")
    assert lines[0] == "event: error"
    assert lines[1] == "id: s:3"
    assert lines[2].startswith("data: ")
    assert frame.count("data: ") == 1, "json.dumps 会把换行转义成 \\n，保持单行 data"
    payload = json.loads(lines[2][len("data: ") :])
    assert payload == {"content": "a\nb"}
    assert frame.endswith("\n\n")


@pytest.mark.asyncio
async def test_emit_assigns_increasing_ids_and_trims(fake_redis, monkeypatch):
    monkeypatch.setattr(sse_module.settings, "SSE_BUFFER_MAXLEN", 3, raising=False)
    stream = SSEStream("sess-1")
    ids = []
    for i in range(5):
        await stream.emit("chunk", {"content": str(i)})
        ids.append(stream.stream_id)
    assert fake_redis.kv["sse:seq:sess-1"] == "5"
    buffered = fake_redis.lists["sse:buf:sess-1"]
    assert len(buffered) == 3, "LTRIM 必须限制缓冲区长度"
    # 最新在前：seq 5,4,3。
    for entry, seq in zip(buffered, (5, 4, 3), strict=True):
        assert f'"id": "sess-1:{seq}"' in entry
    assert fake_redis.expires["sse:seq:sess-1"] > 0
    assert fake_redis.expires["sse:buf:sess-1"] > 0


@pytest.mark.asyncio
async def test_replay_returns_only_newer_in_order(fake_redis):
    stream = SSEStream("sess-1")
    for i in range(5):
        await stream.emit("chunk", {"content": str(i)})

    frames, gone = await stream.replay("sess-1:2")
    assert gone is False
    assert len(frames) == 3
    assert [f.split("\n")[1] for f in frames] == [
        "id: sess-1:3",
        "id: sess-1:4",
        "id: sess-1:5",
    ]


@pytest.mark.asyncio
async def test_replay_gone_when_buffer_evicted(fake_redis):
    stream = SSEStream("sess-1")
    for i in range(5):
        await stream.emit("chunk", {"content": str(i)})
    # 模拟淘汰：只剩 4,5（第一条 seq > last+1）。
    fake_redis.lists["sse:buf:sess-1"] = fake_redis.lists["sse:buf:sess-1"][:2]

    frames, gone = await stream.replay("sess-1:2")
    assert frames == []
    assert gone is True


@pytest.mark.asyncio
async def test_replay_gone_on_stream_mismatch_and_empty_advance(fake_redis):
    stream = SSEStream("sess-1")
    await stream.emit("chunk", {"content": "0"})

    frames, gone = await stream.replay("other:1")
    assert (frames, gone) == ([], True)

    # 缓冲为空但 seq 已前进到重连点之后 ⇒ 已淘汰（GONE）。
    fake_redis.lists["sse:buf:sess-1"] = []
    fake_redis.kv["sse:seq:sess-1"] = "5"
    frames, gone = await stream.replay("sess-1:2")
    assert (frames, gone) == ([], True)

    # 缓冲为空且 seq 未超过重连点 ⇒ 只是没有新事件，不是 GONE。
    fake_redis.kv["sse:seq:sess-1"] = "1"
    frames, gone = await stream.replay("sess-1:1")
    assert (frames, gone) == ([], False)


@pytest.mark.asyncio
async def test_scoped_stream_writes_namespaced_keys_but_event_id_unchanged(fake_redis):
    stream = SSEStream("s", scope="u7")
    await stream.emit("chunk", {"content": "x"})
    assert "sse:seq:u7:s" in fake_redis.kv
    assert "sse:buf:u7:s" in fake_redis.lists
    assert "sse:buf:s" not in fake_redis.lists
    entry = fake_redis.lists["sse:buf:u7:s"][0]
    assert '"id": "s:1"' in entry, "事件 id 仍必须是 {stream_id}:{seq}"


@pytest.mark.asyncio
async def test_replay_rejected_for_unbound_stream_and_future_seq(fake_redis):
    # 未绑定流键：拒绝任意流回放（B2）。
    assert await SSEStream().replay("s:1") == ([], True)

    # 未来 seq（last > current）：不可续传。
    stream = SSEStream("s", scope="u7")
    await stream.emit("chunk", {"content": "0"})
    assert await stream.replay("s:5") == ([], True)


@pytest.mark.asyncio
async def test_keepalive_immediate_cancel_when_disabled():
    cancelled = asyncio.Event()

    async def source():
        try:
            while True:
                yield "x"
                await asyncio.sleep(0.01)
        finally:
            cancelled.set()

    agen = keepalive_stream(source(), ttl_seconds=None)
    assert await agen.__anext__() == "x"
    await agen.aclose()
    await asyncio.sleep(0.05)
    assert cancelled.is_set(), "开关关闭时必须立即取消 pipeline"


@pytest.mark.asyncio
async def test_keepalive_defers_cancel_until_ttl():
    cancelled = asyncio.Event()

    async def source():
        try:
            while True:
                yield "x"
                await asyncio.sleep(0.01)
        finally:
            cancelled.set()

    agen = keepalive_stream(source(), ttl_seconds=0.1)
    assert await agen.__anext__() == "x"
    await agen.aclose()
    await asyncio.sleep(0.05)
    assert not cancelled.is_set(), "TTL 未到前 pipeline 必须继续运行"
    await asyncio.sleep(0.3)
    assert cancelled.is_set(), "TTL 到期后必须取消 pipeline"
