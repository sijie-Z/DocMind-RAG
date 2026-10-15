"""R1/R1b/R2 回归测试：重排序器必须拿到非空正文，子查询合并必须真正去重。

修复前：
- HybridRetriever._post_process 返回的字典没有 _source/content，
  三种重排序策略读到的正文恒为空（配对 [query, ""]），排序退化为任意顺序；
- pipeline 子查询合并读取的 _id/chunk_id/_source.doc_id 全都不存在，
  dedup_key 恒为空，重复 chunk 会占满最终 top_k；
- 调试载荷中的 id/filename 也因同样的形状不匹配而恒为 None/"未知"。
"""
import sys
import types

import pytest

from app.core.config import settings
from app.rag import reranker as reranker_module
from app.rag.pipeline import RAGPipeline
from app.rag.query_processor import QueryComplexityClassifier
from app.rag.reranker import _hit_text, _rerank_llm, _rerank_local, _rerank_zhipu, rerank
from app.rag.retriever import HybridRetriever


def _post_processed_hit(chunk_id: str, text: str, filename: str, score: float) -> dict:
    """构造 HybridRetriever._post_process 形状的命中（带 content/chunk_id）。"""
    return {
        "text": text, "content": text, "filename": filename, "chunk_id": chunk_id,
        "score": score, "snippet": text[:50], "document_id": chunk_id.split("_")[0],
    }


class _FakeCrossEncoder:
    """按候选文本中是否含关键词打分，验证重排序确实使用了真实文本。"""

    def __init__(self, keyword: str = "苹果"):
        self.keyword = keyword
        self.pairs: list[list[str]] | None = None

    def predict(self, pairs, show_progress_bar=False):
        self.pairs = [list(p) for p in pairs]
        scores = [1.0 if self.keyword in p[1] else 0.1 for p in pairs]

        class _Arr:
            def tolist(self):
                return scores

        return _Arr()


class TestRerankerReceivesDocumentText:
    @pytest.mark.asyncio
    async def test_local_reranker_gets_non_empty_passages(self, monkeypatch):
        fake = _FakeCrossEncoder()
        monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: fake)

        hits = [
            _post_processed_hit("doc1_c1", "香蕉的种植方法说明", "banana.md", 0.9),
            _post_processed_hit("doc2_c2", "苹果的种植方法说明", "apple.md", 0.8),
        ]
        out = await _rerank_local("苹果", hits, top_n=2)

        assert all(passage for _, passage in fake.pairs), "重排序器不得收到空文档"
        assert [p[1] for p in fake.pairs] == ["香蕉的种植方法说明", "苹果的种植方法说明"]
        assert [h["chunk_id"] for h in out] == ["doc2_c2", "doc1_c1"], "排序必须依据真实文本"

    @pytest.mark.asyncio
    async def test_public_rerank_api_uses_content_from_post_processed_hits(self, monkeypatch):
        fake = _FakeCrossEncoder()
        monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: fake)
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", True)
        monkeypatch.setattr(settings, "RERANK_USE_LOCAL", True)

        hits = [
            _post_processed_hit("doc1_c1", "香蕉相关内容", "banana.md", 0.9),
            _post_processed_hit("doc2_c2", "苹果相关内容", "apple.md", 0.8),
        ]
        out = await rerank("苹果", hits)

        assert all(passage for _, passage in fake.pairs)
        assert out[0]["chunk_id"] == "doc2_c2"

    @pytest.mark.asyncio
    async def test_local_reranker_still_supports_raw_es_hits(self, monkeypatch):
        """MMR/原始命中路径（_source 形态）必须保持可用。"""
        fake = _FakeCrossEncoder()
        monkeypatch.setattr(reranker_module, "_get_cross_encoder", lambda *a, **k: fake)

        raw_hits = [
            {"_id": "1", "_source": {"content": "苹果全文"}},
            {"_id": "2", "_source": {"chunk_text": "香蕉全文"}},
        ]
        await _rerank_local("苹果", raw_hits, top_n=2)

        assert [p[1] for p in fake.pairs] == ["苹果全文", "香蕉全文"]

    def test_hit_text_supports_all_hit_shapes(self):
        assert _hit_text({"content": "后处理结果"}) == "后处理结果"
        assert _hit_text({"_source": {"content": "raw content 字段"}}) == "raw content 字段"
        assert _hit_text({"_source": {"chunk_text": "raw chunk_text 字段"}}) == "raw chunk_text 字段"
        assert _hit_text({"_source": {}}) == ""


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"results": [{"index": 1}, {"index": 0}]}


class _FakeAsyncClient:
    def __init__(self, payloads: list[dict]):
        self.payloads = payloads

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None, timeout=None):
        self.payloads.append(json)
        return _FakeResponse()


class TestZhipuRerankDocuments:
    @pytest.mark.asyncio
    async def test_zhipu_documents_use_content_fallback(self, monkeypatch):
        """R1b：demo.py 索引的文档只有 _source.content，也必须作为非空文档送审。"""
        payloads: list[dict] = []
        fake_httpx = types.SimpleNamespace(AsyncClient=lambda: _FakeAsyncClient(payloads))
        monkeypatch.setattr(settings, "RERANK_API_KEY", "test-key")
        monkeypatch.setattr(settings, "RERANK_API_URL", "https://fake.local")
        monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

        hits = [
            {"_id": "1", "_source": {"content": "演示文档内容", "filename": "demo.md"}},
            {"_id": "2", "_source": {"chunk_text": "旧式字段内容", "filename": "old.md"}},
        ]
        out = await _rerank_zhipu("查询", hits, top_n=2)

        assert payloads and payloads[0]["documents"] == ["演示文档内容", "旧式字段内容"]
        assert [h["_id"] for h in out] == ["2", "1"]


class TestLLMRerankPrompt:
    @pytest.mark.asyncio
    async def test_llm_rerank_prompt_contains_document_text(self):
        captured: dict = {}

        class _Completions:
            async def create(self, **kwargs):
                captured.update(kwargs)

                class _Msg:
                    content = "[2,1]"

                class _Choice:
                    message = _Msg()

                class _Rsp:
                    choices = [_Choice()]

                return _Rsp()

        class _Chat:
            completions = _Completions()

        class _Client:
            chat = _Chat()

        hits = [
            _post_processed_hit("doc1_c1", "第一段正文", "a.md", 0.5),
            _post_processed_hit("doc2_c2", "第二段正文", "b.md", 0.4),
        ]
        out = await _rerank_llm("问题", hits, 2, _Client())

        prompt = captured["messages"][1]["content"]
        assert "第一段正文" in prompt and "第二段正文" in prompt
        assert "文件:b.md" in prompt
        assert [h["chunk_id"] for h in out] == ["doc2_c2", "doc1_c1"]


class TestPostProcessCarriesStableIdentity:
    def test_post_process_carries_chunk_id_and_content(self):
        retriever = HybridRetriever()
        fused = [{
            "_id": "doc9_c7",
            "_score": 0.5,
            "_source": {"content": "正文内容" * 20, "filename": "x.md", "document_id": "doc9"},
        }]

        docs = retriever._post_process("正文内容", fused, 5)

        assert len(docs) == 1
        assert docs[0]["chunk_id"] == "doc9_c7"
        assert docs[0]["content"] == "正文内容" * 20
        assert docs[0]["filename"] == "x.md"

    def test_post_process_synthesizes_chunk_id_without_es_id(self):
        retriever = HybridRetriever()
        fused = [{
            "_score": 0.5,
            "_source": {"content": "正文内容" * 20, "filename": "x.md", "document_id": "doc9"},
        }]

        docs = retriever._post_process("正文内容", fused, 5)

        assert docs[0]["chunk_id"].startswith("doc9:")


class TestSubQueryMergeDedup:
    @pytest.mark.asyncio
    async def test_duplicate_chunks_across_subqueries_are_deduped(self, monkeypatch):
        pipeline = RAGPipeline()
        pipeline.openai_client = object()  # 打开查询分解分支

        dup = _post_processed_hit("doc1_c1", "重复片段", "a.md", 0.9)
        other = _post_processed_hit("doc2_c2", "另一片段", "b.md", 0.8)
        responses = iter([[dup, other], [dict(dup)]])

        async def fake_retrieve(query, organization_id, top_k, document_ids=None, debug=False, search_mode=None):
            # issue #292：retrieve 契约扩为 (results, query_vector, debug_info, degraded)
            # issue #225：retrieve 增加 search_mode 透传参数（本用例恒 None=自动路由）
            return next(responses), [], None, []

        async def fake_decompose(query, client, model, max_sq):
            return ["子问题一", "子问题二"]

        captured: dict = {}

        async def fake_rerank(query, hits, client=None):
            captured["hits"] = list(hits)
            return list(hits)

        monkeypatch.setattr(pipeline.retriever, "retrieve", fake_retrieve)
        monkeypatch.setattr("app.rag.pipeline.decompose_query", fake_decompose)
        monkeypatch.setattr(QueryComplexityClassifier, "classify", classmethod(lambda cls, q: "complex"))
        monkeypatch.setattr("app.rag.pipeline.rerank", fake_rerank)

        results, debug_info = await pipeline.search_knowledge_base(
            "请对比分析两个方案的区别和影响", 1, top_k=5, debug=True,
        )

        assert [h["chunk_id"] for h in captured["hits"]] == ["doc1_c1", "doc2_c2"], \
            "跨子查询的重复 chunk 必须只保留一次"
        assert len(results) == 2
        # 调试载荷同样基于稳定 id / 文件名（修复前恒为 id=None、filename=未知）
        assert debug_info["rerank_result"][0]["id"] == "doc1_c1"
        assert debug_info["rerank_result"][0]["filename"] == "a.md"
        assert debug_info["rerank_reorder"][0]["id"] == "doc1_c1"
