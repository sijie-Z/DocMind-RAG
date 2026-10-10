"""运行中转向（steering）—— 队列模块级判别（无 DB；手写 _FakeRedis）。

被测契约（app/agent/steering.py 模块 docstring）：
- 有界：push 至满（MAX_STEER_QUEUE）后如实拒绝（-1），**绝不**静默丢弃已经
  收下的任何一条（变异点：把上界判断改成 `>` 或直接丢最早 → 容量用例红）；
- 保序：多条连续 push，drain 顺序 = 发送顺序；
- 通道不可用：push None / drain None——绝不与"已受理"/"无消息"同形；
- compose_guidance：保序合成、超限保最新、单条超限头截断；
- 终态清理：mark_terminal 清空缓冲（含 checkpoint 层接线，变异点：删掉
  clear 调用 → 清理用例红）。
"""

import pytest

from app.agent import steering


class _FakeRedis:
    """字符串面（checkpoint 租约）+ 列表面（steering 队列）的手写替身。"""

    def __bool__(self):
        return True

    def __init__(self):
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

    # ── string ops（租约/取消旗）──
    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        self.lists.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        return key in self.store or key in self.lists

    # ── list ops（steering）──
    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lpop(self, key, count=None):
        items = self.lists.get(key)
        if not items:
            return None
        if count is None:
            return items.pop(0)
        n = min(int(count), len(items))
        out = items[:n]
        del items[:n]
        return out or None

    async def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        if not items:
            return 0
        if count < 0:
            for i in range(len(items) - 1, -1, -1):
                if items[i] == value:
                    del items[i]
                    return 1
            return 0
        removed = 0
        i = 0
        while i < len(items) and (count == 0 or removed < count):
            if items[i] == value:
                del items[i]
                removed += 1
            else:
                i += 1
        return removed


class _RedisDown:
    def __bool__(self):
        return False


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(steering, "redis_client", r)
    return r


def _key(task_id: str) -> str:
    return f"agent:task:steer:{task_id}"


# ── 有界 / 保序 / 通道语义 ───────────────────────────────────────────────────


async def test_bounded_reject_when_full_and_no_silent_drop(fake_redis):
    """容量 5：第 6 条如实拒绝（-1），已收下的 5 条原样在队（变异点：上界判断
    改 `>` 会放第 6 条入队 → 本用例红；丢弃最早一条 → 保序断言红）。"""
    for i in range(steering.MAX_STEER_QUEUE):
        assert await steering.push_steer("t1", f"m{i}") == i + 1

    assert await steering.push_steer("t1", "overflow") == -1
    assert fake_redis.lists[_key("t1")] == [f"m{i}" for i in range(5)], (
        "拒绝不得顶掉任何已入队消息"
    )
    drained = await steering.drain_steers("t1")
    assert drained == [f"m{i}" for i in range(5)], "drain 保序"


async def test_push_rejects_blank_message(fake_redis):
    """评审 MINOR-A：空白消息在模块边界就是编程错误（端点层 422 拦下）——
    绝不静默收下会变成"内容为空幻影 steer_applied"的空串（变异点：移除
    空白守卫 → 本用例红）。"""
    for bad in ("", "   ", "\n\t "):
        with pytest.raises(ValueError):
            await steering.push_steer("t-blank", bad)
    assert fake_redis.lists.get(_key("t-blank")) in (None, []), "空白消息不得入队"


async def test_drain_skips_blank_entries(fake_redis):
    """防御（MINOR-A 同族）：旧写入者/带外写入的空白条目在 drain 时丢弃——
    该条绝不产出幻影 steer_applied（变异点：删掉过滤 → 本用例红）。"""
    fake_redis.lists[_key("t-blank2")] = ["", "  ", "正常指导", "\t"]
    drained = await steering.drain_steers("t-blank2")
    assert drained == ["正常指导"], "空白条目必须被过滤"


async def test_drain_order_and_clear(fake_redis):
    for msg in ("先", "中", "后"):
        await steering.push_steer("t2", msg)
    assert await steering.drain_steers("t2") == ["先", "中", "后"]
    # drain 后为空（原子弹走）
    assert await steering.drain_steers("t2") == []
    assert _key("t2") not in fake_redis.lists or fake_redis.lists[_key("t2")] == []


async def test_refill_after_drain(fake_redis):
    """满 → drain → 重新可入队（消费者腾出空间）。"""
    for i in range(steering.MAX_STEER_QUEUE):
        await steering.push_steer("t3", f"m{i}")
    assert await steering.push_steer("t3", "x") == -1
    await steering.drain_steers("t3")
    assert await steering.push_steer("t3", "新的一条") == 1


async def test_channel_down_never_masquerades(fake_redis, monkeypatch):
    """通道不可用：push None（绝不与"已受理"同形）；drain None（绝不与"无消息"
    同形）；clear 静默无副作用。"""
    monkeypatch.setattr(steering, "redis_client", _RedisDown())
    assert await steering.push_steer("t4", "m") is None
    assert await steering.drain_steers("t4") is None
    await steering.clear_steers("t4")  # 不抛


async def test_channel_exception_is_not_acceptance(fake_redis, monkeypatch):
    class _Boom(_FakeRedis):
        async def llen(self, key):
            raise RuntimeError("redis exploded")

    monkeypatch.setattr(steering, "redis_client", _Boom())
    assert await steering.push_steer("t5", "m") is None


async def test_overflow_rollback_never_exceeds_bound(fake_redis):
    """并发窗口的兜底：post 复查超界时撤回本次入队并如实拒绝（变异点：去掉
    LREM 回滚 → 队列长度断言红）。"""
    # 模拟"快检通过但入队后超界"：直接预置 5 条后手工绕过快检不可行——
    # 用挂钩让 rpush 后 len 比预期多 1（模拟并发插队）
    original_rpush = fake_redis.rpush

    async def _rpush_with_racer(key, value):
        r = await original_rpush(key, value)
        if len(fake_redis.lists[key]) == steering.MAX_STEER_QUEUE:  # 刚好第 5 条
            fake_redis.lists[key].append("并发插队")  # 模拟另一请求挤入
        return r

    fake_redis.rpush = _rpush_with_racer
    for i in range(steering.MAX_STEER_QUEUE - 1):
        await steering.push_steer("t6", f"m{i}")
    # 此刻 4 条；第 5 条 push 时并发插队使 post=6 > 5 → 回滚本次并拒绝
    assert await steering.push_steer("t6", "第五条") == -1
    assert "第五条" not in fake_redis.lists[_key("t6")], "回滚必须删掉自己那条"
    assert len(fake_redis.lists[_key("t6")]) == steering.MAX_STEER_QUEUE


# ── compose_guidance（注入文本合成；纯函数）─────────────────────────────────


def test_compose_keeps_chronological_order():
    assert steering.compose_guidance(["第一句", "第二句"]) == "第一句\n第二句"


def test_compose_keeps_newest_whole_messages_on_overflow():
    old = "旧" * 1200   # 2400 字符，单独就超限
    new = "新指示"
    composed = steering.compose_guidance([old, new])
    assert new in composed, "最近一句必须保住（方向盘语义）"
    assert len(composed) <= steering.GUIDANCE_CAP_CHARS


def test_compose_single_oversize_head_truncates():
    big = "A" * 3000
    composed = steering.compose_guidance([big])
    assert len(composed) == steering.GUIDANCE_CAP_CHARS
    assert composed.startswith("A" * 100)


def test_compose_empty():
    assert steering.compose_guidance([]) == ""


# ── 终态清理（checkpoint 接线）───────────────────────────────────────────────


async def test_mark_terminal_clears_buffer(fake_redis, tmp_path, monkeypatch):
    """mark_terminal（含幂等）清空转向缓冲——终态后指导不再有消费点（变异点：
    删掉 checkpoint.mark_terminal 里的 clear_steers 调用 → 本用例红）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 — 注册全部表
    from app.agent import checkpoint
    from app.core.database import Base
    from app.models.agent_task import TaskStatus

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'steer_terminal.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)

    task_id = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    assert await steering.push_steer(task_id, "别查这个") == 1

    assert await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED) is True
    assert _key(task_id) not in fake_redis.lists, "终态必须清缓冲"
    assert await steering.drain_steers(task_id) == []

    # 幂等重入同样不复活队列
    assert await steering.push_steer(task_id, "晚了") == 1  # 队列层无状态拦不住
    assert await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED) is True
    assert _key(task_id) not in fake_redis.lists

    await engine.dispose()
