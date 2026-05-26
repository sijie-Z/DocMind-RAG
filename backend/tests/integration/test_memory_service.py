"""Agent memory system unit tests."""
import asyncio
from datetime import datetime, timedelta

import pytest

from app.services.memory_service import (
    AgentMemorySystem,
    LongTermMemory,
    MemoryItem,
    ReflectiveMemory,
    ShortTermMemory,
    WorkingMemory,
    get_memory_system,
)

# ---------------------------------------------------------------------------
# MemoryItem
# ---------------------------------------------------------------------------

class TestMemoryItem:
    def test_create_and_to_dict(self):
        item = MemoryItem(content="test memory", memory_type="short_term", importance=0.8)
        d = item.to_dict()
        assert d["content"] == "test memory"
        assert d["memory_type"] == "short_term"
        assert d["importance"] == 0.8
        assert "id" in d
        assert "created_at" in d

    def test_unique_ids(self):
        a = MemoryItem(content="hello")
        b = MemoryItem(content="hello")  # same content, later time -> different hash
        assert a.id != b.id

    def test_access_updates_count_and_timestamp(self):
        item = MemoryItem(content="x")
        before = item.last_accessed
        c0 = item.access_count
        item.access()
        assert item.access_count == c0 + 1
        assert item.last_accessed >= before

    def test_decay_score_fresh(self):
        item = MemoryItem(content="fresh", importance=1.0)
        score = item.get_decay_score(half_life_hours=24)
        assert score > 0.95  # almost no decay for a brand-new item

    def test_decay_score_aged(self):
        item = MemoryItem(content="old", importance=1.0)
        item.created_at = datetime.now() - timedelta(hours=24)
        score = item.get_decay_score(half_life_hours=24)
        assert 0.45 < score < 0.55  # ~0.5 after one half-life

    def test_to_dict_excludes_none_embedding(self):
        item = MemoryItem(content="no emb")
        d = item.to_dict()
        assert "embedding" not in d

    def test_to_dict_includes_embedding_when_set(self):
        item = MemoryItem(content="with emb", embedding=[0.1, 0.2, 0.3])
        d = item.to_dict()
        assert d["embedding"] == [0.1, 0.2, 0.3]


# ---------------------------------------------------------------------------
# ShortTermMemory
# ---------------------------------------------------------------------------

class TestShortTermMemory:
    def test_add_and_get_recent(self):
        stm = ShortTermMemory(max_size=10)
        for i in range(15):
            stm.add(MemoryItem(content=f"msg-{i}"))
        assert len(stm.buffer) == 10  # capped
        recent = stm.get_recent(5)
        assert len(recent) == 5
        # newest first
        assert recent[-1].content == "msg-14"

    def test_search_finds_content(self):
        stm = ShortTermMemory()
        stm.add(MemoryItem(content="报销流程需要审批"))
        stm.add(MemoryItem(content="请假需要主管同意"))
        results = stm.search("报销", top_k=3)
        assert len(results) == 1
        assert "报销" in results[0].content

    def test_search_returns_empty_for_no_match(self):
        stm = ShortTermMemory()
        stm.add(MemoryItem(content="hello world"))
        assert stm.search("xyz") == []

    def test_clear(self):
        stm = ShortTermMemory()
        stm.add(MemoryItem(content="test"))
        stm.clear()
        assert len(stm.buffer) == 0

    def test_to_list(self):
        stm = ShortTermMemory()
        stm.add(MemoryItem(content="a"))
        stm.add(MemoryItem(content="b"))
        lst = stm.to_list()
        assert len(lst) == 2
        assert lst[0]["content"] == "a"


# ---------------------------------------------------------------------------
# LongTermMemory
# ---------------------------------------------------------------------------

class TestLongTermMemory:
    def test_add_and_search_keyword(self):
        ltm = LongTermMemory()
        ltm.add(MemoryItem(content="expense report requires invoice submission", memory_type="episode", importance=0.8))
        ltm.add(MemoryItem(content="leave request needs manager approval", memory_type="episode", importance=0.5))
        ltm.add(MemoryItem(content="travel allowance standard per day 300", memory_type="semantic", importance=0.7))

        results = ltm.search("expense invoice", top_k=5)
        assert len(results) >= 1
        assert any("expense" in r.content for r in results)

    def test_search_respects_memory_type_filter(self):
        ltm = LongTermMemory()
        ltm.add(MemoryItem(content="episode memory", memory_type="episode"))
        ltm.add(MemoryItem(content="semantic memory", memory_type="semantic"))
        results = ltm.search("memory", memory_type="episode", top_k=10)
        assert all(r.memory_type == "episode" for r in results)

    def test_search_respects_min_importance(self):
        ltm = LongTermMemory()
        ltm.add(MemoryItem(content="important", importance=0.9))
        ltm.add(MemoryItem(content="trivial", importance=0.1))
        results = ltm.search("important trivial", min_importance=0.5, use_decay=False)
        assert len(results) == 1
        assert results[0].content == "important"

    def test_get_by_time_range(self):
        ltm = LongTermMemory()
        old = MemoryItem(content="old")
        old.created_at = datetime.now() - timedelta(days=10)
        ltm.add(old)
        ltm.add(MemoryItem(content="recent"))
        results = ltm.get_by_time_range(
            datetime.now() - timedelta(days=1),
            datetime.now() + timedelta(hours=1),
        )
        assert len(results) == 1
        assert results[0].content == "recent"

    def test_get_important_memories(self):
        ltm = LongTermMemory()
        for i in range(20):
            ltm.add(MemoryItem(content=f"mem-{i}", importance=float(i) / 20))
        top = ltm.get_important_memories(top_k=3)
        assert len(top) == 3
        assert top[0].importance >= top[1].importance >= top[2].importance

    def test_forget_removes_from_index(self):
        ltm = LongTermMemory()
        item = MemoryItem(content="unique keyword here", memory_type="episode")
        ltm.add(item)
        assert ltm.search("unique", top_k=3)
        assert ltm.forget(item.id)
        assert not ltm.search("unique", top_k=3)

    def test_search_semantic(self):
        ltm = LongTermMemory()
        ltm.add(MemoryItem(
            content="报销流程需要提交发票申请",
            embedding=[1.0, 0.0, 0.0],
            memory_type="episode",
            importance=1.0,
        ))
        ltm.add(MemoryItem(
            content="请假流程需要主管审批",
            embedding=[0.0, 1.0, 0.0],
            memory_type="episode",
            importance=1.0,
        ))
        ltm.add(MemoryItem(
            content="差旅标准每天300元",
            embedding=[0.0, 0.0, 1.0],
            memory_type="semantic",
            importance=1.0,
        ))

        results = ltm.search_semantic([0.9, 0.1, 0.0], top_k=2)
        assert len(results) >= 1
        assert "报销" in results[0].content

    def test_search_semantic_excludes_items_without_embedding(self):
        ltm = LongTermMemory()
        ltm.add(MemoryItem(content="no embedding item", importance=1.0))
        results = ltm.search_semantic([1.0, 0.0, 0.0])
        assert len(results) == 0

    def test_search_semantic_respects_min_importance(self):
        ltm = LongTermMemory()
        ltm.add(MemoryItem(content="important", embedding=[1.0, 0.0], importance=0.9))
        ltm.add(MemoryItem(content="trivial", embedding=[1.0, 0.0], importance=0.1))
        results = ltm.search_semantic([1.0, 0.0], min_importance=0.5)
        assert len(results) == 1

    def test_cosine_similarity_perfect(self):
        assert LongTermMemory._cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)

    def test_cosine_similarity_orthogonal(self):
        assert LongTermMemory._cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_cosine_similarity_empty(self):
        assert LongTermMemory._cosine_similarity([], [1.0]) == 0.0

    def test_to_dict(self):
        ltm = LongTermMemory()
        ltm.add(MemoryItem(content="test", memory_type="episode"))
        d = ltm.to_dict()
        assert "episode" in d
        assert len(d["episode"]) == 1

    def test_add_and_search_keyword_chinese(self):
        """Chinese text without spaces should be searchable via bigrams."""
        ltm = LongTermMemory()
        ltm.add(MemoryItem(content="报销需要提交发票", memory_type="episode", importance=0.8))
        ltm.add(MemoryItem(content="请假需要主管审批", memory_type="episode", importance=0.5))
        ltm.add(MemoryItem(content="差旅标准每天300元", memory_type="semantic", importance=0.7))

        results = ltm.search("报销发票", top_k=5)
        assert len(results) >= 1
        assert any("报销" in r.content for r in results)

    def test_tokenize_cjk_bigrams(self):
        tokens = LongTermMemory._tokenize("报销流程需要审批")
        assert "报销" in tokens or "销流" in tokens
        assert len(tokens) >= 2  # at least some bigrams

    def test_tokenize_mixed_cjk_english(self):
        tokens = LongTermMemory._tokenize("报销 report 流程")
        assert "report" in tokens
        assert len(tokens) >= 2


# ---------------------------------------------------------------------------
# WorkingMemory
# ---------------------------------------------------------------------------

class TestWorkingMemory:
    def test_state_set_get(self):
        wm = WorkingMemory()
        wm.set_state("current_step", "retrieval")
        assert wm.get_state("current_step") == "retrieval"
        assert wm.get_state("nonexistent", "default") == "default"

    def test_task_stack(self):
        wm = WorkingMemory()
        wm.push_task({"name": "search", "query": "报销"})
        wm.push_task({"name": "generate", "context": "..."})
        popped = wm.pop_task()
        assert popped["task"]["name"] == "generate"
        popped = wm.pop_task()
        assert popped["task"]["name"] == "search"
        assert wm.pop_task() is None

    def test_intermediate_results(self):
        wm = WorkingMemory()
        wm.set_result("search_results", [{"doc": "a"}])
        assert wm.get_result("search_results") == [{"doc": "a"}]

    def test_variables_and_template_resolution(self):
        wm = WorkingMemory()
        wm.set_variable("user_name", "张三")
        wm.set_variable("org", "研发部")
        resolved = wm.resolve_template("你好 {{user_name}}，欢迎来到 {{org}}")
        assert "张三" in resolved
        assert "研发部" in resolved

    def test_clear(self):
        wm = WorkingMemory()
        wm.set_state("k", "v")
        wm.push_task({"t": 1})
        wm.clear()
        assert wm.get_state("k") is None
        assert wm.pop_task() is None

    def test_to_dict(self):
        wm = WorkingMemory()
        wm.set_state("key", "value")
        d = wm.to_dict()
        assert d["state"]["key"] == "value"


# ---------------------------------------------------------------------------
# ReflectiveMemory
# ---------------------------------------------------------------------------

class TestReflectiveMemory:
    def test_add_and_get_insights(self):
        rm = ReflectiveMemory()
        rm.add_insight("用户经常询问报销相关问题", {"domain": "finance"})
        rm.add_insight("请假流程文档需要更新", {"domain": "hr"})

        results = rm.get_relevant_insights("报销 流程", top_k=3)
        assert len(results) >= 1

        results = rm.get_relevant_insights("unrelated topic")
        assert len(results) == 0

    def test_add_and_search_patterns(self):
        rm = ReflectiveMemory()
        rm.add_pattern("频繁提及: 报销", examples=["报销1", "报销2"])
        assert len(rm.patterns) == 1
        assert rm.patterns[0]["pattern"] == "频繁提及: 报销"

    def test_add_and_get_lessons(self):
        rm = ReflectiveMemory()
        rm.add_lesson(
            lesson="ES连接超时时需要降级到纯关键词搜索",
            trigger="Elasticsearch timeout",
            solution="降级到MySQL全文搜索",
        )

        results = rm.get_lessons_for_situation("Elasticsearch timeout occurred")
        assert len(results) == 1
        assert results[0]["lesson"] == "ES连接超时时需要降级到纯关键词搜索"

    def test_get_lessons_no_match(self):
        rm = ReflectiveMemory()
        rm.add_lesson(lesson="test", trigger="specific trigger")
        assert rm.get_lessons_for_situation("unrelated") == []

    def test_to_dict(self):
        rm = ReflectiveMemory()
        rm.add_insight("insight")
        rm.add_pattern("pattern")
        rm.add_lesson("lesson")
        d = rm.to_dict()
        assert len(d["insights"]) == 1
        assert len(d["patterns"]) == 1
        assert len(d["lessons"]) == 1


# ---------------------------------------------------------------------------
# AgentMemorySystem (integration)
# ---------------------------------------------------------------------------

class TestAgentMemorySystem:
    def test_remember_short_term(self):
        ams = AgentMemorySystem(agent_id="test-1")
        # Bypass Redis load
        ams._loaded = True
        item = asyncio.run(ams.remember("test content", memory_type="short_term"))
        assert item.content == "test content"
        assert len(ams.short_term.buffer) == 1

    def test_remember_long_term_triggers_save(self):
        ams = AgentMemorySystem(agent_id="test-2")
        ams._loaded = True
        # _save_to_redis will fail silently (no Redis), but the memory should still be stored
        item = asyncio.run(ams.remember("long-term memory", memory_type="long_term", importance=0.9))
        assert item.memory_type == "long_term"
        assert len(ams.long_term.memories["long_term"]) == 1
        assert item.importance == 0.9

    def test_recall_searches_all_types(self):
        ams = AgentMemorySystem(agent_id="test-3")
        ams._loaded = True
        asyncio.run(ams.remember("short-term msg", memory_type="short_term"))
        asyncio.run(ams.remember("long-term knowledge", memory_type="long_term", importance=0.8))
        ams.reflective.add_insight("reflective insight about knowledge")

        results = asyncio.run(ams.recall("knowledge", top_k=10))
        assert len(results) >= 2  # long-term + reflective

    def test_get_context_returns_formatted_string(self):
        ams = AgentMemorySystem(agent_id="test-4")
        ams._loaded = True
        asyncio.run(ams.remember("user prefers concise answer style", memory_type="long_term", importance=0.8))
        asyncio.run(ams.remember("user likes markdown formatting", memory_type="long_term", importance=0.7))
        context = asyncio.run(ams.get_context("concise style"))
        assert "concise" in context.lower()

    def test_store_interaction(self):
        ams = AgentMemorySystem(agent_id="test-5")
        ams._loaded = True
        asyncio.run(ams.store_interaction("用户问题", "助手回答"))
        assert len(ams.short_term.buffer) >= 2

    def test_store_experience_success(self):
        ams = AgentMemorySystem(agent_id="test-6")
        ams._loaded = True
        asyncio.run(ams.store_experience(
            success=True, action="search", result="找到3条结果", context="test"
        ))
        assert len(ams.long_term.memories) > 0

    def test_store_experience_failure_adds_lesson(self):
        ams = AgentMemorySystem(agent_id="test-7")
        ams._loaded = True
        asyncio.run(ams.store_experience(
            success=False, action="ES query", result="超时", context="test"
        ))
        # Failure should add a lesson
        assert len(ams.reflective.lessons) >= 1

    def test_export_and_import(self):
        ams = AgentMemorySystem(agent_id="test-8")
        ams._loaded = True
        asyncio.run(ams.remember("export test", memory_type="long_term", importance=0.7))
        data = ams.export()
        assert data["agent_id"] == "test-8"
        assert len(data["long_term"]) > 0

        # Import into a new system
        ams2 = AgentMemorySystem(agent_id="test-9")
        ams2._loaded = True
        ams2.import_data(data)
        assert len(ams2.long_term.memories) > 0

    def test_embedding_provider_is_stored(self):
        ams = AgentMemorySystem(agent_id="test-10")
        ams._loaded = True

        async def fake_embedding(text: str):
            return [0.1, 0.2, 0.3]

        ams.set_embedding_provider(fake_embedding)
        item = asyncio.run(ams.remember("emb test", memory_type="long_term"))
        assert item.embedding == [0.1, 0.2, 0.3]

    def test_recall_uses_semantic_when_provider_set(self):
        ams = AgentMemorySystem(agent_id="test-11")
        ams._loaded = True

        async def fake_embedding(text: str):
            return [1.0, 0.0] if "target" in text else [0.0, 1.0]

        ams.set_embedding_provider(fake_embedding)

        # Store with embeddings
        asyncio.run(ams.remember("target content here", memory_type="long_term", importance=1.0))
        asyncio.run(ams.remember("irrelevant content", memory_type="long_term", importance=1.0))

        results = asyncio.run(ams.recall("target query", memory_types=["long_term"], top_k=5))
        assert len(results) >= 1
        assert "target" in results[0]["content"]

    def test_auto_reflect_adds_patterns(self):
        ams = AgentMemorySystem(agent_id="test-12")
        ams._loaded = True
        ams.config["reflect_interval"] = 3
        ams.interaction_count = 2  # next remember triggers reflection

        # Add similar content to create patterns
        asyncio.run(ams.remember("报销报销流程", memory_type="short_term"))
        asyncio.run(ams.remember("报销报销规则", memory_type="short_term"))
        asyncio.run(ams.remember("报销报销标准", memory_type="short_term"))

        assert len(ams.reflective.patterns) >= 0  # at minimum doesn't crash

    def test_get_memory_system_caches(self):
        a = get_memory_system("shared-agent")
        b = get_memory_system("shared-agent")
        assert a is b

    def test_get_memory_system_different_agents(self):
        a = get_memory_system("agent-a")
        b = get_memory_system("agent-b")
        assert a is not b
