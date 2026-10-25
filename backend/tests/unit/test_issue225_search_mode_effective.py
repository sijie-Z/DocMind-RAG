"""issue #225 / 决策文档第 7 条（默认 A）：search_mode 真正生效。

修前事实：REST 端点接受 `search_mode`（hybrid/vector/keyword）、响应原样回显，
但调用链三层都没把它传到检索层（knowledge_service 与 rag_service 门面两处
静默丢弃），实际永远走 QueryComplexityClassifier 自动路由——排查时「换个模式
再试」是无效动作。

修后语义（本文件钉住）：
- 显式 `search_mode` 强制本次请求的检索臂组合：hybrid=两臂+RRF（不含 HyDE）、
  vector=仅向量臂（`vector_only`，自动路由不会产生）、keyword=仅关键词臂；
- 缺省（None）自动路由行为逐条不变（回归对照）；
- 覆盖只作用于该请求：不读别的模式的缓存、也不把结果写给别人（精确缓存按
  模式分域、显式模式跳过语义缓存）；不污染 chat/agent/workflow 等路径。

变异验证（摘掉覆盖接线 → 本文件精确红）：
- retrieve 里 `resolve_retrieval_strategy` 换回 `QueryComplexityClassifier.get_strategy`
  → TestForcedArmSelection 的 ①②③ 全红（强制模式被当缺省处理）；
- knowledge_service / rag_service 门面 / pipeline 任一处去掉 `search_mode=` 透传
  → TestChainWiring 对应用例红；
- 精确缓存键去掉模式段 → TestModeScopedCache 红。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.rag.query_processor import (
    SEARCH_MODE_TO_STRATEGY,
    QueryComplexityClassifier,
    effective_search_mode,
    resolve_retrieval_strategy,
)
from app.rag.retriever import HybridRetriever

# 探针查询（判别力前提：与自动路由档位不同，强制覆盖才可观测）。
SIMPLE_Q = "报销流程"  # 自动路由 → keyword_only
MEDIUM_Q = "如何提交报销申请？"  # 自动路由 → hybrid
COMPLEX_Q = "请对比分析公司不同部门的报销流程差异和优缺点"  # 自动路由 → hybrid_hyde


class _RecordingCache:
    """记录调用（含 kwargs）的缓存替身；get 恒未命中。"""

    def __init__(self):
        self.get_calls: list = []
        self.set_calls: list = []

    async def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        return

    async def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))


class _FakePipe:
    def __init__(self, r):
        self.r = r
        self.ops: list = []

    def setex(self, *a):
        self.ops.append(("setex", a))

    def zadd(self, *a):
        self.ops.append(("zadd", a))

    async def execute(self):
        for op, a in self.ops:
            if op == "setex":
                await self.r.setex(*a)
            else:
                await self.r.zadd(*a)


class _FakeRedis:
    """内存假 Redis：覆盖 SemanticCache / RetrievalCache 用到的接口。"""

    def __init__(self):
        self.kv: dict = {}
        self.z: dict = {}

    async def get(self, k):
        return self.kv.get(k)

    async def setex(self, k, ttl, v):
        self.kv[k] = v

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zrem(self, key, member):
        self.z.get(key, {}).pop(member, None)

    async def zrangebyscore(self, key, lo, hi):
        return [m for m, s in self.z.get(key, {}).items() if lo <= s <= hi]

    def pipeline(self):
        return _FakePipe(self)


# 同 query 跨请求恒定的查询向量 → 语义相似度 1.0（> 阈值），命中与否只取决于条目是否存在。
_SEM_VEC = [0.5, 0.5, 0.5, 0.5]


def _make_sem_pipeline(retrieve_mock, embedding=None):
    """真 RetrievalCache + 真 SemanticCache（配合假 Redis）的 pipeline。"""
    from app.rag.cache import RetrievalCache, SemanticCache
    from app.rag.pipeline import RAGPipeline

    p = RAGPipeline()
    p.retriever = MagicMock()
    p.retriever.get_embedding = AsyncMock(
        return_value=list(embedding if embedding is not None else _SEM_VEC)
    )
    p.retriever.retrieve = retrieve_mock
    p.cache = RetrievalCache()
    p.semantic_cache = SemanticCache.for_retrieval()
    return p


def _kw_hit(chunk_id: str, text: str, score: float = 0.9) -> dict:
    return {
        "_id": chunk_id,
        "_score": score,
        "_rewrite_hits": 1,
        "_source": {"content": text, "filename": "kw.md", "document_id": chunk_id},
    }


def _vec_hit(chunk_id: str, text: str, score: float = 1.7) -> dict:
    return {
        "_id": chunk_id,
        "_score": score,
        "_source": {"content": text, "filename": "vec.md", "document_id": chunk_id},
    }


def _wire_arms(monkeypatch, retriever, kw_hits, vec_hits, query_vector=(0.1, 0.2, 0.3, 0.4)):
    """装上两臂替身并返回调用记录 (kw_calls, vec_calls, vec_hyde_flags)。"""
    kw_calls: list = []
    vec_calls: list = []
    vec_hyde_flags: list = []

    async def fake_kw(query, filters, candidate_size):
        kw_calls.append(query)
        return [dict(h) for h in kw_hits]

    async def fake_vec(query, filters, candidate_size, enable_hyde=True):
        vec_calls.append(query)
        vec_hyde_flags.append(enable_hyde)
        return [dict(h) for h in vec_hits], list(query_vector), None

    monkeypatch.setattr(retriever, "_keyword_hits", fake_kw)
    monkeypatch.setattr(retriever, "_vector_hits", fake_vec)
    return kw_calls, vec_calls, vec_hyde_flags


def _spy_classifier(monkeypatch):
    """分类器咨询 spy：显式模式下不得被咨询（覆盖 = 跳过自动路由）。

    保留真实分类行为（返回真实策略），让「覆盖失效」时先红在臂组合断言上
    （vec_calls/kw_calls 零调用断言），而不是被 tripwire 抛错掩盖。
    """
    calls: list = []
    real = QueryComplexityClassifier.get_strategy

    def _rec(cls, q):
        calls.append(q)
        return real(q)

    monkeypatch.setattr(QueryComplexityClassifier, "get_strategy", classmethod(_rec))
    return calls


class TestForcedArmSelection:
    """①②③：显式模式对检索臂组合的强制（spy/计数断言）。"""

    @pytest.mark.asyncio
    async def test_forced_keyword_never_calls_vector_arm(self, monkeypatch):
        """①强制 keyword → 仅关键词臂：vector 臂零调用（连分类器都不咨询）。"""
        assert QueryComplexityClassifier.get_strategy(MEDIUM_Q) == "hybrid", (
            "前提：自动路由本会跑两臂"
        )
        retriever = HybridRetriever()
        kw_hits = [_kw_hit("doc_kw", "如何提交报销申请正文")]
        vec_hits = [_vec_hit("doc_vec", "如何提交报销申请 另一份内容")]
        kw_calls, vec_calls, _ = _wire_arms(monkeypatch, retriever, kw_hits, vec_hits)
        clf_calls = _spy_classifier(monkeypatch)

        docs, qv, dbg, _deg = await retriever.retrieve(
            MEDIUM_Q, 1, top_k=5, debug=True, search_mode="keyword"
        )

        assert vec_calls == [], "强制 keyword 时 vector 臂不得被调用（零调用断言）"
        assert clf_calls == [], "显式模式不得咨询分类器（覆盖 = 跳过自动路由）"
        assert kw_calls == [MEDIUM_Q]
        assert qv == [], "keyword 序无查询向量"
        ids = [d["chunk_id"] for d in docs]
        assert ids == ["doc_kw"], f"只应返回 keyword 臂命中，实际 {ids}"
        assert docs[0]["has_keyword"] is True
        assert dbg["strategy"] == "keyword_only" and dbg["search_mode"] == "keyword"

    @pytest.mark.asyncio
    async def test_forced_vector_never_calls_keyword_arm(self, monkeypatch):
        """②强制 vector → 仅向量臂：keyword 臂零调用（连分类器都不咨询）。"""
        assert QueryComplexityClassifier.get_strategy(MEDIUM_Q) == "hybrid", (
            "前提：自动路由本会跑两臂"
        )
        retriever = HybridRetriever()
        kw_hits = [_kw_hit("doc_kw", "如何提交报销申请正文")]
        vec_hits = [_vec_hit("doc_vec", "如何提交报销申请 另一份内容")]
        kw_calls, vec_calls, vec_hyde_flags = _wire_arms(monkeypatch, retriever, kw_hits, vec_hits)
        clf_calls = _spy_classifier(monkeypatch)

        docs, qv, dbg, _deg = await retriever.retrieve(
            MEDIUM_Q, 1, top_k=5, debug=True, search_mode="vector"
        )

        assert kw_calls == [], "强制 vector 时 keyword 臂不得被调用（零调用断言）"
        assert clf_calls == [], "显式模式不得咨询分类器（覆盖 = 跳过自动路由）"
        assert vec_calls == [MEDIUM_Q]
        assert vec_hyde_flags == [False], "显式 vector 不跑 HyDE（m1 定夺：与显式 hybrid 对齐）"
        assert qv == [0.1, 0.2, 0.3, 0.4], "向量臂产出的查询向量应随结果返回"
        ids = [d["chunk_id"] for d in docs]
        assert ids == ["doc_vec"], f"只应返回向量臂命中，实际 {ids}"
        assert docs[0]["has_vector"] is True
        assert dbg["strategy"] == "vector_only" and dbg["search_mode"] == "vector"

    @pytest.mark.asyncio
    async def test_forced_hybrid_runs_both_arms_and_fuses(self, monkeypatch):
        """③强制 hybrid → 两臂都调用且 RRF 融合生效（含「覆盖逆着自动路由」方向）。

        用 SIMPLE_Q（自动路由 keyword_only）：若覆盖失效，vector 臂零调用、
        融合不发生——本用例双断言（调用计数 + 融合产物含向量臂独有命中）。
        """
        assert QueryComplexityClassifier.get_strategy(SIMPLE_Q) == "keyword_only", (
            "前提：自动路由不会跑向量臂"
        )
        retriever = HybridRetriever()
        kw_hits = [_kw_hit("doc_kw", "报销流程审批说明")]
        vec_hits = [_vec_hit("doc_vec", "报销流程 另一份内容")]
        kw_calls, vec_calls, vec_hyde_flags = _wire_arms(monkeypatch, retriever, kw_hits, vec_hits)
        clf_calls = _spy_classifier(monkeypatch)

        fuse_calls: list = []
        real_fuse = HybridRetriever._rrf_fuse

        def spy_fuse(keyword_hits, vector_hits):
            fuse_calls.append((len(keyword_hits), len(vector_hits)))
            return real_fuse(keyword_hits, vector_hits)

        monkeypatch.setattr(HybridRetriever, "_rrf_fuse", staticmethod(spy_fuse))

        docs, _qv, dbg, _deg = await retriever.retrieve(
            SIMPLE_Q, 1, top_k=5, debug=True, search_mode="hybrid"
        )

        assert kw_calls == [SIMPLE_Q] and vec_calls == [SIMPLE_Q], "两臂都必须被调用"
        assert clf_calls == [], "显式模式不得咨询分类器（覆盖 = 跳过自动路由）"
        assert vec_hyde_flags == [False], (
            "显式 hybrid 不含 HyDE（enable_hyde=False，与自动 hybrid 档一致）"
        )
        assert fuse_calls == [(1, 1)], "RRF 融合路径必须被执行"
        ids = [d["chunk_id"] for d in docs]
        assert "doc_kw" in ids and "doc_vec" in ids, f"两臂命中都应进入融合结果，实际 {ids}"
        assert dbg["strategy"] == "hybrid" and dbg["search_mode"] == "hybrid"

    @pytest.mark.asyncio
    async def test_forced_vector_empty_does_not_fall_back(self, monkeypatch):
        """模式纯度：显式 vector 无命中时不静默兜底混入 keyword 臂。"""
        retriever = HybridRetriever()
        fb_calls: list = []

        async def spy_fallback(query, filters, top_k):
            fb_calls.append(query)
            return [
                {"_id": "fb", "_score": 1.0, "_source": {"content": "兜底", "document_id": "fb"}}
            ], None

        _, vec_calls, _ = _wire_arms(monkeypatch, retriever, [_kw_hit("doc_kw", "x")], [])
        monkeypatch.setattr(retriever, "_fallback", spy_fallback)

        docs, _qv, _dbg, deg = await retriever.retrieve(MEDIUM_Q, 1, top_k=5, search_mode="vector")

        assert docs == [] and fb_calls == [], (
            "显式 vector 空结果不得走 keyword 兜底（否则模式说了不算）"
        )
        assert vec_calls == [MEDIUM_Q] and deg == []


class TestAutoRouteUnchanged:
    """④缺省（None）回归对照：自动路由行为与修复前逐条一致。"""

    def test_resolve_none_is_byte_identical_to_classifier(self):
        for q, expected in (
            (SIMPLE_Q, "keyword_only"),
            (MEDIUM_Q, "hybrid"),
            (COMPLEX_Q, "hybrid_hyde"),
        ):
            assert resolve_retrieval_strategy(q, None) == expected
            assert resolve_retrieval_strategy(q) == QueryComplexityClassifier.get_strategy(q)

    @pytest.mark.asyncio
    async def test_auto_simple_query_still_keyword_only(self, monkeypatch):
        retriever = HybridRetriever()
        kw_calls, vec_calls, _ = _wire_arms(
            monkeypatch,
            retriever,
            [_kw_hit("doc_kw", "报销流程审批说明")],
            [_vec_hit("doc_vec", "报销流程")],
        )
        docs, _qv, dbg, _deg = await retriever.retrieve(SIMPLE_Q, 1, top_k=5, debug=True)
        assert kw_calls == [SIMPLE_Q] and vec_calls == [], "缺省时 simple 仍不跑向量臂"
        assert dbg["strategy"] == "keyword_only" and dbg["search_mode"] is None
        assert [d["chunk_id"] for d in docs] == ["doc_kw"]

    @pytest.mark.asyncio
    async def test_auto_medium_query_still_runs_both_arms_without_hyde(self, monkeypatch):
        retriever = HybridRetriever()
        kw_calls, vec_calls, vec_hyde_flags = _wire_arms(
            monkeypatch,
            retriever,
            [_kw_hit("doc_kw", "alpha bravo charlie 正文")],
            [_vec_hit("doc_vec", "alpha bravo charlie 另一份")],
        )
        _docs, _qv, dbg, _deg = await retriever.retrieve(MEDIUM_Q, 1, top_k=5, debug=True)
        assert kw_calls == [MEDIUM_Q] and vec_calls == [MEDIUM_Q]
        assert vec_hyde_flags == [False], "缺省 hybrid 档仍不带 HyDE"
        assert dbg["strategy"] == "hybrid" and dbg["search_mode"] is None

    def test_unknown_mode_falls_back_to_auto(self):
        """防御口径：未知字符串按「未提供」处理（API 边界由枚举 422 兜住）。"""
        assert resolve_retrieval_strategy(SIMPLE_Q, "bogus") == "keyword_only"
        assert effective_search_mode(SIMPLE_Q, "bogus") == "keyword"


class TestResolutionHelpers:
    """映射与回显口径（生效语义单点解析）。"""

    def test_forced_mapping_is_independent_of_query(self):
        assert SEARCH_MODE_TO_STRATEGY == {
            "keyword": "keyword_only",
            "vector": "vector_only",
            "hybrid": "hybrid",
        }
        for q in (SIMPLE_Q, MEDIUM_Q, COMPLEX_Q):
            assert resolve_retrieval_strategy(q, "keyword") == "keyword_only"
            assert resolve_retrieval_strategy(q, "vector") == "vector_only"
            assert resolve_retrieval_strategy(q, "hybrid") == "hybrid"

    def test_effective_mode_matches_actual_route(self):
        # 显式值回显自身
        assert effective_search_mode(COMPLEX_Q, "keyword") == "keyword"
        # 自动路由回显顶层查询路由档位映射的公开模式名（hybrid_hyde → hybrid）
        assert effective_search_mode(SIMPLE_Q, None) == "keyword"
        assert effective_search_mode(MEDIUM_Q, None) == "hybrid"
        assert effective_search_mode(COMPLEX_Q, None) == "hybrid"


class TestChainWiring:
    """三层调用链接线（修前的两个静默丢弃点 + pipeline→retriever）。"""

    @pytest.mark.asyncio
    async def test_knowledge_service_passes_search_mode_to_facade(self, monkeypatch):
        import app.services.rag_service as rag_service_mod
        from app.services.knowledge_service import KnowledgeService

        fake = MagicMock()
        fake.search_knowledge_base = AsyncMock(return_value=([], None))
        monkeypatch.setattr(rag_service_mod, "rag_service", fake)

        await KnowledgeService().search_knowledge("q", "1", top_k=5, search_mode="keyword")
        assert fake.search_knowledge_base.await_args.kwargs["search_mode"] == "keyword", (
            "knowledge_service 曾静默丢弃 search_mode（修前的第一个丢弃点）"
        )

        fake.search_knowledge_base.reset_mock()
        await KnowledgeService().search_knowledge("q", "1", top_k=5)
        assert fake.search_knowledge_base.await_args.kwargs["search_mode"] is None, (
            "缺省必须传 None（自动路由），不得再落回字面量 'hybrid'"
        )

    @pytest.mark.asyncio
    async def test_facade_passes_search_mode_to_pipeline(self, monkeypatch):
        import app.services.rag_service as mod

        pipeline = MagicMock()
        pipeline.search_knowledge_base = AsyncMock(return_value=([], None))
        monkeypatch.setattr(mod, "get_rag_pipeline", lambda: pipeline)

        await mod.rag_service.search_knowledge_base("q", 1, top_k=5, search_mode="vector")
        assert pipeline.search_knowledge_base.await_args.kwargs["search_mode"] == "vector", (
            "rag_service 门面曾收下 search_mode 后丢弃（修前的第二个丢弃点）"
        )

    @pytest.mark.asyncio
    async def test_agent_tool_passes_hybrid_mode_to_pipeline(self, monkeypatch):
        import app.dependencies as dependencies
        from app.agent.core_tools import search_knowledge_base

        pipeline = MagicMock()
        pipeline.search_knowledge_base = AsyncMock(return_value=([], None))
        monkeypatch.setattr(dependencies, "get_rag_pipeline", lambda: pipeline)
        document_ids = ["doc-2", "doc-1"]

        await search_knowledge_base(
            "报销流程",
            top_k=7,
            document_ids=document_ids,
            mode="hybrid",
            organization_id=9,
        )

        pipeline.search_knowledge_base.assert_awaited_once_with(
            query="报销流程",
            organization_id=9,
            top_k=7,
            document_ids=document_ids,
            search_mode="hybrid",
        )
        assert pipeline.search_knowledge_base.await_args.kwargs["document_ids"] is document_ids

    @pytest.mark.asyncio
    async def test_pipeline_passes_search_mode_to_retriever(self, monkeypatch):
        from app.rag.pipeline import RAGPipeline

        pipeline = RAGPipeline()
        pipeline.retriever = MagicMock()
        pipeline.retriever.retrieve = AsyncMock(return_value=([], [], None, []))
        pipeline.cache = _RecordingCache()
        pipeline.semantic_cache = _RecordingCache()

        await pipeline.search_knowledge_base("报销流程", 1, top_k=5, search_mode="keyword")
        assert pipeline.retriever.retrieve.await_args.kwargs["search_mode"] == "keyword"


class TestModeScopedCache:
    """覆盖仅作用于该请求：缓存按模式分域（不借位、不污染）。"""

    def test_exact_key_auto_is_unchanged_and_forced_is_scoped(self):
        from app.rag.cache import RetrievalCache

        cache = RetrievalCache()
        # 缺省键与修前逐字节相同（回归对照）
        assert cache._key("Q", 2, 10) == "org=2|topk=10|q=q"
        assert cache._key("Q", 2, 10, None) == "org=2|topk=10|q=q"
        # 显式模式分域且互不相同
        assert cache._key("Q", 2, 10, "vector") == "org=2|topk=10|mode=vector|q=q"
        keys = {cache._key("Q", 2, 10, m) for m in ("vector", "keyword", "hybrid")} | {
            cache._key("Q", 2, 10)
        }
        assert len(keys) == 4, "自动路由与三种显式模式的缓存键必须两两不同"

    @pytest.mark.asyncio
    async def test_cache_calls_carry_search_mode(self):
        from app.rag.pipeline import RAGPipeline

        pipeline = RAGPipeline()
        pipeline.retriever = MagicMock()
        pipeline.retriever.retrieve = AsyncMock(return_value=([], [], None, []))
        pipeline.cache = _RecordingCache()
        pipeline.semantic_cache = _RecordingCache()

        await pipeline.search_knowledge_base("报销流程", 1, top_k=5, search_mode="keyword")
        assert pipeline.cache.get_calls[0][1]["search_mode"] == "keyword", (
            "精确缓存读必须带模式分域"
        )
        assert pipeline.semantic_cache.get_calls == [], "显式模式跳过语义缓存读"

        # 自动路由（对照）：读不带模式段、语义缓存照常参与
        pipeline2 = RAGPipeline()
        pipeline2.retriever = MagicMock()
        pipeline2.retriever.get_embedding = AsyncMock(return_value=[0.1, 0.2])
        pipeline2.retriever.retrieve = AsyncMock(return_value=([], [], None, []))
        pipeline2.cache = _RecordingCache()
        pipeline2.semantic_cache = _RecordingCache()
        await pipeline2.search_knowledge_base("报销流程", 1, top_k=5)
        assert pipeline2.cache.get_calls[0][1]["search_mode"] is None
        assert len(pipeline2.semantic_cache.get_calls) == 1, "缺省时语义缓存按原路径参与"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", ["keyword", "vector", "hybrid"])
    async def test_forced_request_skips_semantic_read_write_and_embedding(self, monkeypatch, mode):
        """显式模式（三模式矩阵）：语义缓存读/写全 0 + 不产生查询 embedding。

        评审 B1 修复后的非真空锁：retrieve 替身回传 **非空 qv**（vector/hybrid 臂
        的真实形态；keyword 臂也用非空 qv 做对抗加固）。修前写条件经回填的
        `query_vector` 变真、照写语义缓存——keyword「不泄漏」只是其臂回传空向量的
        侥幸。变异：摘掉 `and not search_mode` 写守卫 → 本用例精确红。
        """
        from app.rag.pipeline import RAGPipeline

        monkeypatch.setattr(
            "app.rag.pipeline.rerank",
            AsyncMock(
                side_effect=lambda query, docs, client=None, **kwargs: (
                    docs,
                    {
                        "status": "disabled",
                        "path": "noop",
                        "reason": "test_stub",
                        "attempts": [],
                        "scores_attached": False,
                        "model": None,
                    },
                )
            ),
        )
        healthy = [
            {
                "chunk_id": "c1",
                "content": "报销流程正文",
                "filename": "p.md",
                "score": 0.9,
                "document_id": "d1",
            }
        ]

        pipeline = RAGPipeline()
        pipeline.retriever = MagicMock()
        pipeline.retriever.get_embedding = AsyncMock(return_value=[0.1, 0.2])
        pipeline.retriever.retrieve = AsyncMock(
            return_value=(healthy, [0.1, 0.2, 0.3, 0.4], None, [])
        )
        pipeline.cache = _RecordingCache()
        pipeline.semantic_cache = _RecordingCache()

        await pipeline.search_knowledge_base("报销流程", 1, top_k=5, search_mode=mode)

        assert pipeline.semantic_cache.get_calls == [], (
            "显式模式不得读语义缓存（防借位其它模式的结果）"
        )
        assert pipeline.semantic_cache.set_calls == [], "显式模式结果不得写语义缓存（B1 写侧守卫）"
        assert pipeline.retriever.get_embedding.await_count == 0, (
            "显式模式请求不应产生查询 embedding 计算"
        )
        assert pipeline.cache.set_calls and pipeline.cache.set_calls[0][1]["search_mode"] == mode, (
            "结果照常写自己的模式分域"
        )

    @pytest.mark.asyncio
    async def test_forced_mode_results_invisible_to_auto_with_real_cache(self, monkeypatch):
        """端到端分域（真 RetrievalCache 内存回退，仅内存、禁 Redis）：

        强制模式的写入不被缺省请求命中；同模式重复请求照常命中。
        变异：缓存键去掉模式段 → 缺省请求会命中强制模式的写入 → 本用例红。
        """
        from app.rag.cache import RetrievalCache
        from app.rag.pipeline import RAGPipeline

        monkeypatch.setattr("app.rag.cache.settings.RAG_ENABLE_CACHE", True)
        monkeypatch.setattr(
            "app.rag.cache._get_redis", lambda: None
        )  # 只走内存回退，避免触真 Redis
        monkeypatch.setattr(
            "app.rag.pipeline.rerank",
            AsyncMock(
                side_effect=lambda query, docs, client=None, **kwargs: (
                    docs,
                    {
                        "status": "disabled",
                        "path": "noop",
                        "reason": "test_stub",
                        "attempts": [],
                        "scores_attached": False,
                        "model": None,
                    },
                )
            ),
        )
        probe_q = "分域探针查询-225"
        retrieved = [
            {
                "chunk_id": "kw_only",
                "content": "分域探针查询正文",
                "filename": "p.md",
                "score": 0.9,
                "document_id": "d1",
            }
        ]

        def _make_pipeline(retrieve_mock):
            p = RAGPipeline()
            p.retriever = MagicMock()
            p.retriever.get_embedding = AsyncMock(return_value=[])
            p.retriever.retrieve = retrieve_mock
            p.cache = RetrievalCache()
            p.semantic_cache = _RecordingCache()
            return p

        p1 = _make_pipeline(AsyncMock(return_value=(retrieved, [], None, [])))
        await p1.search_knowledge_base(probe_q, 1, top_k=5, search_mode="keyword")

        # 缺省请求：不得命中强制模式的写入 → 必须真正执行检索
        p2 = _make_pipeline(AsyncMock(return_value=([], [], None, [])))
        await p2.search_knowledge_base(probe_q, 1, top_k=5)
        assert p2.retriever.retrieve.await_count == 1, (
            "缺省请求必须真正执行检索（强制模式缓存对其不可见）"
        )

        # 同模式重复：命中自己分域的精确缓存 → 不执行检索
        p3 = _make_pipeline(AsyncMock(return_value=([], [], None, [])))
        await p3.search_knowledge_base(probe_q, 1, top_k=5, search_mode="keyword")
        assert p3.retriever.retrieve.await_count == 0, "同模式重复请求应命中自己模式分域的精确缓存"

    @pytest.mark.asyncio
    async def test_forced_vector_write_does_not_leak_into_auto_causal_chain(self, monkeypatch):
        """评审 B1 因果闭环（写侧）：强制 vector 的写侧探针（真 SemanticCache + 假 Redis）

        → 同 query（同向量、相似度 1.0）的自动路由必须执行自己的检索，不得吃到
        强制向量臂的结果。这是「显式模式跳过语义缓存读**与写**」承诺的端到端判别；
        修前 vector/hybrid 臂回传的非空 qv 会让写条件变真、污染成立（评审 P2 实测）。

        变异：摘掉语义写条件的 `and not search_mode` 守卫 → 第一条断言先红
        （强制 vector 写了 rag:sem:retrieval 条目），自动路由随之命中该条目、
        retrieve 零调用 → 本条精确红。
        """
        import app.rag.cache as cache_mod

        fake = _FakeRedis()
        monkeypatch.setattr(cache_mod, "_get_redis", lambda: fake)
        # issue #195：语义缓存默认关闭；本用例验证显式模式的读写隔离，
        # 必须显式开启检索语义缓存，否则「零写入」断言会退化为空转。
        monkeypatch.setattr(cache_mod.settings, "SEMANTIC_CACHE_RETRIEVAL_ENABLED", True)
        monkeypatch.setattr(
            "app.rag.pipeline.rerank",
            AsyncMock(
                side_effect=lambda query, docs, client=None, **kwargs: (
                    docs,
                    {
                        "status": "disabled",
                        "path": "noop",
                        "reason": "test_stub",
                        "attempts": [],
                        "scores_attached": False,
                        "model": None,
                    },
                )
            ),
        )

        forced_hit = [
            {
                "chunk_id": "forced_vec_only",
                "content": "强制向量臂独有命中",
                "filename": "f.md",
                "score": 0.9,
                "document_id": "d1",
            }
        ]
        auto_hit = [
            {
                "chunk_id": "auto_route_hit",
                "content": "自动路由命中",
                "filename": "a.md",
                "score": 0.8,
                "document_id": "d2",
            }
        ]
        q = "语义写侧泄漏探针-225"

        p1 = _make_sem_pipeline(AsyncMock(return_value=(forced_hit, list(_SEM_VEC), None, [])))
        await p1.search_knowledge_base(q, 1, top_k=5, search_mode="vector")
        sem_keys = [k for k in fake.kv if "rag:sem:retrieval:" in k]
        assert sem_keys == [], f"显式 vector 不得写语义缓存（B1 写侧守卫），实际写了 {sem_keys}"
        assert not fake.z.get("rag:semantic_index:retrieval:1"), (
            "语义索引 zset 亦不得出现强制模式条目"
        )

        p2 = _make_sem_pipeline(AsyncMock(return_value=(auto_hit, list(_SEM_VEC), None, [])))
        res2, _ = await p2.search_knowledge_base(q, 1, top_k=5)  # 自动路由（同 query、同向量）
        ids2 = [h.get("chunk_id") for h in res2]
        assert p2.retriever.retrieve.await_count == 1, (
            "自动路由必须执行自己的检索（强制 vector 的写入对其不可见）"
        )
        assert ids2 == ["auto_route_hit"], f"自动路由不得吃到强制向量臂结果，实际 {ids2}"

    @pytest.mark.asyncio
    async def test_forced_vector_does_not_read_semantic_cache_written_by_auto(self, monkeypatch):
        """读侧镜像（评审 P2 场景 B）：自动路由写入的语义缓存，不得被同 query 的显式 vector 借位。

        变异：摘掉读侧 `not search_mode` 守卫 → 强制 vector 命中自动路由条目、
        返回 auto 的结果且 retrieve 零调用 → 本用例红。
        """
        import app.rag.cache as cache_mod

        fake = _FakeRedis()
        monkeypatch.setattr(cache_mod, "_get_redis", lambda: fake)
        # issue #195：语义缓存默认关闭；本用例的因果链以「自动路由确实写入
        # 语义缓存」为前提，显式开启检索语义缓存。
        monkeypatch.setattr(cache_mod.settings, "SEMANTIC_CACHE_RETRIEVAL_ENABLED", True)
        monkeypatch.setattr(
            "app.rag.pipeline.rerank",
            AsyncMock(
                side_effect=lambda query, docs, client=None, **kwargs: (
                    docs,
                    {
                        "status": "disabled",
                        "path": "noop",
                        "reason": "test_stub",
                        "attempts": [],
                        "scores_attached": False,
                        "model": None,
                    },
                )
            ),
        )

        auto_hit = [
            {
                "chunk_id": "auto_route_hit",
                "content": "自动路由命中",
                "filename": "a.md",
                "score": 0.8,
                "document_id": "d2",
            }
        ]
        forced_hit = [
            {
                "chunk_id": "forced_vec_only",
                "content": "强制向量臂独有命中",
                "filename": "f.md",
                "score": 0.9,
                "document_id": "d1",
            }
        ]
        q = "语义读侧借位探针-225"

        p1 = _make_sem_pipeline(AsyncMock(return_value=(auto_hit, list(_SEM_VEC), None, [])))
        await p1.search_knowledge_base(q, 1, top_k=5)  # 自动路由：写语义缓存（正常行为）
        assert any("rag:sem:retrieval:" in k for k in fake.kv), "前提：自动路由确实写了语义缓存"

        p2 = _make_sem_pipeline(AsyncMock(return_value=(forced_hit, list(_SEM_VEC), None, [])))
        res2, _ = await p2.search_knowledge_base(q, 1, top_k=5, search_mode="vector")
        ids2 = [h.get("chunk_id") for h in res2]
        assert p2.retriever.retrieve.await_count == 1, (
            "显式 vector 必须执行自己的检索（不得借位自动路由的语义缓存）"
        )
        assert ids2 == ["forced_vec_only"], f"应返回向量臂自己的结果，实际 {ids2}"
