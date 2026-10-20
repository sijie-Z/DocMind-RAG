"""issue #190 + #185 + #189 · 分数与状态契约（rerank 绝对分 / 双字段 / 降级可见）。

修前缺陷（origin/develop @ a62d9479 单元级实测）：
- `_rerank_zhipu` 请求体写死 ``"model": "rerank"``，``settings.RERANK_MODEL`` /
  legacy ``RAG_RERANK_MODEL`` 被忽略（#190）；
- zhipu ``relevance_score`` 与本地 CrossEncoder ``predict`` 分数被丢弃，命中无绝对分（#190）；
- local/zhipu 送审 passage 无文件名出处（#190）；
- ``rerank()`` 全链失败静默返回原序（同一个 list 对象），无路径/状态/原因（#189）；
- ``_post_process`` 的 ``score`` 是批内 min-max（榜首恒 1.0），``build_sources_list``
  原样塞进 ``relevanceScore``，前端按「相关度 %」展示 —— 不可证伪（#185）。

修复后契约：
- ``rerank(hits)`` → ``(hits, info)``；``info = {status: ok|degraded|disabled|empty,
  path: local|zhipu|llm|noop, reason, attempts[], scores_attached, model}``；
- 打分路径（local/zhipu）在命中上写 ``rerank_score``（绝对分，不归一化）；
  LLM 路径只给序、无分数（不伪造）；
- pipeline 命中写 ``relevance_score``（绝对分或 None）与 ``rank_score``（相对排序分；
  旧 ``score`` 字段保留为同值别名）；
- ``build_sources_list`` 输出 ``relevanceScore``（绝对分或 None，绝不回填 rank 分）/
  ``rankScore`` / ``rank``。

变异点（M1-M8，前端 F1-F3 见对应的 vitest 用例）：
M1 恢复 ``"model": "rerank"`` 字面量 → TestRerankModelSingleSource 红；
M2 丢弃 relevance_score / predict 分数 → TestRerankAbsoluteScores 红；
M3 passage 不带 filename 前缀 → TestRerankPassageProvenance 红；
M4 rerank 全失败不返回 degraded/reason/attempts → TestRerankStatus 红；
M5 pipeline 不写 debug_info["rerank"] / 不挂 relevance_score → TestPipelineScoreContract 红；
M6 build_sources_list 把 score 回填 relevanceScore → TestSourcesContract 红；
M7 _post_process 不产出 rank_score → TestPostProcessRankScore 红；
M8 重排降级仍写缓存 → TestPipelineScoreContract 的降级用例红。
"""
import logging
import sys
import types

import pytest

from app.core.config import settings
from app.rag import reranker as reranker_module
from app.rag.pipeline import RAGPipeline
from app.rag.reranker import (
    _rerank_llm,
    _rerank_local,
    _rerank_zhipu,
    rerank,
)
from app.rag.retriever import HybridRetriever
from app.services.chat_service import build_sources_list

# ── 替身 ──────────────────────────────────────────────────────────

class _KwCrossEncoder:
    """按 passage 是否含关键词打分（模拟 CrossEncoder 的绝对 logit）。"""

    def __init__(self, keyword: str = "苹果"):
        self.keyword = keyword
        self.pairs: list[list[str]] | None = None

    def predict(self, pairs, show_progress_bar=False):
        self.pairs = [list(p) for p in pairs]
        scores = [2.5 if self.keyword in p[1] else -0.5 for p in pairs]

        class _Arr:
            def tolist(self):
                return scores

        return _Arr()


def _hit(chunk_id: str, text: str, filename: str, score: float = 0.9) -> dict:
    """pipeline 合并后 / _post_process 形状的命中。"""
    return {
        "text": text, "content": text, "prompt_text": text,
        "filename": filename, "chunk_id": chunk_id,
        "score": score, "snippet": text[:40], "document_id": chunk_id.split("_")[0],
    }


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """记录 zhipu rerank 请求体。"""

    def __init__(self, captured: list[dict], payload: dict, status_code: int = 200):
        self.captured = captured
        self.response = _FakeResponse(payload, status_code)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None, timeout=None):
        self.captured.append({"url": url, "headers": headers, "json": json})
        return self.response


def _patch_zhipu(monkeypatch, payload: dict, status_code: int = 200) -> list[dict]:
    captured: list[dict] = []
    fake_httpx = types.SimpleNamespace(
        AsyncClient=lambda: _FakeAsyncClient(captured, payload, status_code)
    )
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)
    monkeypatch.setattr(settings, "RERANK_API_KEY", "test-key")
    monkeypatch.setattr(settings, "RERANK_API_URL", "https://fake.local")
    monkeypatch.setattr(settings, "RAG_RERANK_API_KEY", None)
    monkeypatch.setattr(settings, "RAG_RERANK_API_URL", None)
    return captured


def _zhipu_payload(*pairs: tuple[int, float]) -> dict:
    return {"results": [{"index": idx, "relevance_score": score} for idx, score in pairs]}


# ── #190：模型配置单一真源 ────────────────────────────────────────

class TestRerankModelSingleSource:
    @pytest.mark.asyncio
    async def test_zhipu_request_uses_configured_model(self, monkeypatch):
        captured = _patch_zhipu(monkeypatch, _zhipu_payload((0, 0.9)))
        monkeypatch.setattr(settings, "RERANK_MODEL", "custom-reranker-model")
        monkeypatch.setattr(settings, "RAG_RERANK_MODEL", None)

        out, reason = await _rerank_zhipu("查询", [_hit("d1_c1", "正文", "a.md")], top_n=1)

        assert reason is None and out is not None
        assert captured[0]["json"]["model"] == "custom-reranker-model"

    @pytest.mark.asyncio
    async def test_legacy_alias_used_when_primary_unset(self, monkeypatch):
        captured = _patch_zhipu(monkeypatch, _zhipu_payload((0, 0.9)))
        monkeypatch.setattr(settings, "RERANK_MODEL", None)
        monkeypatch.setattr(settings, "RAG_RERANK_MODEL", "legacy-reranker")

        out, reason = await _rerank_zhipu("查询", [_hit("d1_c1", "正文", "a.md")], top_n=1)

        assert reason is None and out is not None
        assert captured[0]["json"]["model"] == "legacy-reranker"

    @pytest.mark.asyncio
    async def test_protocol_default_when_nothing_configured(self, monkeypatch):
        """两个模型配置都未设置时才回退协议默认值 "rerank"。"""
        captured = _patch_zhipu(monkeypatch, _zhipu_payload((0, 0.9)))
        monkeypatch.setattr(settings, "RERANK_MODEL", None)
        monkeypatch.setattr(settings, "RAG_RERANK_MODEL", None)

        out, reason = await _rerank_zhipu("查询", [_hit("d1_c1", "正文", "a.md")], top_n=1)

        assert reason is None and out is not None
        assert captured[0]["json"]["model"] == "rerank"

    @pytest.mark.asyncio
    async def test_legacy_key_and_url_aliases_are_honored(self, monkeypatch):
        captured = _patch_zhipu(monkeypatch, _zhipu_payload((0, 0.9)))
        monkeypatch.setattr(settings, "RERANK_API_KEY", None)
        monkeypatch.setattr(settings, "RERANK_API_URL", None)
        monkeypatch.setattr(settings, "RAG_RERANK_API_KEY", "legacy-key")
        monkeypatch.setattr(settings, "RAG_RERANK_API_URL", "https://legacy.local")

        out, reason = await _rerank_zhipu("查询", [_hit("d1_c1", "正文", "a.md")], top_n=1)

        assert reason is None and out is not None
        assert captured[0]["url"] == "https://legacy.local/rerank"
        assert captured[0]["headers"]["Authorization"] == "Bearer legacy-key"

    @pytest.mark.asyncio
    async def test_llm_fallback_uses_chat_model_not_rerank_api_model(self, monkeypatch):
        """LLM 兜底走 chat completions：模型必须是对话模型，不是 /rerank 的模型名。"""
        captured: dict = {}

        class _Completions:
            async def create(self, **kwargs):
                captured.update(kwargs)

                class _Msg:
                    content = "[1]"

                class _Choice:
                    message = _Msg()

                class _Rsp:
                    choices = [_Choice()]

                return _Rsp()

        client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=_Completions())
        )
        monkeypatch.setattr(settings, "RERANK_MODEL", "custom-reranker-model")
        monkeypatch.setattr(settings, "ENABLE_LOCAL_LLM", False)
        monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-chat-test")

        out, reason = await _rerank_llm("查询", [_hit("d1_c1", "正文", "a.md")], 1, client)

        assert reason is None and out is not None
        assert captured["model"] == "deepseek-chat-test"


# ── #190：绝对分回传 ─────────────────────────────────────────────

class TestRerankAbsoluteScores:
    @pytest.mark.asyncio
    async def test_zhipu_relevance_score_attached_to_hits(self, monkeypatch):
        _patch_zhipu(monkeypatch, _zhipu_payload((1, 0.93), (0, 0.12)))
        hits = [
            _hit("d1_c1", "香蕉正文", "banana.md"),
            _hit("d2_c2", "苹果正文", "apple.md"),
        ]

        out, reason = await _rerank_zhipu("苹果", hits, top_n=2)

        assert reason is None
        assert [h["chunk_id"] for h in out] == ["d2_c2", "d1_c1"]
        assert out[0]["rerank_score"] == pytest.approx(0.93)
        assert out[1]["rerank_score"] == pytest.approx(0.12)

    @pytest.mark.asyncio
    async def test_local_predict_scores_attached_to_hits(self, monkeypatch):
        fake = _KwCrossEncoder("苹果")
        monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: fake)
        hits = [
            _hit("d1_c1", "香蕉正文", "banana.md"),
            _hit("d2_c2", "苹果正文", "apple.md"),
        ]

        out, reason = await _rerank_local("苹果", hits, top_n=2)

        assert reason is None
        assert [h["chunk_id"] for h in out][:2] == ["d2_c2", "d1_c1"]
        # 绝对 logit 原样保留（不做 min-max）
        assert out[0]["rerank_score"] == pytest.approx(2.5)
        assert out[1]["rerank_score"] == pytest.approx(-0.5)


# ── #190：送审 passage 带出处 ────────────────────────────────────

class TestRerankPassageProvenance:
    @pytest.mark.asyncio
    async def test_local_passages_carry_filename(self, monkeypatch):
        fake = _KwCrossEncoder("苹果")
        monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: fake)

        await _rerank_local(
            "苹果",
            [_hit("d1_c1", "苹果正文", "apple.md"), _hit("d2_c2", "香蕉正文", "banana.md")],
            top_n=2,
        )

        assert fake.pairs is not None
        assert fake.pairs[0][1].startswith("文件:apple.md")
        assert "苹果正文" in fake.pairs[0][1]

    @pytest.mark.asyncio
    async def test_zhipu_documents_carry_filename(self, monkeypatch):
        captured = _patch_zhipu(monkeypatch, _zhipu_payload((0, 0.9)))

        await _rerank_zhipu(
            "查询",
            [_hit("d1_c1", "演示文档内容", "demo.md"), _hit("d2_c2", "旧式内容", "old.md")],
            top_n=2,
        )

        documents = captured[0]["json"]["documents"]
        assert documents[0] == "文件:demo.md\n演示文档内容"
        assert documents[1] == "文件:old.md\n旧式内容"


# ── #189：路径状态与失败信号 ─────────────────────────────────────

def _degrade_all_paths(monkeypatch, caplog):
    """禁用本地模型、无 API key、无 LLM 客户端 —— 全链不可用。"""
    monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", True)
    monkeypatch.setattr(settings, "RERANK_USE_LOCAL", True)
    monkeypatch.setattr(settings, "RERANK_API_KEY", None)
    monkeypatch.setattr(settings, "RAG_RERANK_API_KEY", None)
    monkeypatch.setattr(settings, "RERANK_API_URL", None)
    monkeypatch.setattr(settings, "RAG_RERANK_API_URL", None)
    monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: None)
    caplog.set_level(logging.WARNING, logger="app.rag.reranker")


class TestRerankStatus:
    @pytest.mark.asyncio
    async def test_ok_reports_path_scores_and_model(self, monkeypatch):
        fake = _KwCrossEncoder("苹果")
        monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: fake)
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", True)
        monkeypatch.setattr(settings, "RERANK_USE_LOCAL", True)

        hits = [
            _hit("d1_c1", "香蕉正文", "banana.md"),
            _hit("d2_c2", "苹果正文", "apple.md"),
        ]
        result, info = await rerank("苹果", hits)

        assert info["status"] == "ok"
        assert info["path"] == "local"
        assert info["reason"] is None
        assert info["scores_attached"] is True
        assert info["model"] == settings.RERANK_LOCAL_MODEL
        assert [h["chunk_id"] for h in result] == ["d2_c2", "d1_c1"]

    @pytest.mark.asyncio
    async def test_disabled_by_config_is_explicit(self, monkeypatch):
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", False)
        hits = [_hit("d1_c1", "正文", "a.md")]

        result, info = await rerank("查询", hits)

        assert result is hits
        assert info["status"] == "disabled"
        assert info["path"] == "noop"
        assert "RAG_ENABLE_RERANKER" in info["reason"]

    @pytest.mark.asyncio
    async def test_all_paths_failed_is_degraded_with_attempts_and_warning(
        self, monkeypatch, caplog
    ):
        _degrade_all_paths(monkeypatch, caplog)
        hits = [_hit("d1_c1", "正文一", "a.md"), _hit("d2_c2", "正文二", "b.md")]

        result, info = await rerank("查询", hits)

        assert result is hits, "降级必须保持原始顺序（不丢结果）"
        assert info["status"] == "degraded"
        assert info["path"] == "noop"
        assert info["reason"] == "all_rerank_paths_failed"
        assert [a["path"] for a in info["attempts"]] == ["local", "zhipu", "llm"]
        assert all(a["ok"] is False for a in info["attempts"])
        assert info["scores_attached"] is False
        assert any(
            "degraded" in rec.getMessage().lower() or "reranker" in rec.getMessage().lower()
            for rec in caplog.records
        ), "降级必须有可判定的 warning 日志（#189 修前零信号）"

    @pytest.mark.asyncio
    async def test_empty_hits_is_explicit(self, monkeypatch):
        result, info = await rerank("查询", [])
        assert result == []
        assert info["status"] == "empty"
        assert info["path"] == "noop"

    @pytest.mark.asyncio
    async def test_metric_carries_path_and_outcome_labels(self, monkeypatch):
        from app.core import prometheus

        calls: list[dict] = []

        class _Child:
            def inc(self):
                pass

        class _Total:
            def labels(self, **labels):
                calls.append(labels)
                return _Child()

        monkeypatch.setattr(prometheus, "RAG_RERANK_TOTAL", _Total())
        fake = _KwCrossEncoder("苹果")
        monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: fake)
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", True)
        monkeypatch.setattr(settings, "RERANK_USE_LOCAL", True)

        await rerank("苹果", [_hit("d1_c1", "苹果正文", "apple.md")])

        assert {"path": "local", "outcome": "ok"} in calls


# ── #185/#189：pipeline 契约 ─────────────────────────────────────

def _stub_retrieve(monkeypatch, pipeline: RAGPipeline, hits: list[dict]):
    async def fake_retrieve(query, organization_id, top_k, document_ids=None,
                            debug=False, search_mode=None, doc_cap=None):
        return [dict(h) for h in hits], [], None, []

    monkeypatch.setattr(pipeline.retriever, "retrieve", fake_retrieve)


class TestPipelineScoreContract:
    @pytest.mark.asyncio
    async def test_pipeline_attaches_relevance_score_and_debug_rerank(self, monkeypatch):
        pipeline = RAGPipeline()
        _stub_retrieve(monkeypatch, pipeline, [_hit("d1_c1", "正文一", "a.md", 1.0),
                                               _hit("d2_c2", "正文二", "b.md", 0.8)])

        async def fake_rerank(query, docs, client=None):
            out = [dict(docs[1]), dict(docs[0])]
            out[0]["rerank_score"] = 6.5
            out[1]["rerank_score"] = -1.25
            return out, {
                "status": "ok", "path": "local", "reason": None,
                "attempts": [{"path": "local", "ok": True, "reason": None}],
                "scores_attached": True, "model": "BAAI/bge-reranker-base",
            }

        monkeypatch.setattr("app.rag.pipeline.rerank", fake_rerank)

        results, debug_info = await pipeline.search_knowledge_base(
            "契约查询-190-ok", 1, top_k=2, debug=True,
        )

        assert [r["chunk_id"] for r in results] == ["d2_c2", "d1_c1"]
        assert results[0]["relevance_score"] == pytest.approx(6.5)
        assert results[1]["relevance_score"] == pytest.approx(-1.25)
        assert results[0]["rank_score"] == pytest.approx(0.8)
        assert results[0]["score"] == results[0]["rank_score"]
        assert debug_info["rerank"]["status"] == "ok"
        assert debug_info["rerank"]["path"] == "local"
        assert debug_info["rerank_result"][0]["rerank_score"] == pytest.approx(6.5)

    @pytest.mark.asyncio
    async def test_pipeline_degraded_is_visible_and_never_fabricates_relevance(
        self, monkeypatch
    ):
        pipeline = RAGPipeline()
        _stub_retrieve(monkeypatch, pipeline, [_hit("d1_c1", "正文一", "a.md", 1.0)])

        class _RecordingCache:
            def __init__(self):
                self.set_calls: list = []

            async def get(self, *args, **kwargs):
                return None

            async def set(self, *args, **kwargs):
                self.set_calls.append((args, kwargs))

            def describe_key(self, *args, **kwargs):
                return {}

        cache = _RecordingCache()
        pipeline.cache = cache

        async def fake_rerank(query, docs, client=None):
            return list(docs), {
                "status": "degraded", "path": "noop",
                "reason": "all_rerank_paths_failed",
                "attempts": [
                    {"path": "local", "ok": False, "reason": "local_model_unavailable"},
                    {"path": "zhipu", "ok": False, "reason": "no_api_key"},
                    {"path": "llm", "ok": False, "reason": "no_llm_client"},
                ],
                "scores_attached": False, "model": None,
            }

        monkeypatch.setattr("app.rag.pipeline.rerank", fake_rerank)

        results, debug_info = await pipeline.search_knowledge_base(
            "契约查询-190-degraded", 1, top_k=2, debug=True,
        )

        assert results, "降级不得吞掉已召回的结果"
        assert results[0]["relevance_score"] is None
        assert results[0]["rank_score"] is not None
        assert debug_info["rerank"]["status"] == "degraded"
        assert debug_info["rerank"]["reason"] == "all_rerank_paths_failed"
        assert cache.set_calls == [], (
            "重排降级的原始顺序不得写入缓存（否则 TTL 内后续命中跳过 rerank、"
            "降级信号丢失 —— issue #189）"
        )


# ── #185：来源契约（不把 rank 分冒充相关度） ─────────────────────

class TestSourcesContract:
    def test_rank_score_never_mapped_to_relevance_score(self):
        items = [
            {"filename": "a.md", "score": 1.0, "rank_score": 1.0,
             "relevance_score": None, "snippet": "片一"},
            {"filename": "b.md", "score": 0.8, "rank_score": 0.8,
             "relevance_score": -2.5, "snippet": "片二"},
        ]

        sources = build_sources_list(items)

        assert sources[0]["relevanceScore"] is None, "无绝对分时必须为 None，不得回填 1.0"
        assert sources[0]["rankScore"] == pytest.approx(1.0)
        assert sources[0]["rank"] == 1
        assert sources[1]["relevanceScore"] == pytest.approx(-2.5)
        assert sources[1]["rankScore"] == pytest.approx(0.8)
        assert sources[1]["rank"] == 2

    def test_legacy_hit_without_new_fields_is_honest(self):
        sources = build_sources_list([{"filename": "old.md", "score": 0.99, "snippet": "旧"}])
        source = sources[0]
        assert source["relevanceScore"] is None
        assert source["rankScore"] == pytest.approx(0.99)
        assert source["rank"] == 1


class TestPostProcessRankScore:
    def _fused(self, chunk_id: str, score: float) -> dict:
        text = "苹果 种植 方法 苹果 种植 方法 " * 20
        return {
            "_id": chunk_id,
            "_score": score,
            "_source": {
                "content": text,
                "filename": f"{chunk_id}.md",
                "document_id": chunk_id.split("_")[0],
                "upload_time": "2026-01-01T00:00:00",
            },
            "has_keyword": True,
        }

    def test_post_process_emits_rank_score_and_no_fake_relevance(self):
        retriever = HybridRetriever()
        fused = [self._fused("doc1_c1", 1000.0), self._fused("doc2_c2", 999.0)]

        docs = retriever._post_process("苹果 种植 方法", fused, 5)

        assert [d["chunk_id"] for d in docs] == ["doc1_c1", "doc2_c2"]
        assert docs[0]["rank_score"] == pytest.approx(1.0)
        assert docs[0]["score"] == docs[0]["rank_score"]
        assert docs[0].get("relevance_score") is None, "post-process 不得伪造绝对分"

        sources = build_sources_list(docs)
        assert sources[0]["relevanceScore"] is None, (
            "榜首 rank_score=1.0 不得出现在 relevanceScore —— #185 的不可证伪根源"
        )

    def test_rank_score_is_scale_invariant_but_never_relevance(self):
        """原始分放大 1000 倍：rank_score 仍为 1.0（相对排序），relevance 仍无。"""
        retriever = HybridRetriever()
        small = retriever._post_process(
            "苹果 种植 方法",
            [self._fused("doc1_c1", 1.0), self._fused("doc2_c2", 0.999)],
            5,
        )
        big = retriever._post_process(
            "苹果 种植 方法",
            [self._fused("doc1_c1", 1000.0), self._fused("doc2_c2", 999.0)],
            5,
        )

        assert small[0]["rank_score"] == pytest.approx(1.0)
        assert big[0]["rank_score"] == pytest.approx(1.0)
        for docs in (small, big):
            assert build_sources_list(docs)[0]["relevanceScore"] is None
