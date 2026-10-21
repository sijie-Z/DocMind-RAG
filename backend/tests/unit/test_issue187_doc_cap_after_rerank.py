"""#187 判别测试：2/文档帽从「rerank 之前」移到「rerank 之后」。

实验事实（`backend/docs/eval-gate-phase2-experiment.md`）：`_post_process` 在
pipeline 调用 rerank **之前**按文档截断——同一文档第 3 个质量达标的 chunk 在
rerank 看到它之前就被剔掉；放开 doc 帽后 ClauseHit@5 80%→88%，但 NearMiss@5
12.5%→20.8%（结构性代价）。本 PR 的修法（分诊报告 #187 推荐）：
- `retrieve(..., doc_cap=None)` 把帽从检索内部摘下，让 rerank 看到全部候选；
  默认 `doc_cap=2` 保旧调用方（eval runner / 单测）行为逐字不变；
- pipeline 在 rerank **之后**按查询类型做多样性截断：普通语义查询 2/文档
  （避免 NearMiss 结构性上升），条号直查类查询（`_is_clause_lookup_query`）
  放宽到 4/文档（同文档相邻条/前言与 gold 同属一份文件，2/文档会结构性压掉 gold）。

变异点（红→绿判别）：
- pipeline 的 `apply_doc_cap` 删掉或前移（帽留在 retrieve 内）→
  `test_pipeline_rerank_sees_third_same_doc_chunk` 红（rerank 入参里看不到第 3 块 /
  `doc_cap` 不是 None / 最终每文档块数不对）；
- `_post_process` 默认 doc_cap 改成 None → `test_post_process_default_cap_stays_two` 红；
- 条号直查预算退回 2 → `test_clause_lookup_allows_four_chunks_per_doc` 红。
"""

import pytest

import app.rag.pipeline as pipeline_mod
from app.rag.pipeline import RAGPipeline
from app.rag.query_processor import QueryComplexityClassifier, extract_query_terms
from app.rag.retriever import (
    DOC_CAP_CLAUSE_LOOKUP,
    DOC_CAP_DEFAULT,
    HybridRetriever,
    apply_doc_cap,
    clause_lookup_doc_cap,
)

# 12 个 ASCII 术语的探针查询：overlap 口径无中文分词歧义。
TERM_QUERY = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"


def _raw_hit(chunk_id: str, text: str, score: float = 0.9) -> dict:
    """构造 fused 序的 raw hit 形状（_post_process 入参）。"""
    return {
        "_id": chunk_id,
        "_score": score,
        "_source": {
            "content": text,
            "filename": "x.md",
            "document_id": chunk_id.split("_")[0],
        },
    }


def _post_processed_hit(chunk_id: str, document_id: str, text: str = "正文") -> dict:
    """构造 pipeline 入参形状（retrieve 后处理产物的最小键集）。"""
    return {
        "chunk_id": chunk_id,
        "document_id": document_id,
        "content": text,
        "prompt_text": text,
        "filename": "x.md",
        "score": 0.5,
    }


class TestPostProcessDocCapParam:
    """函数级：doc_cap 默认仍是 2；显式 None 时不截断（rerank 可见全部候选）。"""

    def test_post_process_default_cap_stays_two(self):
        retriever = HybridRetriever()
        fused = [
            _raw_hit("docA_c1", "alpha bravo charlie 正文", 0.9),
            _raw_hit("docA_c2", "alpha bravo charlie 其余内容", 0.8),
            _raw_hit("docA_c3", "alpha bravo charlie 更多内容", 0.7),
        ]
        docs = retriever._post_process(TERM_QUERY, fused, top_k=5)
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docA_c2"], (
            "未接线 rerank 的旧调用方（默认 doc_cap=2）行为必须逐字不变"
        )

    def test_post_process_doc_cap_none_returns_third_chunk(self):
        retriever = HybridRetriever()
        fused = [
            _raw_hit("docA_c1", "alpha bravo charlie 正文", 0.9),
            _raw_hit("docA_c2", "alpha bravo charlie 其余内容", 0.8),
            _raw_hit("docA_c3", "alpha bravo charlie 更多内容", 0.7),
        ]
        docs = retriever._post_process(TERM_QUERY, fused, top_k=5, doc_cap=None)
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docA_c2", "docA_c3"], (
            "doc_cap=None 必须让同文档第 3 个质量达标 chunk 进入 rerank 视野"
        )


class TestApplyDocCapHelper:
    """rerank 之后的多样性截断：保序、按文档计数、缺 document_id 原样保留。"""

    def test_cap_limits_per_document_and_keeps_order(self):
        hits = [
            {"chunk_id": "a1", "document_id": "A"},
            {"chunk_id": "b1", "document_id": "B"},
            {"chunk_id": "a2", "document_id": "A"},
            {"chunk_id": "a3", "document_id": "A"},
            {"chunk_id": "b2", "document_id": "B"},
        ]
        out = apply_doc_cap(hits, 2)
        assert [h["chunk_id"] for h in out] == ["a1", "b1", "a2", "b2"]

    def test_cap_none_is_passthrough(self):
        hits = [{"chunk_id": "a1", "document_id": "A"}, {"chunk_id": "a2", "document_id": "A"}]
        assert apply_doc_cap(hits, None) == hits

    def test_hits_without_document_id_pass_through(self):
        hits = [{"chunk_id": "x"}, {"chunk_id": "y"}]
        assert [h["chunk_id"] for h in apply_doc_cap(hits, 2)] == ["x", "y"]

    def test_clause_lookup_budget_policy(self):
        assert DOC_CAP_DEFAULT == 2
        assert DOC_CAP_CLAUSE_LOOKUP == 4
        assert clause_lookup_doc_cap("第3条规定了什么？") == 4
        assert clause_lookup_doc_cap("《知识库服务管理办法》第七条规定了什么？") == 4
        assert clause_lookup_doc_cap("§2.3 告警响应时限是怎么规定的？") == 4
        assert clause_lookup_doc_cap("审计日志保留多久？") == 2
        assert clause_lookup_doc_cap("回滚的触发条件和步骤是什么？") == 2


def _wire_pipeline(monkeypatch, hits, rerank_order):
    """给 pipeline 装 retriever/rerank 替身并记录调用现场。"""
    pipeline = RAGPipeline()
    seen: dict = {}

    async def fake_retrieve(
        query, organization_id, top_k, document_ids=None, debug=False, search_mode=None, **kwargs
    ):
        seen["retrieve_top_k"] = top_k
        seen["doc_cap_kwarg"] = kwargs.get("doc_cap", "MISSING")
        return [dict(h) for h in hits], [], {"strategy": "keyword_only"}, []

    async def fake_rerank(query, candidates, client=None):
        seen["rerank_ids"] = [c["chunk_id"] for c in candidates]
        order = {cid: i for i, cid in enumerate(rerank_order)}
        ordered = sorted(candidates, key=lambda c: order.get(c["chunk_id"], len(order)))
        # issue #189 契约：rerank 返回 (hits, info)，本替身固定 ok 路径。
        info = {
            "status": "ok",
            "path": "stub",
            "reason": None,
            "attempts": 1,
            "scores_attached": False,
            "model": None,
        }
        return ordered, info

    monkeypatch.setattr(pipeline.retriever, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline_mod, "rerank", fake_rerank)
    return pipeline, seen


class TestPipelineDocCapAfterRerank:
    """端到端：rerank 看到第 3 块；帽在 rerank 之后按查询类型施加。"""

    @pytest.mark.asyncio
    async def test_pipeline_rerank_sees_third_same_doc_chunk(self, monkeypatch):
        hits = [
            _post_processed_hit("docA_c1", "docA", "alpha bravo charlie 一"),
            _post_processed_hit("docA_c2", "docA", "alpha bravo charlie 二"),
            _post_processed_hit("docA_c3", "docA", "alpha bravo charlie 三"),
            _post_processed_hit("docB_c1", "docB", "alpha bravo charlie 四"),
        ]
        pipeline, seen = _wire_pipeline(
            monkeypatch, hits, rerank_order=["docA_c3", "docA_c1", "docA_c2", "docB_c1"]
        )

        results, debug_info = await pipeline.search_knowledge_base(
            TERM_QUERY, organization_id=187001, top_k=3, debug=True
        )

        assert seen["doc_cap_kwarg"] is None, (
            "pipeline 必须显式要求 retrieve 不截断（帽不早于 rerank）；"
            f"实际 doc_cap={seen['doc_cap_kwarg']!r}"
        )
        assert seen["rerank_ids"] == ["docA_c1", "docA_c2", "docA_c3", "docB_c1"], (
            "rerank 必须看到同文档第 3 个 chunk（修前它在 retrieve 内就被帽掉）"
        )
        assert [r["chunk_id"] for r in results] == ["docA_c3", "docA_c1", "docB_c1"], (
            "rerank 后按 2/文档截断：docA_c3 经 rerank 提权后必须存活，docA_c2 被裁"
        )
        div = debug_info["diversity"]
        assert div["stage"] == "post_rerank"
        assert div["per_document_cap"] == 2 and div["clause_lookup"] is False
        assert div["before"] == 4 and div["after"] == 3 and div["dropped"] == 1

    @pytest.mark.asyncio
    async def test_clause_lookup_allows_four_chunks_per_doc(self, monkeypatch):
        hits = [_post_processed_hit(f"docA_c{i}", "docA", f"第3条 正文 {i}") for i in range(1, 6)]
        hits.append(_post_processed_hit("docB_c1", "docB", "第3条 另一文档"))
        query = "第3条规定了什么？"
        pipeline, seen = _wire_pipeline(
            monkeypatch, hits, rerank_order=[h["chunk_id"] for h in hits]
        )

        results, debug_info = await pipeline.search_knowledge_base(
            query, organization_id=187002, top_k=5, debug=True
        )

        ids = [r["chunk_id"] for r in results]
        assert ids == ["docA_c1", "docA_c2", "docA_c3", "docA_c4", "docB_c1"], (
            "条号直查类查询的 rerank 后预算为 4/文档：第 5 个 docA 块被裁，docB 块保留"
        )
        assert debug_info["diversity"]["clause_lookup"] is True
        assert debug_info["diversity"]["per_document_cap"] == 4
        assert debug_info["diversity"]["dropped"] == 1


class TestRetrieveForwardsDocCap:
    """中间链路锁：`retrieve()` 必须把 doc_cap 转发到 `_post_process`。

    变异点：删掉 `retrieve()` 里任一条分支的 `doc_cap=doc_cap` 转发 →
    `test_retrieve_doc_cap_none_exposes_third_chunk` 红（第 3 块重新被内部帽掉）。
    """

    @pytest.mark.asyncio
    async def test_retrieve_doc_cap_none_exposes_third_chunk(self, monkeypatch):
        retriever = HybridRetriever()

        async def fake_kw(query, filters, candidate_size):
            return [
                _raw_hit("docA_c1", "alpha bravo charlie 正文", 0.9),
                _raw_hit("docA_c2", "alpha bravo charlie 其余内容", 0.8),
                _raw_hit("docA_c3", "alpha bravo charlie 更多内容", 0.7),
            ]

        monkeypatch.setattr(retriever, "_keyword_hits", fake_kw)
        monkeypatch.setattr(
            QueryComplexityClassifier,
            "get_strategy",
            classmethod(lambda cls, _q: "keyword_only"),
        )

        docs, _qv, _dbg, _deg = await retriever.retrieve(TERM_QUERY, 1, top_k=20, doc_cap=None)
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docA_c2", "docA_c3"], (
            "doc_cap=None 必须穿过 retrieve() 到达 _post_process"
        )

        docs_default, *_ = await retriever.retrieve(TERM_QUERY, 1, top_k=20)
        assert [d["chunk_id"] for d in docs_default] == ["docA_c1", "docA_c2"], (
            "默认 doc_cap=2 必须仍由 _post_process 施加（评估 / 旧调用方语义不变）"
        )


class TestOverlapProbeUnchanged:
    """回归：本 PR 不动 overlap 口径（术语选择仍是 keyword 臂语料表）。"""

    def test_probe_overlap_shape(self):
        terms = extract_query_terms(TERM_QUERY)
        assert len(terms) == 12
