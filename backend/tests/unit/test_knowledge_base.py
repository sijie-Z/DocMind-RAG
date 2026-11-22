"""P2 多知识库：实体 CRUD、租户边界与检索过滤的判别性测试。

变异点（逐条对应断言）：
- service.get 的 ``organization_id ==`` 条件删掉 → 跨组织 get/update/delete 红；
- service.list 的 org 条件删掉 → 跨组织列表红；
- delete 的文档 UPDATE 去掉 org 条件 → 跨组织文档被解绑红；
- resolve_knowledge_base_document_ids 的 KB org 条件删掉 → 跨组织 KB 可用红；
- resolve 的文档 org 条件删掉 → 跨组织文档被召回红；
- pipeline 的 ``_NO_MATCH`` 早退改成空列表 → 零命中回退全组织红；
- pipeline 的 kb_ids 合流删掉 → KB 过滤不生效红；
- pipeline 的 blocked_ids 传递删掉 → 私有文档借 KB 可见红；
- 无过滤路径误开 DB 会话 → 默认 NULL 兼容性红。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 注册全部表
from app.api.v1.endpoints import knowledge_bases as kb_endpoints
from app.core.database import Base
from app.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)
from app.models.knowledge_base import KnowledgeBase
from app.rag.knowledge_base_filter import resolve_knowledge_base_document_ids
from app.rag.pipeline import RAGPipeline
from app.schemas.knowledge import KnowledgeSearchRequest
from app.services.knowledge_base_service import knowledge_base_service


def _doc(doc_id: str, org_id: int, kb_id: str | None, *, private: bool = False) -> Document:
    return Document(
        id=doc_id,
        filename=f"{doc_id}.txt",
        file_path=f"{org_id}/{doc_id}.txt",
        file_size=1,
        file_type=DocumentType.TXT,
        status=DocumentStatus.INDEXED,
        organization_id=org_id,
        uploaded_by=7,
        knowledge_base_id=kb_id,
        visibility=DocumentVisibility.PRIVATE if private else DocumentVisibility.ORGANIZATION,
    )


@pytest.fixture
async def kb_db():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                KnowledgeBase(
                    id="kb-org1",
                    organization_id=1,
                    name="Org1 KB",
                    description="first",
                ),
                KnowledgeBase(id="kb-org2", organization_id=2, name="Org2 secret KB"),
                _doc("doc-in-kb", 1, "kb-org1"),
                _doc("doc-null", 1, None),
                _doc("doc-other-kb", 1, "kb-org2"),  # 跨组织 kb_id 挂在本组织文档上
                _doc("doc-org2", 2, "kb-org2"),
                _doc("doc-private", 1, "kb-org1", private=True),
            ]
        )
        await session.commit()
    yield maker
    await engine.dispose()


class TestKnowledgeBaseCrud:
    @pytest.mark.asyncio
    async def test_list_get_update_delete_are_org_scoped(self, kb_db):
        async with kb_db() as session:
            items, total = await knowledge_base_service.list(session, 1)
            assert total == 1
            assert [kb.id for kb in items] == ["kb-org1"]

            kb = await knowledge_base_service.get(session, 1, "kb-org1")
            assert kb.name == "Org1 KB"

            updated = await knowledge_base_service.update(
                session, 1, "kb-org1", name="Renamed", description=None
            )
            assert updated.name == "Renamed"

            # 删除只解除本组织文档的关联；NULL 文档与跨组织文档都不受影响。
            await knowledge_base_service.delete(session, 1, "kb-org1")
            rows = (
                (await session.execute(__import__("sqlalchemy").select(Document))).scalars().all()
            )
            by_id = {row.id: row for row in rows}
            assert by_id["doc-in-kb"].knowledge_base_id is None
            assert by_id["doc-private"].knowledge_base_id is None
            assert by_id["doc-other-kb"].knowledge_base_id == "kb-org2"
            assert by_id["doc-org2"].knowledge_base_id == "kb-org2"
            assert await knowledge_base_service.get(session, 2, "kb-org2") is not None

    @pytest.mark.asyncio
    async def test_cross_org_read_update_delete_are_404(self, kb_db):
        async with kb_db() as session:
            with pytest.raises(NotFoundError):
                await knowledge_base_service.get(session, 1, "kb-org2")
            with pytest.raises(NotFoundError):
                await knowledge_base_service.update(session, 1, "kb-org2", name="hijack")
            with pytest.raises(NotFoundError):
                await knowledge_base_service.delete(session, 1, "kb-org2")
            kb = await knowledge_base_service.get(session, 2, "kb-org2")
            assert kb.name == "Org2 secret KB"

    @pytest.mark.asyncio
    async def test_invalid_org_and_name_rejected(self, kb_db):
        async with kb_db() as session:
            with pytest.raises(ValidationError):
                await knowledge_base_service.create(session, 0, name="x")
            with pytest.raises(ValidationError):
                await knowledge_base_service.create(session, 1, name="   ")
            assert (await knowledge_base_service.list(session, 1))[1] == 1


class TestKnowledgeBaseFilter:
    @pytest.mark.asyncio
    async def test_omitted_id_preserves_legacy_shape(self, kb_db):
        async with kb_db() as session:
            assert await resolve_knowledge_base_document_ids(session, 1, None) is None
            assert await resolve_knowledge_base_document_ids(session, 1, "") is None

    @pytest.mark.asyncio
    async def test_only_own_org_kb_documents_pass(self, kb_db):
        async with kb_db() as session:
            ids = await resolve_knowledge_base_document_ids(session, 1, "kb-org1")
        assert set(ids or []) == {"doc-in-kb", "doc-private"}
        assert "doc-null" not in (ids or [])
        assert "doc-org2" not in (ids or [])

    @pytest.mark.asyncio
    async def test_foreign_kb_id_and_zero_match_return_empty_not_all(self, kb_db):
        """跨组织 KB id / 空库返回 []，绝不能被当作「不限定」回退全库。"""
        async with kb_db() as session:
            assert await resolve_knowledge_base_document_ids(session, 1, "kb-org2") == []
            assert await resolve_knowledge_base_document_ids(session, 1, "kb-missing") == []
            empty = await knowledge_base_service.create(session, 1, name="Empty")
            assert await resolve_knowledge_base_document_ids(session, 1, empty.id) == []

    @pytest.mark.asyncio
    async def test_db_failure_fails_closed(self):
        from app.rag.errors import RetrievalUnavailableError

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(RetrievalUnavailableError, match="检索已拒绝"):
            await resolve_knowledge_base_document_ids(db, 1, "kb-1")


class TestPipelineKnowledgeBaseFilter:
    @pytest.mark.asyncio
    async def test_no_kb_id_does_not_open_db_or_narrow_scope(self):
        """默认 NULL：不读 KB 表、不收窄 document_ids，查询结构与旧版一致。"""
        pipeline = RAGPipeline()
        calls: dict = {}

        async def _fake_retrieve(*args, **kwargs):
            calls["document_ids"] = args[3] if len(args) > 3 else kwargs.get("document_ids")
            calls["blocked"] = kwargs.get("blocked_document_ids")
            return [], [], None, []

        pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)
        session = MagicMock()
        session.__aenter__ = AsyncMock(side_effect=AssertionError("must not open DB"))
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("app.core.database.AsyncSessionLocal", return_value=session):
            await pipeline.search_knowledge_base("q", 1, top_k=5)

        assert calls["document_ids"] is None
        assert calls["blocked"] == []
        session.__aenter__.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_zero_match_kb_never_falls_back_to_org_wide(self):
        """零命中 KB 过滤：早退空结果，ES 不执行、缓存不写。

        变异点：把 ``_NO_MATCH`` 早退改成空列表 → retriever 被调用 → 本条红。
        """
        pipeline = RAGPipeline()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        pipeline.retriever.retrieve = AsyncMock(side_effect=AssertionError("must not retrieve"))
        pipeline.cache.get = AsyncMock(side_effect=AssertionError("must not read cache"))
        pipeline.cache.set = AsyncMock(side_effect=AssertionError("must not write cache"))
        pipeline.semantic_cache.set = AsyncMock(side_effect=AssertionError("must not write cache"))

        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.rag.knowledge_base_filter.resolve_knowledge_base_document_ids",
                new=AsyncMock(return_value=[]),
            ),
        ):
            results, debug = await pipeline.search_knowledge_base(
                "q", 1, top_k=5, knowledge_base_id="kb-missing", debug=True
            )

        assert results == []
        assert debug["knowledge_base_filter"]["matched_document_ids"] == []
        pipeline.retriever.retrieve.assert_not_awaited()
        pipeline.cache.set.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_kb_filter_keeps_acl_blocked_ids(self):
        """私有文档即使属于目标 KB，B 用户仍带 must_not；ACL 不被 KB 覆盖。

        变异点：删掉 blocked_document_ids 的传递 → 本条红。
        """
        pipeline = RAGPipeline()
        calls: dict = {}

        async def _fake_retrieve(*args, **kwargs):
            calls.update(kwargs)
            calls["document_ids"] = args[3] if len(args) > 3 else kwargs.get("document_ids")
            return [], [], None, []

        async def _fake_blocked(db, org_id, viewer_id):
            assert viewer_id == 42
            return ["doc-private"]

        pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.services.document_acl.get_blocked_document_ids",
                new=_fake_blocked,
            ),
            patch(
                "app.rag.knowledge_base_filter.resolve_knowledge_base_document_ids",
                new=AsyncMock(return_value=["doc-in-kb", "doc-private"]),
            ),
        ):
            await pipeline.search_knowledge_base(
                "q", 1, top_k=5, knowledge_base_id="kb-org1", viewer_user_id=42
            )

        assert calls["document_ids"] == ["doc-in-kb", "doc-private"]
        assert calls["blocked_document_ids"] == ["doc-private"]

    @pytest.mark.asyncio
    async def test_kb_and_explicit_document_ids_intersect(self):
        """显式 document_ids 与 KB 成员取交集；零交集按早退处理。"""
        pipeline = RAGPipeline()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        pipeline.retriever.retrieve = AsyncMock(side_effect=AssertionError("must not retrieve"))
        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.rag.knowledge_base_filter.resolve_knowledge_base_document_ids",
                new=AsyncMock(return_value=["doc-in-kb"]),
            ),
        ):
            results, _ = await pipeline.search_knowledge_base(
                "q", 1, top_k=5, document_ids=["doc-null"], knowledge_base_id="kb-org1"
            )
        assert results == []
        pipeline.retriever.retrieve.assert_not_awaited()


class TestSchemaAndMigrationRegistration:
    def test_search_request_omitted_kb_stays_none(self):
        assert KnowledgeSearchRequest(query="q", organization_id=1).knowledge_base_id is None
        with pytest.raises(ValueError):
            KnowledgeSearchRequest(query="q", organization_id=1, knowledge_base_id="x" * 37)

    def test_migration_033_down_revision(self):
        path = (
            Path(__file__).resolve().parents[2] / "alembic" / "versions" / "033_knowledge_base.py"
        )
        spec = importlib.util.spec_from_file_location("kb_migration_033", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.revision == "033_knowledge_base"
        assert module.down_revision == "032_review_rule_snapshot"


class TestKnowledgeBaseEndpoints:
    """端点层租户解析：服务端组织上下文，不接受请求体里的 organization_id。

    变异点：端点改用请求体/查询参数里的 org_id → 跨组织分支红。
    """

    @pytest.mark.asyncio
    async def test_create_uses_server_org_and_requires_admin(self, kb_db):
        user = SimpleNamespace(id=7, is_superuser=False, organization_id=1)
        async with kb_db() as session:
            with (
                patch.object(kb_endpoints, "require_org_admin", new=AsyncMock()) as admin_check,
                patch(
                    "app.services.audit_service.audit_service.log_activity",
                    new=AsyncMock(),
                ),
            ):
                created = await kb_endpoints.create_knowledge_base(
                    kb_endpoints.KnowledgeBaseCreate(name="New KB"),
                    current_user=user,
                    db=session,
                )
                admin_check.assert_awaited_once()
            assert created.organization_id == 1
            assert created.created_by == 7

    @pytest.mark.asyncio
    async def test_cross_org_get_raises_not_found(self, kb_db):
        async with kb_db() as session:
            with pytest.raises(NotFoundError):
                await kb_endpoints.get_knowledge_base(
                    "kb-org2", current_user=SimpleNamespace(id=7, organization_id=1), db=session
                )

    @pytest.mark.asyncio
    async def test_non_admin_write_rejected(self, kb_db):
        async with kb_db() as session:
            with (
                patch.object(
                    kb_endpoints,
                    "require_org_admin",
                    new=AsyncMock(side_effect=AuthorizationError("需要组织管理员权限")),
                ),
                pytest.raises(AuthorizationError),
            ):
                await kb_endpoints.delete_knowledge_base(
                    "kb-org1", current_user=SimpleNamespace(id=7, organization_id=1), db=session
                )
            # 未获授权时知识库仍在，未发生删除。
            assert await knowledge_base_service.get(session, 1, "kb-org1") is not None
