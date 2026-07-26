"""Behavior tests for the memory system.

These tests verify the memory system ACTUALLY COMPLETES tasks — not that
internal methods are called.  The doc in backend/docs/memory_system.md is the
truth reference for field names, default sizes, and scoring formulas.
"""

import math

import pytest

from app.services.memory_service import (
    AgentMemorySystem,
    LongTermMemory,
    MemoryItem,
    ReflectiveMemory,
    ShortTermMemory,
    WorkingMemory,
)


# ── ShortTermMemory behavior ────────────────────────────────────────────────


class TestShortTermMemoryDefaultCapacity:
    """Doc §3.1: max_size=20, NOT 50."""

    def test_buffer_wraps_at_20_not_50(self):
        stm = ShortTermMemory()
        assert stm.max_size == 20

        for i in range(20):
            stm.add(MemoryItem(f"item-{i}", memory_type="short_term"))

        assert len(stm.buffer) == 20
        assert stm.buffer[0].content == "item-0"

        stm.add(MemoryItem("overflow", memory_type="short_term"))
        assert len(stm.buffer) == 20, "should still be 20 (not 50)"
        assert stm.buffer[0].content == "item-1", "oldest item should have been evicted"

    def test_get_recent_returns_last_n(self):
        stm = ShortTermMemory()
        for i in range(10):
            stm.add(MemoryItem(f"item-{i}", memory_type="short_term"))
        recent = stm.get_recent(5)
        assert len(recent) == 5
        assert recent[0].content == "item-5"
        assert recent[-1].content == "item-9"

    def test_search_keyword_hit(self):
        stm = ShortTermMemory()
        stm.add(MemoryItem("我叫张三", memory_type="short_term"))
        stm.add(MemoryItem("天气不错", memory_type="short_term"))

        results = stm.search("张三", top_k=5)
        assert len(results) == 1
        assert "张三" in results[0].content

    def test_search_miss_returns_empty(self):
        stm = ShortTermMemory()
        stm.add(MemoryItem("天气不错", memory_type="short_term"))
        results = stm.search("李四", top_k=5)
        assert results == []


# ── LongTermMemory scoring formulas ─────────────────────────────────────────


class TestLongTermScoring:
    """Doc §3.2: keyword path = 0.5*relevance + 0.3*decay + 0.2*min(access/10, 1);
                     semantic path = 0.6*sim + 0.4*decay."""
    # keyword scoring is not pure — depends on time. But formula itself testable.
    pass  # TODO


# ── AgentMemorySystem.recall default layers ──────────────────────────────────


class TestRecallDefaultLayers:
    """Doc §4: default memory_types is ["short_term","long_term","reflective"],
       NOT ["long_term","reflective"]."""

    @pytest.mark.asyncio
    async def test_default_includes_short_term(self):
        ams = AgentMemorySystem(agent_id="test_recall")
        ams.short_term.add(MemoryItem("short测试", memory_type="short_term"))

        # No embedding provider -> falls back to keyword search
        results = await ams.recall("测试", top_k=5)
        contents = [r.get("content", "") for r in results]
        assert any("short测试" in c for c in contents), \
            f"short_term should be included in default recall, got: {contents}"


# ── store_interaction writes only short_term (twice) ─────────────────────────


class TestStoreInteractionBehavior:
    """Doc §5 Phase 4 fix: store_interaction writes TWO short_term entries,
       NOT one short_term + one long_term.
    """

    @pytest.mark.asyncio
    async def test_store_interaction_does_not_touch_long_term(self):
        ams = AgentMemorySystem(agent_id="test_store_interact")
        assert len(ams.long_term.memories) == 0

        await ams.store_interaction("你好", "你好！有什么可以帮你的?")
        # Both entries went to short_term, NOT long_term
        assert len(ams.short_term.buffer) >= 2, "should write two short_term entries"
        long_term_items = sum(
            len(items) for items in ams.long_term.memories.values()
        )
        assert long_term_items == 0, \
            "store_interaction should NOT write to long_term"

    @pytest.mark.asyncio
    async def test_store_experience_writes_long_term_and_failed_reflective(self):
        ams = AgentMemorySystem(agent_id="test_store_exp")
        await ams.store_experience(success=False, action="search", result="timeout")
        long_term_items = sum(
            len(items) for items in ams.long_term.memories.values()
        )
        assert long_term_items >= 1, "store_experience should write to long_term"
        # Failed experience should also hit reflective.lessons
        assert len(ams.reflective.lessons) >= 1, \
            "failed store_experience should call reflective.add_lesson"


# ── WorkingMemory / ReflectiveMemory data structures ─────────────────────────


class TestWorkingMemoryShape:
    """Doc §3.3: WorkingMemory is NOT MemoryItem; stores dicts."""

    def test_stores_and_retrieves_step_results(self):
        wm = WorkingMemory()
        wm.set_result("step-1", {"answer": "42"})
        assert wm.get_result("step-1") == {"answer": "42"}

    def test_variable_template_resolution(self):
        wm = WorkingMemory()
        wm.set_variable("name", "张三")
        resolved = wm.resolve_template("你好, {{name}}!")
        assert resolved == "你好, 张三!"


class TestReflectiveMemoryShape:
    """Doc §3.4: structure is insights + patterns + lessons (three lists)."""

    def test_has_three_separate_lists(self):
        rm = ReflectiveMemory()
        rm.add_insight("洞察1", {})
        rm.add_pattern("频繁提及: 财务", ["财务1", "财务2"])
        rm.add_lesson("教训1", trigger="search", solution="retry")

        assert len(rm.insights) == 1
        assert len(rm.patterns) == 1
        assert len(rm.lessons) == 1


# ── estimate_tokens fallback ────────────────────────────────────────────────


class TestTokenEstimation:
    """Doc §7: CHARS_PER_TOKEN=2.5, not // 3."""

    def test_fallback_uses_2_5_divisor(self):
        from app.agent.context import CHARS_PER_TOKEN

        assert CHARS_PER_TOKEN == 2.5, \
            f"CHARS_PER_TOKEN should be 2.5 (doc §7), got {CHARS_PER_TOKEN}"

    def test_estimate_tokens_returns_reasonable_count(self):
        from app.agent.context import estimate_tokens

        text = "X" * 2500
        # With tiktoken present the value will be encoder-based (e.g. ~313).
        # Without tiktoken it would be int(2500 / 2.5) = 1000.
        # Either way, it must NOT be int(2500 / 3) = 833 (the old doc bug).
        tokens = estimate_tokens(text)
        assert tokens >= 1, f"estimated tokens should be positive, got {tokens}"
        assert tokens != 833, \
            f"old doc bug: len(text)//3 would give 833; actual estimate_tokens returned {tokens}"


# ── AgentMemorySystem constructor ───────────────────────────────────────────


class TestAgentMemorySystemConstructor:
    """Doc §4: __init__ takes agent_id, stores self.agent_id (NOT namespace)."""

    def test_default_agent_id(self):
        ams = AgentMemorySystem()
        assert ams.agent_id == "default"
        assert not hasattr(ams, "namespace"), "AgentMemorySystem should NOT have .namespace"

    def test_custom_agent_id(self):
        ams = AgentMemorySystem(agent_id="finance-agent")
        assert ams.agent_id == "finance-agent"
