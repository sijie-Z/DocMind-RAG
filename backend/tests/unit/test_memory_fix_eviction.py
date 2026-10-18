"""M2 修复验证：memory_systems 缓存必须有界（LRU），驱逐前尽力持久化。

修复前：缓存无上限、无驱逐，每个命名空间都常驻内存（长期记忆 + embedding），
且 remember() 每次都会把整份长期记忆写一遍 Redis。
"""
import asyncio

import pytest

from app.services import memory_service as ms


@pytest.fixture()
def isolated_cache():
    """隔离全局记忆系统缓存，避免本文件的压力测试污染其他测试。"""
    original = dict(ms.memory_systems)
    ms.memory_systems.clear()
    try:
        yield
    finally:
        ms.memory_systems.clear()
        ms.memory_systems.update(original)


class TestBoundedCache:
    def test_cache_never_grows_beyond_cap(self, isolated_cache):
        for i in range(ms._MAX_MEMORY_SYSTEMS + 50):
            ms.get_memory_system(0, 0, f"fix-m2-agent-{i}")
        assert len(ms.memory_systems) == ms._MAX_MEMORY_SYSTEMS

    def test_lru_hit_does_not_grow_cache(self, isolated_cache):
        for _ in range(100):
            ms.get_memory_system(0, 0, "fix-m2-same-key")
        assert len(ms.memory_systems) == 1

    def test_least_recently_used_evicted_first(self, isolated_cache):
        cap = ms._MAX_MEMORY_SYSTEMS
        oldest = ms.get_memory_system(0, 0, "fix-m2-oldest")
        for i in range(cap):
            ms.get_memory_system(0, 0, f"fix-m2-fill-{i}")

        # 最老的命名空间已被挤出，重新获取应得到全新实例
        assert ms.get_memory_system(0, 0, "fix-m2-oldest") is not oldest

    def test_recently_used_namespace_survives_eviction(self, isolated_cache):
        cap = ms._MAX_MEMORY_SYSTEMS
        for i in range(cap):
            ms.get_memory_system(0, 0, f"fix-m2-slot-{i}")

        kept = ms.get_memory_system(0, 0, "fix-m2-slot-0")  # 触碰 → 变为最近使用
        ms.get_memory_system(0, 0, "fix-m2-newcomer")       # 触发一次驱逐（应驱逐 slot-1）

        assert ms.get_memory_system(0, 0, "fix-m2-slot-0") is kept
        assert len(ms.memory_systems) == cap

    def test_evict_helper_pops_entry(self, isolated_cache):
        ms.get_memory_system(0, 0, "fix-m2-direct")
        ms._evict_memory_system("0:0:fix-m2-direct")
        assert "0:0:fix-m2-direct" not in ms.memory_systems


class TestEvictionPersistence:
    @pytest.mark.asyncio
    async def test_loaded_system_flushed_before_eviction(self, isolated_cache, monkeypatch):
        saved: list[str] = []

        async def fake_save(self):
            saved.append(self.agent_id)

        monkeypatch.setattr(ms.AgentMemorySystem, "_save_to_redis", fake_save)

        oldest = ms.get_memory_system(0, 0, "fix-m2-flush-oldest")
        oldest._loaded = True  # 模拟已从 Redis 加载过的实例

        for i in range(ms._MAX_MEMORY_SYSTEMS):
            ms.get_memory_system(0, 0, f"fix-m2-flush-{i}")

        await asyncio.sleep(0.05)  # 等待驱逐时调度的持久化任务执行
        assert "0:0:fix-m2-flush-oldest" in saved, \
            "驱逐前必须把已加载实例的长期/反思记忆写回 Redis"

    @pytest.mark.asyncio
    async def test_unloaded_system_not_written_on_eviction(self, isolated_cache, monkeypatch):
        """未加载过的实例内存为空，写回会覆盖 Redis 中的已有数据 → 必须跳过。"""
        saved: list[str] = []

        async def fake_save(self):
            saved.append(self.agent_id)

        monkeypatch.setattr(ms.AgentMemorySystem, "_save_to_redis", fake_save)

        ms.get_memory_system(0, 0, "fix-m2-unloaded-oldest")
        for i in range(ms._MAX_MEMORY_SYSTEMS):
            ms.get_memory_system(0, 0, f"fix-m2-unloaded-{i}")

        await asyncio.sleep(0.05)
        assert "0:0:fix-m2-unloaded-oldest" not in saved
