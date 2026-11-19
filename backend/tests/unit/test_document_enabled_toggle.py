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
    """检索 filter 层：停用文档不得被召回。"""

    def test_enabled_filter_present_in_retrieval_filters(self):
        """变异点：删掉 enabled_filter → 本条断言红（filters 只剩 org/文档）。"""
        # 直接检查源码层的 filter 组装：retrieve() 内部 filters 列表
        import inspect

        from app.rag import retriever as retriever_mod

        src = inspect.getsource(retriever_mod)
        assert 'must_not' in src, "检索必须用 must_not 排除 is_enabled=False"
        assert '"is_enabled": False' in src, "filter 必须针对 is_enabled 字段"
        # 明确排除写法是 must_not（而非 term:true）——后者会让旧索引（无字段）漏召回
        assert '"term": {"is_enabled": True}' not in src, (
            "不得用 term:true（旧索引无该字段会漏召回）"
        )

    def test_es_source_includes_is_enabled(self):
        """索引侧：_build_es_source 写入 is_enabled，新文档停用后可被 filter 命中。"""
        import inspect

        from app.worker import doc_processor

        src = inspect.getsource(doc_processor.DocumentProcessor._build_es_source)
        assert '"is_enabled"' in src, "_build_es_source 必须写入 is_enabled 字段"


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
    async def test_es_failure_does_not_rollback_db(self):
        """ES 同步失败时 DB 已改（不回滚），端点仍返回成功——留痕而非静默。"""
        from app.api.v1.endpoints import documents as documents_mod

        fake_doc = MagicMock()
        fake_doc.is_enabled = True

        with (
            patch.object(documents_mod, "get_document_for_user", AsyncMock(return_value=fake_doc)),
            patch.object(
                documents_mod.ElasticsearchTools,
                "update_documents_by_query",
                AsyncMock(side_effect=RuntimeError("ES down")),
            ),
        ):
            body = documents_mod.DocumentEnableUpdate(enabled=False)
            result = await documents_mod.set_document_enabled(
                document_id="doc-2",
                body=body,
                db=AsyncMock(),
                current_user=MagicMock(),
            )

        assert fake_doc.is_enabled is False
        assert result["success"] is True
