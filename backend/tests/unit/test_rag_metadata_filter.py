"""竞品对标 P0：标签/元数据过滤进入知识检索的判别性测试。

设计要点：
- 标签走 DB 预筛 → document_id terms filter（ES mapping 没有实际写入 tags，
  直接塞 ES filter 会静默过滤成空），因此查询体结构与无过滤时逐字一致；
- ACL blocked_ids 独立 append must_not，任何标签过滤都不能覆盖它；
- 非法字段/值在 schema 阶段 422，绝不进入 ES 查询；
- 空命中 must fail-closed（早退空结果），不能退回全组织检索。

变异实验（恢复后提交）：删映射/交集 → 标签用例红；删 ACL 合并 → ACL 用例红。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.rag.metadata_filter import (
    _NO_MATCH,
    MAX_METADATA_FILTER_SCAN,
    intersect_document_ids,
    normalize_metadata_filters,
    resolve_metadata_filter_document_ids,
)
from app.rag.retriever import build_retrieval_filters
from app.schemas.knowledge import KnowledgeSearchRequest


class TestSchemaWhitelist:
    def test_invalid_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            KnowledgeSearchRequest(
                query="x", organization_id=1, metadata_filters={"content": "secret"}
            )
        assert "metadata_filters" in str(exc.value)
        assert "不支持字段" in str(exc.value)

    @pytest.mark.parametrize(
        "bad",
        [
            {"tags": ["*"]},
            {"tags": ["a", ""]},
            {"tags": 'a" OR 1=1'},
            {"tags": ["x"] * 21},
            {"file_type": 123},
            {"tags": {}},
        ],
    )
    def test_invalid_values_rejected(self, bad):
        with pytest.raises(ValidationError):
            KnowledgeSearchRequest(query="x", organization_id=1, metadata_filters=bad)

    def test_omitted_and_empty_keep_legacy_shape(self):
        assert KnowledgeSearchRequest(query="x", organization_id=1).metadata_filters is None
        assert (
            KnowledgeSearchRequest(
                query="x", organization_id=1, metadata_filters={}
            ).metadata_filters
            is None
        )

    def test_valid_filters_normalized(self):
        req = KnowledgeSearchRequest(
            query="x", organization_id=1, metadata_filters={"tags": "2024", "file_type": ["pdf"]}
        )
        assert req.metadata_filters == {"tags": ["2024"], "file_type": ["pdf"]}


class TestTagPrefilter:
    @pytest.mark.asyncio
    async def test_only_matching_documents_pass_document_id_filter(self):
        """标签只召回匹配文档：预筛交集 → 与旧 document_id terms filter 合流。"""
        rows = [
            ("doc-annual", ["2024", "finance"]),
            ("doc-old", ["2023"]),
            ("doc-null", None),
        ]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))

        ids = await resolve_metadata_filter_document_ids(db, 7, {"tags": ["2024"]})
        assert ids == ["doc-annual"]
        narrowed = intersect_document_ids(["doc-old", "doc-annual"], ids)
        assert narrowed == ["doc-annual"]

        filters = build_retrieval_filters(7, narrowed)
        assert {"terms": {"document_id": ["doc-annual"]}} in filters
        assert "doc-old" not in str(filters)

    @pytest.mark.asyncio
    async def test_private_document_cannot_be_revealed_by_tags(self):
        """B 用户用 tags 过滤也看不到 A 的私有文档：ACL must_not 始终共存。"""
        from app.services.document_acl import with_acl_filter

        rows = [("doc-private", ["2024", "secret"]), ("doc-public", ["2024"])]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
        ids = await resolve_metadata_filter_document_ids(db, 7, {"tags": ["2024"]})
        assert set(ids or []) == {"doc-private", "doc-public"}

        filters = build_retrieval_filters(7, ids)
        filters = with_acl_filter(filters, ["doc-private"])
        assert {"bool": {"must_not": [{"terms": {"document_id": ["doc-private"]}}]}} in filters
        terms = next(f["terms"]["document_id"] for f in filters if "terms" in f)
        assert set(terms) == {"doc-private", "doc-public"}
        # 变异点：删掉 ACL 合并后本断言失败。
        assert any("must_not" in str(f) and "doc-private" in str(f) for f in filters)

    @pytest.mark.asyncio
    async def test_pipeline_keeps_acl_when_tag_filtering(self):
        """真实 pipeline 路径：标签匹配到私有文档，B 的请求仍带 ACL must_not。

        变异点：删 pipeline 里 blocked_document_ids 的传递 → 本条红。
        """
        from app.rag.pipeline import RAGPipeline
        from app.services.document_acl import with_acl_filter

        pipeline = RAGPipeline()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=[("doc-private", ["2024"])]))
        )

        captured: dict = {}

        async def _fake_retrieve(*args, **kwargs):
            captured["document_ids"] = args[3] if len(args) > 3 else kwargs.get("document_ids")
            captured.update(kwargs)
            return [], [], None, []

        async def _fake_blocked(db, org_id, viewer_id):
            assert viewer_id == 42
            return ["doc-private"]

        pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)
        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.services.document_acl.get_blocked_document_ids",
                new=_fake_blocked,
            ),
            patch(
                "app.rag.metadata_filter.resolve_metadata_filter_document_ids",
                new=AsyncMock(return_value=["doc-private"]),
            ),
        ):
            await pipeline.search_knowledge_base(
                "x",
                7,
                top_k=5,
                metadata_filters={"tags": ["2024"]},
                viewer_user_id=42,
            )

        assert captured["document_ids"] == ["doc-private"]
        assert captured["blocked_document_ids"] == ["doc-private"]
        filters = build_retrieval_filters(7, captured["document_ids"])
        filters = with_acl_filter(filters, captured["blocked_document_ids"])
        assert {"bool": {"must_not": [{"terms": {"document_id": ["doc-private"]}}]}} in filters

    @pytest.mark.asyncio
    async def test_zero_intersection_never_falls_back_to_org_wide(self):
        """显式 doc-old + 标签只命中 doc-annual → 早退空结果，ES 不执行、缓存不写。

        变异点（CRITICAL）：intersect_document_ids 把零交集返回 [] → 本条红。
        """
        from app.rag.pipeline import RAGPipeline

        rows = [("doc-annual", ["2024"])]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))

        pipeline = RAGPipeline()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = db.execute

        pipeline.retriever.retrieve = AsyncMock(side_effect=AssertionError("must not retrieve"))
        pipeline.cache.get = AsyncMock(side_effect=AssertionError("must not read cache"))
        pipeline.cache.set = AsyncMock(side_effect=AssertionError("must not write cache"))
        pipeline.semantic_cache.set = AsyncMock(side_effect=AssertionError("must not write cache"))

        with patch("app.core.database.AsyncSessionLocal", return_value=session):
            results, _ = await pipeline.search_knowledge_base(
                "x",
                7,
                top_k=5,
                document_ids=["doc-old"],
                metadata_filters={"tags": ["2024"]},
            )

        assert results == []
        pipeline.retriever.retrieve.assert_not_awaited()
        pipeline.cache.set.assert_not_awaited()
        # 直接断言零交集不是可被 retriever 忽略的空列表。
        assert intersect_document_ids(["doc-old"], ["doc-annual"]) is _NO_MATCH
        # ES filter 在零交集路径上根本没有生成，绝不退化成仅 org/is_enabled。
        assert build_retrieval_filters(7, None) != build_retrieval_filters(7, ["doc-old"])

    @pytest.mark.asyncio
    async def test_zero_match_with_index_error_rejects(self):
        """标签零命中 + 索引配置错误 → 必须拒绝，不能用 200 空结果掩盖故障。

        变异点（IMPORTANT）：index_error 检查放回零命中早退之后 → 本条红。
        """
        from app.rag.errors import RetrievalUnavailableError
        from app.rag.pipeline import RAGPipeline

        pipeline = RAGPipeline()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))

        with (
            patch("app.core.elasticsearch.get_index_error", return_value="dimension mismatch"),
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            pytest.raises(RetrievalUnavailableError, match="索引不可用"),
        ):
            await pipeline.search_knowledge_base(
                "x", 7, top_k=5, metadata_filters={"tags": ["nope"]}
            )

    @pytest.mark.asyncio
    async def test_scan_limit_exceeded_rejects_instead_of_partial_result(self):
        """候选超过上限 → 显式拒绝；不能静默返回部分匹配放大检索范围。"""
        from app.rag.errors import RetrievalUnavailableError

        rows = [(f"doc-{i}", ["2024"]) for i in range(MAX_METADATA_FILTER_SCAN + 1)]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=rows)))
        with pytest.raises(RetrievalUnavailableError, match="超过"):
            await resolve_metadata_filter_document_ids(db, 7, {"tags": ["2024"]})
        stmt = db.execute.await_args.args[0]
        assert stmt._limit == MAX_METADATA_FILTER_SCAN + 1

    @pytest.mark.asyncio
    async def test_empty_match_fails_closed(self):
        """标签无命中 → 哨兵早退，绝不能变成 [] 被检索器当成「不限定」。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ids = await resolve_metadata_filter_document_ids(db, 7, {"tags": ["nope"]})
        assert intersect_document_ids(None, ids) is _NO_MATCH
        assert build_retrieval_filters(7, None) == build_retrieval_filters(7)

    @pytest.mark.asyncio
    async def test_db_failure_fails_closed(self):
        from app.rag.errors import RetrievalUnavailableError

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        with pytest.raises(RetrievalUnavailableError):
            await resolve_metadata_filter_document_ids(db, 7, {"tags": ["2024"]})

    @pytest.mark.asyncio
    async def test_invalid_file_type_returns_no_document(self):
        db = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
        ids = await resolve_metadata_filter_document_ids(db, 7, {"file_type": ["pdf"]})
        assert ids == []
        assert intersect_document_ids(None, ids) is _NO_MATCH


class TestQueryBodyCompatibility:
    def test_without_metadata_filters_query_body_is_unchanged(self):
        """无过滤时查询体与父版本兼容：org/is_enabled 两个 filter，无 document_id terms。"""
        import asyncio

        from app.core.elasticsearch import ElasticsearchTools
        from app.rag.retriever import HybridRetriever

        captured: list[dict] = []
        retriever = HybridRetriever()

        async def _fake_search(query: dict, **kwargs):
            captured.append(query)
            return {"hits": {"hits": []}}

        with patch.object(ElasticsearchTools, "search_documents", new=_fake_search):
            asyncio.run(
                retriever._keyword_hits("hello", build_retrieval_filters(1), candidate_size=12)
            )

        assert captured, "keyword 臂未执行 ES 查询"
        body = captured[0]
        assert body["query"]["bool"]["filter"] == [
            {"term": {"organization_id": "1"}},
            {"bool": {"must_not": [{"term": {"is_enabled": False}}]}},
            {"bool": {"must_not": [{"term": {"is_chunk_enabled": False}}]}},
        ]
        assert "tags" not in str(body)
        assert "metadata_filters" not in str(body)

    def test_no_es_filter_accepts_unknown_field(self):
        """未知字段被 schema 拒绝，不能出现``{"terms": {"content": ...}}``这类 ES 注入。"""
        try:
            KnowledgeSearchRequest(
                query="x", organization_id=1, metadata_filters={"metadata.secret": "x"}
            )
        except ValidationError:
            return
        pytest.fail("非法字段未被拒绝，可能进入 ES 查询体")

    def test_normalizer_never_mutates_input(self):
        original = {"tags": ["2024"]}
        normalize_metadata_filters(original)
        assert original == {"tags": ["2024"]}
