"""issue #292 回归：检索故障不得伪装「知识库无相关内容」+ 故障/空结果不得毒化缓存。

修前实测（见 `_orchestrate/round2/_tmp_rag291/repro_292.py` 的输出原文）：
① ES 不可达 → `search_knowledge_base` 返回 `[]`（与「确实没有内容」完全同形）；
② 该 `[]` 被写进 `RetrievalCache`（`RAG_CACHE_TTL_SECONDS=600`）；
③ ES 恢复后同一措辞 600s 内直接命中空缓存、`retriever` 零调用（一次抖动被
   放大成 10 分钟的持续假无结果）；
④ embedding 挂时向量臂被静默关闭（返回 `[]` 无信号），keyword 臂空即整体呈「无结果」。

本文件钉住修复后的语义：
- 三态：正常空结果（不抛）/ 服务故障（`kind="failed"`）/ 超时（`kind="timeout"`）；
- 缓存写入门控：只有「全臂正常 + 有结果」才写；故障/降级一律不写；
  正常空结果按决策也不写负缓存（重算成本 << 毒化成本）；
- 上层诚实降级：chat 链路故障提示词与「暂无相关文档」严格区分、严格模式显式拒答；
  agent 工具返回结构化 ToolResult.fail；knowledge_service 不再吞成 []；
- 回归对照：正常空结果与正常命中的既有行为不变。

变异验证（tools/mutate.py，每个用例类内注明对应点）：
- 还原「异常回落成空结果」→ TestFailureDoesNotFakeEmpty 红；
- 还原「无条件写缓存」→ TestFailureDoesNotFakeEmpty / TestNormalEmptyNoNegativeCache 红；
- 去掉降级臂对缓存门控的参与 → TestDegradedArmSemantics 红；
- kind 恒为 "failed" → 超时用例红；
- chat 链路恢复「暂无相关文档」话术/恢复写缓存 → TestChatHonestDegradation 红。
"""
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.rag.errors import RetrievalUnavailableError
from app.rag.pipeline import RAGPipeline
from app.services.knowledge_service import KnowledgeService


def _healthy_doc(chunk_id: str = "c1") -> dict:
    return {
        "chunk_id": chunk_id, "content": "真实文档内容", "filename": "真实.pdf",
        "score": 0.9, "document_id": "d1",
    }


def _patch_rerank():
    """rerank 替身：保持输入顺序（避免真模型加载/网络）。"""
    return patch(
        "app.rag.pipeline.rerank",
        new=AsyncMock(side_effect=lambda query, docs, client=None: docs),
    )


class _RecordingCache:
    """记录写入的缓存替身（get 恒未命中）。"""

    def __init__(self):
        self.set_calls: list = []

    async def get(self, *args, **kwargs):
        return None

    async def set(self, *args, **kwargs):
        self.set_calls.append(args)


def _make_pipeline(retrieve: AsyncMock, embedding: list | None = None) -> RAGPipeline:
    pipeline = RAGPipeline()
    pipeline.retriever = MagicMock()
    pipeline.retriever.get_embedding = AsyncMock(return_value=embedding if embedding is not None else [])
    pipeline.retriever.retrieve = retrieve
    pipeline.cache = _RecordingCache()
    pipeline.semantic_cache = _RecordingCache()
    return pipeline


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    """重试退避 sleep 置为 no-op：重试逻辑（真实路径）保留，单测不为等待变慢。

    注意不能把 RAG_RETRIEVAL_MAX_RETRIES 置 0——读取处是 `… or 2`，0 是 falsy
    会回落成默认 2（配置语义如此，测试尊重之）。
    """
    monkeypatch.setattr("app.rag.pipeline.asyncio.sleep", AsyncMock())


# ══════════════════════════════════════════════════════════════════════════
# 1 · 服务故障：不再伪装空结果，且缓存零写入
# ══════════════════════════════════════════════════════════════════════════
class TestFailureDoesNotFakeEmpty:
    @pytest.mark.asyncio
    async def test_retrieval_exception_raises_instead_of_returning_fake_empty(self):
        """ES/向量层异常 → RetrievalUnavailableError(kind="failed")，而不是 `[]`。

        变异验证：把 pipeline 的故障判定块改回「回落成空结果」（跳过 raise）→ 红。
        """
        retrieve = AsyncMock(side_effect=RuntimeError("ES connection refused"))
        pipeline = _make_pipeline(retrieve)

        with pytest.raises(RetrievalUnavailableError) as ei:
            await pipeline.search_knowledge_base("故障查询", 1, top_k=5)

        assert ei.value.kind == "failed", "异常故障必须是 failed 态"
        assert ei.value.sub_queries == ["故障查询"]
        assert retrieve.await_count >= 1

    @pytest.mark.asyncio
    async def test_failure_writes_no_cache(self):
        """故障不得写任何缓存（毒化路径必须断）——精确/语义缓存零 set。

        变异验证：把 `cache_safe` 门控去掉（恢复无条件写）→ 红。
        """
        retrieve = AsyncMock(side_effect=RuntimeError("ES connection refused"))
        pipeline = _make_pipeline(retrieve)

        with pytest.raises(RetrievalUnavailableError):
            await pipeline.search_knowledge_base("故障查询2", 1, top_k=5)

        assert pipeline.cache.set_calls == [], "故障不得写精确缓存"
        assert pipeline.semantic_cache.set_calls == [], "故障不得写语义缓存"

    @pytest.mark.asyncio
    async def test_timeout_is_classified_as_timeout_state(self):
        """超时与其它故障三态可分（kind="timeout"），并同样不写缓存。

        变异验证：kind 恒写 "failed" → 红。
        """
        retrieve = AsyncMock(side_effect=TimeoutError("read timeout"))
        pipeline = _make_pipeline(retrieve)

        with pytest.raises(RetrievalUnavailableError) as ei:
            await pipeline.search_knowledge_base("超时查询", 1, top_k=5)

        assert ei.value.kind == "timeout"
        assert ei.value.is_timeout is True
        assert pipeline.cache.set_calls == []

    @pytest.mark.asyncio
    async def test_client_timeout_class_name_is_recognized_as_timeout(self):
        """httpx/openai 风格超时类（类名含 Timeout）同样归为超时态。"""

        class ReadTimeout(Exception):  # 模拟 httpx.ReadTimeout 命名
            pass

        retrieve = AsyncMock(side_effect=ReadTimeout("read timed out"))
        pipeline = _make_pipeline(retrieve)

        with pytest.raises(RetrievalUnavailableError) as ei:
            await pipeline.search_knowledge_base("客户端超时查询", 1, top_k=5)

        assert ei.value.kind == "timeout"


# ══════════════════════════════════════════════════════════════════════════
# 2 · 毒化场景端到端：故障 → 恢复后必须真正重试检索（走真实缓存写路径）
# ══════════════════════════════════════════════════════════════════════════
class TestRecoveryNotPoisoned:
    @pytest.mark.asyncio
    async def test_recovery_after_failure_is_not_poisoned_by_cached_empty(self, monkeypatch):
        """修前形态：故障写空缓存 → 恢复后同一措辞命中空缓存、retriever 零调用。

        修后：故障不写缓存；恢复后同一措辞立即拿到真实结果。

        变异验证：恢复无条件写缓存（含故障时的空结果）→ 第二个断言红
        （恢复后照样命中空缓存 / 或缓存里存在空条目）。
        """
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: None)  # 走内存回退（真实写路径）
        q = f"issue292-恢复-{uuid.uuid4().hex}"

        pipeline = RAGPipeline()
        pipeline.retriever = MagicMock()
        pipeline.retriever.get_embedding = AsyncMock(return_value=[])
        pipeline.retriever.retrieve = AsyncMock(side_effect=RuntimeError("ES down"))

        with pytest.raises(RetrievalUnavailableError):
            await pipeline.search_knowledge_base(q, 1, top_k=5)

        assert await pipeline.cache.get(q, 1, 5) is None, "故障后缓存必须为空（不得有 [] 毒条目）"

        # 模拟 ES 恢复
        pipeline.retriever.retrieve = AsyncMock(return_value=([_healthy_doc()], [], None, []))
        with _patch_rerank():
            results, _ = await pipeline.search_knowledge_base(q, 1, top_k=5)

        assert results and results[0]["chunk_id"] == "c1", "恢复后同一措辞必须拿到真实结果"
        assert pipeline.retriever.retrieve.await_count == 1, "恢复后必须真正重试检索（修前这里不会发生）"


# ══════════════════════════════════════════════════════════════════════════
# 3 · 臂级降级：向量臂故障有信号；「降级+空」按故障态处理；「降级+有结果」不写缓存
# ══════════════════════════════════════════════════════════════════════════
class TestDegradedArmSemantics:
    @pytest.mark.asyncio
    async def test_embedding_failure_surfaces_degraded_signal_not_silent(self):
        """embedding 挂 → retriever 返回结构化降级原因（不再静默关臂）。

        变异验证：把 `_vector_hits` 的 except 改回 `return [], []`（无信号）→ 红。
        """
        from app.rag.query_processor import QueryComplexityClassifier
        from app.rag.retriever import HybridRetriever

        failing = MagicMock()
        failing.embeddings.create = AsyncMock(side_effect=ConnectionError("embedding down"))

        with (
            patch(
                "app.rag.retriever.ElasticsearchTools.search_documents",
                new=AsyncMock(return_value={"hits": {"hits": []}}),
            ),
            patch.object(QueryComplexityClassifier, "get_strategy", staticmethod(lambda q: "hybrid")),
        ):
            retriever = HybridRetriever(openai_client=None, embedding_client=failing)
            results, _qv, _dbg, degraded = await retriever.retrieve("向量层故障查询", 1, top_k=5)

        assert results == []
        assert degraded and any("vector_arm_unavailable" in d for d in degraded), (
            "embedding 故障必须以结构化降级信号暴露，而不是静默关掉向量臂"
        )

    @pytest.mark.asyncio
    async def test_keyword_only_strategy_by_design_is_not_degraded(self):
        """keyword_only 策略没有向量臂是**按设计**行为，不产生降级信号。"""
        from app.rag.query_processor import QueryComplexityClassifier
        from app.rag.retriever import HybridRetriever

        failing = MagicMock()
        failing.embeddings.create = AsyncMock(side_effect=ConnectionError("embedding down"))

        with (
            patch(
                "app.rag.retriever.ElasticsearchTools.search_documents",
                new=AsyncMock(return_value={"hits": {"hits": []}}),
            ),
            patch.object(QueryComplexityClassifier, "get_strategy", staticmethod(lambda q: "keyword_only")),
        ):
            retriever = HybridRetriever(openai_client=None, embedding_client=failing)
            results, _qv, _dbg, degraded = await retriever.retrieve("短查询", 1, top_k=5)

        assert results == [] and degraded == [], "按设计缺省不等于故障"

    @pytest.mark.asyncio
    async def test_degraded_arm_with_no_results_raises_not_fake_empty(self):
        """降级臂 + 最终为空：无法断言「知识库没有相关内容」→ 故障态。

        变异验证：故障判定条件去掉 `degraded_reasons`（只看子查询异常）→ 红。
        """
        retrieve = AsyncMock(return_value=([], [], None, ["vector_arm_unavailable: embedding down"]))
        pipeline = _make_pipeline(retrieve)

        with pytest.raises(RetrievalUnavailableError) as ei:
            await pipeline.search_knowledge_base("降级空查询", 1, top_k=5)

        assert ei.value.kind == "failed"
        assert pipeline.cache.set_calls == []

    @pytest.mark.asyncio
    async def test_degraded_arm_with_results_returns_them_but_does_not_cache(self):
        """降级臂 + keyword 有真实命中：返回结果（可用性保持），不写缓存。

        变异验证：cache_safe 只看 `failed_sub_queries`（漏掉 degraded_reasons）→ 红。
        """
        retrieve = AsyncMock(
            return_value=([_healthy_doc()], [], None, ["vector_arm_unavailable: embedding down"])
        )
        pipeline = _make_pipeline(retrieve)

        with _patch_rerank():
            results, _ = await pipeline.search_knowledge_base("降级有结果查询", 1, top_k=5)

        assert results and results[0]["chunk_id"] == "c1"
        assert pipeline.cache.set_calls == [], "降级结果不得冻结进缓存"
        assert pipeline.semantic_cache.set_calls == []

    @pytest.mark.asyncio
    async def test_partial_sub_query_failure_with_results_is_not_cached(self):
        """查询分解后：子查询甲重试耗尽仍失败、乙有结果 → 返回乙的结果但整体不写缓存。"""
        from app.rag.query_processor import QueryComplexityClassifier

        def _retrieve_side_effect(sq, *args, **kwargs):
            if sq == "子查询甲":
                raise RuntimeError("ES down")
            return ([_healthy_doc()], [], None, [])

        retrieve = AsyncMock(side_effect=_retrieve_side_effect)
        pipeline = _make_pipeline(retrieve)
        pipeline.openai_client = object()  # 让分解路径启用

        with (
            patch(
                "app.rag.pipeline.decompose_query",
                new=AsyncMock(return_value=["子查询甲", "子查询乙"]),
            ),
            patch.object(QueryComplexityClassifier, "classify", staticmethod(lambda q: "complex")),
            _patch_rerank(),
        ):
            results, _ = await pipeline.search_knowledge_base("复杂问题", 1, top_k=5)

        assert results and results[0]["chunk_id"] == "c1"
        # 甲：默认重试 2 次（共 3 次尝试）全部失败；乙：1 次调用成功
        assert retrieve.await_count == 4
        assert pipeline.cache.set_calls == [], "部分子查询失败的结果是残缺的，不得写缓存"


# ══════════════════════════════════════════════════════════════════════════
# 4 · 回归对照：正常空结果 / 正常命中行为不变（含负缓存决策）
# ══════════════════════════════════════════════════════════════════════════
class TestNormalEmptyAndHitRegression:
    @pytest.mark.asyncio
    async def test_normal_empty_returns_empty_without_exception_and_no_negative_cache(self, monkeypatch):
        """全臂正常、确实无命中：仍是正常空结果（不抛异常），但不写负缓存。

        决策（issue #292）：空结果不写缓存。负缓存把「一次未命中」放大成
        TTL 窗口（600s）内的持续假无结果，而重算成本远小于毒化成本。

        变异验证：恢复 `cache.set(...)` 无条件调用 → 第三个断言红。
        """
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: None)
        q = f"issue292-正常空-{uuid.uuid4().hex}"

        pipeline = RAGPipeline()
        pipeline.retriever = MagicMock()
        pipeline.retriever.get_embedding = AsyncMock(return_value=[])
        pipeline.retriever.retrieve = AsyncMock(return_value=([], [], None, []))

        results, _ = await pipeline.search_knowledge_base(q, 1, top_k=5)

        assert results == [], "正常空结果语义不变（不抛异常）"
        assert await pipeline.cache.get(q, 1, 5) is None, "空结果不得写负缓存"

    @pytest.mark.asyncio
    async def test_normal_hit_is_cached_and_served_from_cache_next_time(self, monkeypatch):
        """回归对照：正常命中行为不变——写缓存，二次同查询命中缓存（retriever 只调 1 次）。"""
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: None)
        q = f"issue292-正常命中-{uuid.uuid4().hex}"

        pipeline = RAGPipeline()
        pipeline.retriever = MagicMock()
        pipeline.retriever.get_embedding = AsyncMock(return_value=[0.1] * 4)
        pipeline.retriever.retrieve = AsyncMock(return_value=([_healthy_doc()], [0.1] * 4, None, []))

        with _patch_rerank():
            first, _ = await pipeline.search_knowledge_base(q, 1, top_k=5)
            second, _ = await pipeline.search_knowledge_base(q, 1, top_k=5)

        assert first and first[0]["chunk_id"] == "c1"
        assert second and second[0]["chunk_id"] == "c1"
        assert pipeline.retriever.retrieve.await_count == 1, "第二次必须命中精确缓存（正常行为不变）"


# ══════════════════════════════════════════════════════════════════════════
# 5 · 上层诚实降级：chat 链路
# ══════════════════════════════════════════════════════════════════════════
class _RecordingSemanticCache:
    def __init__(self):
        self.set_calls: list = []

    async def get(self, *args, **kwargs):
        return None

    async def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))


def _mock_chat_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    hist = MagicMock()
    hist.scalars.return_value.all.return_value = []
    hist.scalar_one_or_none = MagicMock(return_value=None)  # get_system_prompt → 无模板
    session.execute = AsyncMock(return_value=hist)
    return session


def _fake_rag_stack(search_side_effect, captured: dict) -> MagicMock:
    fake_rag = MagicMock()
    fake_rag.get_embedding = AsyncMock(return_value=[])
    fake_rag.search_knowledge_base = AsyncMock(side_effect=search_side_effect)
    fake_rag.report_grounded = MagicMock()
    fake_rag.get_last_token_usage = MagicMock(return_value=None)

    async def _chat_stream(query, context, history=None, system_prompt_override=None, **kw):
        captured["chat_stream_calls"] = captured.get("chat_stream_calls", 0) + 1
        captured["system_prompt_override"] = system_prompt_override
        captured["context"] = context
        yield "通用知识回答"

    fake_rag.chat_stream = _chat_stream
    return fake_rag


def _fake_memory_system() -> MagicMock:
    memory = MagicMock()
    memory._embedding_provider = "stub"
    memory.get_context = AsyncMock(return_value="")
    memory.store_interaction = AsyncMock()
    return memory


def _drain(queue: asyncio.Queue) -> list:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


class TestChatHonestDegradation:
    @pytest.mark.asyncio
    async def test_non_strict_failure_uses_honest_prompt_and_skips_all_caches(self):
        """非严格模式故障：直答可以继续，但必须如实提示检索服务不可用，
        且不得写问答缓存 / 不得污染 groundedness 口径。

        变异验证：恢复 `system_prompt_override = await get_system_prompt(...)`
        （去掉故障分支）→ 提示词断言红；恢复无条件 `_semantic_cache.set` → 缓存断言红。
        """
        from app.services.chat_service import run_rag_pipeline

        session = _mock_chat_session()
        queue: asyncio.Queue = asyncio.Queue()
        sem_cache = _RecordingSemanticCache()
        captured: dict = {}
        fake_rag = _fake_rag_stack(
            RetrievalUnavailableError("ES down", kind="failed"), captured
        )

        with (
            patch("app.services.chat_service.rag_service", new=fake_rag),
            patch("app.services.chat_service._semantic_cache", new=sem_cache),
            patch(
                "app.services.chat_service.get_memory_system",
                new=MagicMock(return_value=_fake_memory_system()),
            ),
        ):
            await run_rag_pipeline(
                session=session, user_content="报销流程是什么", user_id=1,
                search_org_id=1, organization_id=1, conversation_id=None, file_ids=[],
                strict_mode=False, privacy_mode=False, event_queue=queue,
            )

        events = _drain(queue)
        assert "error" not in [e.type for e in events], "非严格模式不应走拒答分支"
        assert any(e.type == "message" and e.content == "通用知识回答" for e in events)

        prompt = captured.get("system_prompt_override") or ""
        assert "检索服务暂时不可用" in prompt, "故障必须使用诚实降级提示词"
        assert "暂无相关文档" not in prompt, "不得复用「知识库中暂无相关文档」话术（把故障说成没找到）"

        assert sem_cache.set_calls == [], "故障态答案不得写问答缓存（否则故障形态被固化 3600s）"
        assert fake_rag.report_grounded.call_count == 0, "故障不是「未命中来源」，不得污染 groundedness 口径"

    @pytest.mark.asyncio
    async def test_strict_mode_failure_refuses_explicitly_without_llm_call(self):
        """严格模式承诺「只根据文档作答」：检索不可用即如实拒答（不伪装成「文档里没有」）。

        变异验证：去掉严格模式拒答分支（故障继续直答）→ 事件断言红。
        """
        from app.services.chat_service import run_rag_pipeline

        session = _mock_chat_session()
        queue: asyncio.Queue = asyncio.Queue()
        captured: dict = {}
        fake_rag = _fake_rag_stack(
            RetrievalUnavailableError("read timeout", kind="timeout"), captured
        )

        with (
            patch("app.services.chat_service.rag_service", new=fake_rag),
            patch(
                "app.services.chat_service.get_memory_system",
                new=MagicMock(return_value=_fake_memory_system()),
            ),
        ):
            await run_rag_pipeline(
                session=session, user_content="报销流程是什么", user_id=1,
                search_org_id=1, organization_id=1, conversation_id=None,
                file_ids=["doc-1"], strict_mode=True, privacy_mode=False, event_queue=queue,
            )

        events = _drain(queue)
        assert [e.type for e in events] == ["error"], f"严格模式故障必须显式拒答: {[e.type for e in events]}"
        assert "检索服务暂不可用" in events[0].content
        assert captured.get("chat_stream_calls", 0) == 0, "拒答路径不得调用 LLM"

    @pytest.mark.asyncio
    async def test_normal_empty_keeps_legacy_no_context_flow(self):
        """回归对照：正常空结果（无故障）不进故障分支——沿用既有直答流程。"""
        from app.services.chat_service import run_rag_pipeline

        session = _mock_chat_session()
        queue: asyncio.Queue = asyncio.Queue()
        captured: dict = {}
        fake_rag = _fake_rag_stack(None, captured)
        fake_rag.search_knowledge_base = AsyncMock(return_value=([], None))

        with (
            patch("app.services.chat_service.rag_service", new=fake_rag),
            patch(
                "app.services.chat_service.get_memory_system",
                new=MagicMock(return_value=_fake_memory_system()),
            ),
        ):
            await run_rag_pipeline(
                session=session, user_content="报销流程是什么", user_id=1,
                search_org_id=1, organization_id=1, conversation_id=None, file_ids=[],
                strict_mode=False, privacy_mode=False, event_queue=queue,
            )

        events = _drain(queue)
        assert "error" not in [e.type for e in events]
        assert any(e.type == "message" for e in events)
        assert captured.get("system_prompt_override") is None, (
            "正常空结果不得被故障提示词接管（无模板时沿用 chat_stream 的既有话术）"
        )
        assert fake_rag.report_grounded.call_count == 1, "正常空结果仍记 groundedness 口径"


# ══════════════════════════════════════════════════════════════════════════
# 6 · 上层诚实降级：agent 工具 + agent 混合链
# ══════════════════════════════════════════════════════════════════════════
class TestAgentChainHonesty:
    @pytest.mark.asyncio
    async def test_kb_tool_returns_structured_failure_not_no_content_text(self):
        """Agent 的 search_knowledge_base 工具：故障 → ToolResult.fail（tool_error /
        run_report 失败通道），绝不当成「没有相关内容」的成功结果。

        变异验证：去掉工具内 except（故障原样上抛/或回落空文案）→ 断言红。
        """
        import app.agent.core_tools  # noqa: F401 — 触发工具注册（execute_detailed 需要）
        from app.agent.registry import ToolResult, tool_registry

        fake_pipeline = MagicMock()
        fake_pipeline.search_knowledge_base = AsyncMock(
            side_effect=RetrievalUnavailableError("ES down", kind="timeout")
        )

        with patch("app.dependencies.get_rag_pipeline", return_value=fake_pipeline):
            result = await tool_registry.execute_detailed(
                "search_knowledge_base", {"query": "配置在哪"}, organization_id=7
            )

        assert isinstance(result, ToolResult)
        assert result.success is False, "故障必须以失败态进入 executor/run_report"
        assert result.error is not None and result.error.code == "knowledge_base_unavailable"
        assert "超时" in result.error.message, "超时态在用户可见文案中保持区分"
        assert "No relevant" not in str(result)

    @pytest.mark.asyncio
    async def test_kb_tool_normal_empty_keeps_legacy_message(self):
        """回归对照：正常空结果仍返回既有文案（工具语义不变）。"""
        from app.agent.core_tools import search_knowledge_base as kb_tool

        fake_pipeline = MagicMock()
        fake_pipeline.search_knowledge_base = AsyncMock(return_value=([], None))

        with patch("app.dependencies.get_rag_pipeline", return_value=fake_pipeline):
            out = await kb_tool("配置在哪", organization_id=7)

        assert out == "No relevant documents found for this query."

    @pytest.mark.asyncio
    async def test_search_and_chat_emits_honest_warning_and_continues(self):
        """agent 混合链：检索故障 → 如实 warning 事件 + 无上下文继续（不崩、不伪装）。"""
        from app.agent.service import agent_service

        fake_pipeline = MagicMock()
        fake_pipeline.search_knowledge_base = AsyncMock(
            side_effect=RetrievalUnavailableError("ES down", kind="failed")
        )

        class _FakeLoop:
            def __init__(self, *args, **kwargs):
                pass

            async def run(self, query, history=None, context_docs=None):
                self.context_docs = context_docs
                yield MagicMock(type="done", content="ok")

        with (
            patch("app.dependencies.get_rag_pipeline", return_value=fake_pipeline),
            patch("app.agent.service.PERAgentLoop", _FakeLoop),
        ):
            agent_service._client = MagicMock()  # 通过 self.client 的早退检查
            try:
                events = []
                async for ev in agent_service.search_and_chat("查询", organization_id=7):
                    events.append(ev)
            finally:
                agent_service._client = None

        warnings = [e for e in events if e.get("type") == "warning"]
        assert warnings, f"故障必须发出如实提示事件，实际: {[e.get('type') for e in events]}"
        assert "检索服务暂不可用" in warnings[0]["content"]
        assert not any(e.get("type") == "context" for e in events), "故障不得伪造上下文事件"


# ══════════════════════════════════════════════════════════════════════════
# 7 · 上层诚实降级：knowledge_service（/search 链 + suggestions）
# ══════════════════════════════════════════════════════════════════════════
def _stub_rag_facade(kind: str):
    """故障注入替身：替换 **模块单例对象**（app.services.rag_service.rag_service），
    而不是 patch 类方法 `RagServiceFacade.search_knowledge_base`。

    原因（issue #292 CI 红复盘）：实例级 monkeypatch 泄漏会把绑定方法写回成实例属性，
    永久 shadow 类属性 → 后续类级 patch 静默失效（同进程整包跑时命中真实 ES 故障）。
    替换模块属性（knowledge_service 函数内 import 每次取模块属性）对实例 shadow 免疫。
    """
    stub = MagicMock()
    stub.search_knowledge_base = AsyncMock(
        side_effect=RetrievalUnavailableError(f"stubbed retrieval failure ({kind})", kind=kind)
    )
    return patch("app.services.rag_service.rag_service", new=stub)


class TestKnowledgeServiceHonesty:
    @pytest.mark.asyncio
    async def test_retrieval_unavailable_is_reraised_not_swallowed_to_empty(self):
        """检索故障不得被 KnowledgeService 吞成 []（修前：`except Exception → []`）。

        变异验证：给 search_knowledge 恢复笼统吞异常 → 红。
        """
        with _stub_rag_facade("failed"):
            with pytest.raises(RetrievalUnavailableError):
                await KnowledgeService().search_knowledge("q", "1", top_k=5)

    @pytest.mark.asyncio
    async def test_suggestions_degrade_to_empty_without_raising(self):
        """搜索建议是 best-effort：故障降级为空列表并留日志，不把故障扩大成 5xx。"""
        with _stub_rag_facade("timeout"):
            out = await KnowledgeService().get_search_suggestions("q", "1")

        assert out == []
