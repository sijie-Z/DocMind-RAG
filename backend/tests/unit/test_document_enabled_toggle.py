"""文档级启停（is_enabled）的判别性测试。

背景：对标 RAGFlow / Dify / FastGPT 的「禁用/归档文档」——停用后文档
不参与检索召回，但内容与索引保留。此前只能删除或全量重建。

覆盖两层：
1. 检索 filter 必须带 ``must_not: {"term": {"is_enabled": False}}``
   （must_not 写法兼容旧索引：迁移前写入的 chunk 没有该字段，不得漏召回）；
2. PATCH /documents/{id}/enabled 端点改 DB 并同步 ES。

说明：检索侧用**源码级断言**而非挂载 HybridRetriever 跑 retrieve()——
后者的依赖（缓存/rerank/embedding 客户端）在单测环境需要大量打桩，
收益低于成本；filter 组装是纯语句级事实，源码断言 + 变异实验已足够
（已实测：删 enabled_filter 后本条红）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestRetrieverEnabledFilter:
    """检索 filter：停用文档不得被召回，且 DSL 必须合法。"""

    def test_filter_dsl_is_valid_nested_bool(self):
        """变异点（评审 A 实测 C1）：退化成裸 ``{"must_not": [...]}`` →
        ES 8.11 直接 400、全部检索不可用。本条红。"""
        from app.rag.retriever import build_retrieval_filters

        filters = build_retrieval_filters(organization_id=1)
        assert filters[0] == {"term": {"organization_id": "1"}}
        enabled = filters[1]
        assert set(enabled.keys()) == {"bool"}, f"裸 must_not 非法：{enabled}"
        assert enabled["bool"]["must_not"] == [{"term": {"is_enabled": False}}]

    def test_document_ids_kept_in_same_filters(self):
        from app.rag.retriever import build_retrieval_filters

        filters = build_retrieval_filters(1, ["a", "b", ""])
        assert {"terms": {"document_id": ["a", "b"]}} in filters
        # 启停过滤在文档限定查询里也必须存在（否则「指定文档搜索」绕过停用）
        assert any("is_enabled" in str(f) for f in filters)

    def test_es_source_reads_real_document_value(self):
        """变异点：写死 ``"is_enabled": True`` → 停用文档重建后重新被召回，本条红。"""
        from app.worker.doc_processor import DocumentProcessor

        doc = MagicMock()
        doc.is_enabled = False
        doc.organization_id = 1
        doc.id = "d1"
        doc.filename = "f.pdf"
        doc.file_type = "pdf"
        doc.file_size = 1
        doc.created_at = None
        doc.uploaded_by = 1

        src = DocumentProcessor._build_es_source(
            doc, "c1", 0, {"chunk_text": "hello", "metadata": {}}
        )
        assert src["is_enabled"] is False


class TestSetDocumentEnabledEndpoint:
    """PATCH 端点：DB 与 ES 同步。"""

    @pytest.mark.asyncio
    async def test_endpoint_updates_db_and_es(self):
        from app.api.v1.endpoints import documents as documents_mod

        fake_doc = MagicMock()
        fake_doc.is_enabled = True
        fake_doc.organization_id = 1

        db = AsyncMock()
        db.commit = AsyncMock()

        with (
            patch.object(documents_mod, "get_document_for_user", AsyncMock(return_value=fake_doc)),
            patch.object(
                documents_mod.ElasticsearchTools,
                "update_documents_by_query",
                AsyncMock(return_value={"updated": 3}),
            ) as es_mock,
        ):
            body = documents_mod.DocumentEnableUpdate(enabled=False)
            result = await documents_mod.set_document_enabled(
                document_id="doc-1",
                body=body,
                db=db,
                current_user=MagicMock(),
            )

        assert fake_doc.is_enabled is False
        db.commit.assert_awaited()
        es_mock.assert_awaited_once()
        call_kwargs = es_mock.call_args.kwargs
        assert call_kwargs["query"] == {"term": {"document_id": "doc-1"}}
        assert call_kwargs["script"]["params"]["enabled"] is False
        assert result["data"] == {"id": "doc-1", "is_enabled": False}

    @pytest.mark.asyncio
    async def test_es_failure_fails_whole_request(self):
        """ES 失败 → 整体失败且**不改 DB**（检索过滤器读 ES，不能假装成功）。

        变异点：退回「ES 失败仍返回 success 并提交 DB」→ 本条红。
        """
        from app.api.v1.endpoints import documents as documents_mod

        fake_doc = MagicMock()
        fake_doc.is_enabled = True
        fake_doc.organization_id = 1
        db = AsyncMock()

        with (
            patch.object(documents_mod, "get_document_for_user", AsyncMock(return_value=fake_doc)),
            patch.object(
                documents_mod.ElasticsearchTools,
                "update_documents_by_query",
                AsyncMock(side_effect=RuntimeError("ES down")),
            ),
        ):
            body = documents_mod.DocumentEnableUpdate(enabled=False)
            with pytest.raises(documents_mod.AppError):
                await documents_mod.set_document_enabled(
                    document_id="doc-2",
                    body=body,
                    db=db,
                    current_user=MagicMock(),
                )

        assert fake_doc.is_enabled is True, "ES 失败时不得改 DB（否则状态不一致）"
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_success_clears_retrieval_cache(self):
        """启停成功后必须清本组织检索缓存（否则最长 10 分钟仍按旧状态召回）。"""
        from app.api.v1.endpoints import documents as documents_mod

        fake_doc = MagicMock()
        fake_doc.is_enabled = True
        fake_doc.organization_id = 7

        cache = MagicMock()
        cache.clear_for_org = AsyncMock()

        with (
            patch.object(documents_mod, "get_document_for_user", AsyncMock(return_value=fake_doc)),
            patch.object(
                documents_mod.ElasticsearchTools,
                "update_documents_by_query",
                AsyncMock(return_value={"updated": 1}),
            ),
            patch("app.rag.cache.RetrievalCache", return_value=cache),
            patch("app.rag.cache.SemanticCache") as sem_cls,
        ):
            sem_cls.for_retrieval.return_value = MagicMock(clear_for_org=AsyncMock())
            body = documents_mod.DocumentEnableUpdate(enabled=False)
            await documents_mod.set_document_enabled(
                document_id="doc-3",
                body=body,
                db=AsyncMock(),
                current_user=MagicMock(),
            )

        cache.clear_for_org.assert_awaited_once_with(7)
