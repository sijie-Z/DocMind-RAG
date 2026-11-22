"""P2 知识库安全护栏的判别性补强测试（对抗式变异清单）。

每条用例对应一个具体变异，断言点写在用例 docstring 中：
- 过滤解析器的 documents.org 条件：跨组织同 kb_id 文档不得被召回；
- 上传端点的 KB 归属校验：跨组织/不存在的 kb_id 必须拒绝，合法同组织 id 必须放行；
- pipeline 精确/语义缓存门：KB 查询必须绕过 exact/semantic 缓存读写；
- service.delete 解绑 org 条件：org2 文档不得被 org1 删除操作解绑；
- 迁移 033 的静态结构与降级顺序；
- schema 空白 kb_id 不得变成「不限定」；
- search 端点必须把 kb_id 透传给 service；
- viewer_user_id=0 必须按「有 viewer」处理。
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from starlette.datastructures import UploadFile

import app.models  # noqa: F401 — register every table for create_all
from app.api.v1.endpoints import documents as documents_ep
from app.api.v1.endpoints import knowledge as knowledge_ep
from app.core.config import settings
from app.core.database import Base
from app.exceptions import ConflictError, NotFoundError
from app.models.document import (
    Document,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)
from app.models.knowledge_base import KnowledgeBase
from app.models.organization import Organization
from app.models.user import User
from app.rag.cache import _viewer_segment
from app.rag.knowledge_base_filter import resolve_knowledge_base_document_ids
from app.rag.pipeline import RAGPipeline
from app.rag.source_provenance import PARSER_ORIGINAL
from app.schemas.knowledge import KnowledgeSearchRequest
from app.services.knowledge_base_service import knowledge_base_service
from app.services.organization_service import organization_service

_BACKEND = Path(__file__).resolve().parents[2]


def _doc(doc_id: str, org_id: int, kb_id: str | None, *, title: str | None = None) -> Document:
    return Document(
        id=doc_id,
        filename=f"{doc_id}.txt",
        title=title or doc_id,
        file_path=f"{org_id}/{doc_id}.txt",
        file_size=1,
        file_type=DocumentType.TXT,
        status=DocumentStatus.INDEXED,
        organization_id=org_id,
        uploaded_by=7,
        knowledge_base_id=kb_id,
        visibility=DocumentVisibility.ORGANIZATION,
    )


@pytest.fixture
async def kb_db():
    """Org1 owns kb-org1; org2 has a doc that illegally references kb-org1."""
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                Organization(id=1, name="kb-hardening-org1", owner_id=7),
                Organization(id=2, name="kb-hardening-org2", owner_id=7),
                User(
                    id=7,
                    username="kb-uploader",
                    email="kb-uploader@example.invalid",
                    hashed_password="not-used",
                    organization_id=1,
                ),
                KnowledgeBase(id="kb-org1", organization_id=1, name="Org1 KB"),
                KnowledgeBase(id="kb-org2", organization_id=2, name="Org2 KB"),
                _doc("doc-org1", 1, "kb-org1"),
                _doc("doc-org1-null", 1, None),
                _doc("doc-org2", 2, "kb-org1"),
            ]
        )
        await session.commit()
    yield maker
    await engine.dispose()


class _MemoryExactCache:
    """Minimal shared exact-cache stand-in: keys are the observable scope."""

    def __init__(self):
        self.store: dict[tuple, list] = {}
        self.get_calls: list[tuple] = []
        self.set_calls: list[tuple] = []

    async def get(self, query, organization_id, top_k, **kwargs):
        key = (query, organization_id, top_k, kwargs.get("search_mode"))
        self.get_calls.append(key)
        return self.store.get(key)

    async def set(self, query, organization_id, top_k, result, **kwargs):
        key = (query, organization_id, top_k, kwargs.get("search_mode"))
        self.set_calls.append((key, [r.get("document_id") for r in result]))
        self.store[key] = result


def _approval_hit(doc_id: str) -> dict:
    """A verified original hit that survives the strict source-provenance gate."""
    return {
        "id": f"chunk-{doc_id}",
        "document_id": doc_id,
        "chunk_id": f"chunk-{doc_id}",
        "text": "policy original text",
        "original_text": "policy original text",
        "text_provenance": PARSER_ORIGINAL,
        "content": "policy original text",
        "score": 1.0,
        "metadata": {},
        "source": {},
    }


class TestResolveCrossOrgDocument:
    @pytest.mark.asyncio
    async def test_org2_document_in_org1_kb_is_excluded(self, kb_db):
        """Mutation: remove `Document.organization_id == organization_id` at
        knowledge_base_filter.py:48 -> org2 doc joins kb-org1 and appears;
        assertion `ids == ["doc-org1"]` turns red.
        """
        async with kb_db() as session:
            ids = await resolve_knowledge_base_document_ids(session, 1, "kb-org1")
            assert ids == ["doc-org1"]
            # Same KB from the other tenant's side: only its own doc, not org1's.
            ids2 = await resolve_knowledge_base_document_ids(session, 2, "kb-org1")
            assert ids2 == []  # KB belongs to org1, so tenant 2 cannot use it at all


class TestUploadKnowledgeBaseAssociation:
    @pytest.mark.asyncio
    async def test_foreign_and_unknown_kb_ids_rejected_valid_org_kb_accepted(
        self, kb_db, monkeypatch
    ):
        """Mutations at documents.py:238-244:
        - removing `knowledge_base_service.get(db, org_id, kb_id)` ->
          org2/unknown id reaches Document creation and a 201 is returned;
          the `pytest.raises(NotFoundError)` assertions turn red.
        - passing the wrong org (e.g. no org filter in service.get) ->
          `kb-org2` would be accepted for org1 and the first assertion turns red.
        Real endpoint function + real SQLite KB rows; only IO side effects stubbed.
        """
        monkeypatch.setattr(documents_ep.minio_client, "stat_object", MagicMock(return_value={}))
        monkeypatch.setattr(
            documents_ep.kafka_producer, "send_message", AsyncMock(return_value=True)
        )

        async def _upload(db, kb_id):
            return await documents_ep.upload_document(
                file=UploadFile(io.BytesIO(b"policy text\n"), filename="policy.txt"),
                title=None,
                description=None,
                tags=None,
                db=db,
                current_user=await db.merge(
                    User(
                        id=7,
                        username="kb-uploader",
                        email="kb-uploader@example.invalid",
                        hashed_password="not-used",
                        organization_id=1,
                    )
                ),
                knowledge_base_id=kb_id,
            )

        async with kb_db() as session:
            for bad_id in ("kb-org2", "kb-missing"):
                with pytest.raises(NotFoundError, match="知识库不存在"):
                    await _upload(session, bad_id)
            # No Document row may be left behind by the rejected uploads.
            rows = (await session.execute(select(Document))).scalars().all()
            assert {row.filename for row in rows} == {
                "doc-org1.txt",
                "doc-org1-null.txt",
                "doc-org2.txt",
            }

            result = await _upload(session, "kb-org1")
            assert result["success"] is True
            new_doc = (
                await session.execute(select(Document).where(Document.id == result["data"]["id"]))
            ).scalar_one()
            assert new_doc.organization_id == 1
            assert new_doc.knowledge_base_id == "kb-org1"


class TestExactCacheGateForKbScope:
    @pytest.mark.asyncio
    async def test_kb_scoped_narrowing_skips_exact_cache(self):
        """Mutation: remove `kb_scoped` from the read-side exact-cache gate
        (pipeline.py:312) -> cache.get is awaited and any org-wide entry is
        readable by a KB-scoped query; `cache.get.assert_not_awaited()` turns red.
        No viewer => acl_enabled False, isolating the KB part of the gate.
        """
        pipeline = RAGPipeline()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        pipeline.cache.get = AsyncMock(side_effect=AssertionError("exact cache must be skipped"))
        pipeline.cache.set = AsyncMock(side_effect=AssertionError("exact cache must be skipped"))
        seen: dict = {}

        async def _fake_resolve(db, org_id, kb_id):
            # Isolate the kb_scoped gate: document_ids stays None, so only
            # `kb_scoped` can keep this query out of the shared exact cache.
            seen["args"] = (org_id, kb_id)
            return

        async def _fake_retrieve(*args, **kwargs):
            seen["document_ids"] = args[3] if len(args) > 3 else kwargs.get("document_ids")
            # 非空且通过 provenance gate 的结果才会触达写缓存门控。
            return [_approval_hit("doc-kb")], [0.1, 0.2], {}, []

        pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)
        pipeline.semantic_cache = MagicMock()
        pipeline.semantic_cache.enabled = False
        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.rag.knowledge_base_filter.resolve_knowledge_base_document_ids",
                new=_fake_resolve,
            ),
        ):
            await pipeline.search_knowledge_base("q", 1, top_k=5, knowledge_base_id="kb-org1")

        assert seen["args"] == (1, "kb-org1")
        assert seen["document_ids"] is None
        pipeline.cache.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_kb_scoped_result_is_not_shared_with_unfiltered_or_other_kb(self, monkeypatch):
        """Mutations:
        - remove `kb_scoped` from the exact WRITE gate (pipeline.py:717) ->
          the KB-A result is stored under an org-wide key; with the read gate
          also removed, the unfiltered / KB-B callers get A's result;
        - remove `kb_scoped` from either READ gate (exact line 312 or semantic
          line 367) -> cache.get / semantic_cache.get is awaited for KB queries.
        Assertions on `cache.get_calls`, `cache.set_calls`, `retrieval_calls`
        and the per-scope document ids all discriminate the mutations.
        """
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", False)
        pipeline = RAGPipeline()
        pipeline.cache = _MemoryExactCache()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        monkeypatch.setattr(pipeline.semantic_cache, "_namespace", "chat")

        retrieval_calls = 0
        scope_seen: list = []

        async def _fake_retrieve(*args, **kwargs):
            nonlocal retrieval_calls
            retrieval_calls += 1
            scope_seen.append(args[3] if len(args) > 3 else kwargs.get("document_ids"))
            ids = args[3] if len(args) > 3 else kwargs.get("document_ids")
            doc_id = "doc-org2" if ids == ["doc-org2"] else "doc-org1"
            return [_approval_hit(doc_id)], None, {}, []

        pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)

        async def _resolve(db, org_id, kb_id):
            return ["doc-org1"] if kb_id == "kb-org1" else ["doc-org2"]

        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.rag.knowledge_base_filter.resolve_knowledge_base_document_ids",
                new=_resolve,
            ),
        ):
            result_a, _ = await pipeline.search_knowledge_base(
                "shared q", 1, top_k=5, knowledge_base_id="kb-org1"
            )
            result_b, _ = await pipeline.search_knowledge_base(
                "shared q", 1, top_k=5, knowledge_base_id="kb-org2"
            )
            # The unfiltered call may cache its own result; KB calls must not.
            assert pipeline.cache.set_calls == []
            # KB-A/KB-B must never touch the shared cache; snapshot before the
            # unfiltered call, which is allowed to use it.
            get_calls_at_kb_b = list(pipeline.cache.get_calls)
            set_calls_at_kb_b = list(pipeline.cache.set_calls)
            result_all, _ = await pipeline.search_knowledge_base("shared q", 1, top_k=5)

        assert [r["document_id"] for r in result_a] == ["doc-org1"]
        assert [r["document_id"] for r in result_all] == ["doc-org1"]
        assert [r["document_id"] for r in result_b] == ["doc-org2"]
        assert retrieval_calls == 3
        assert scope_seen == [["doc-org1"], ["doc-org2"], None]
        # Only the final unfiltered call may touch the exact cache.
        assert get_calls_at_kb_b == []
        assert set_calls_at_kb_b == []

    @pytest.mark.asyncio
    async def test_kb_scoped_skips_semantic_cache_read_and_write(self, monkeypatch):
        """Mutation: remove `kb_scoped` from the semantic read gate
        (pipeline.py:367) or the semantic write gate (pipeline.py:750) ->
        semantic_cache.get / set is awaited; assertions turn red.
        A MagicMock semantic cache exposes `enabled=True` so the namespace is
        active, and skips on document_ids/kb_scoped must be explicit.
        """
        monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", False)
        pipeline = RAGPipeline()
        pipeline.cache = _MemoryExactCache()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        pipeline.semantic_cache = MagicMock()
        pipeline.semantic_cache.get = AsyncMock(return_value=None)
        pipeline.semantic_cache.set = AsyncMock()
        pipeline.semantic_cache.enabled = True
        # If the kb_scoped read gate is removed, the pipeline computes a vector
        # and reaches semantic_cache.get; without this stub that path would fail
        # on a real embedding call instead of deterministically hitting the spy.
        pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])

        async def _fake_retrieve(*args, **kwargs):
            return [_approval_hit("doc-org1")], [0.1, 0.2], {}, []

        pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)
        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.rag.knowledge_base_filter.resolve_knowledge_base_document_ids",
                # Keep document_ids None: only kb_scoped can skip the cache.
                new=AsyncMock(return_value=None),
            ),
        ):
            result, _ = await pipeline.search_knowledge_base(
                "semantic q", 1, top_k=5, knowledge_base_id="kb-org1"
            )

        assert [r["document_id"] for r in result] == ["doc-org1"]
        pipeline.semantic_cache.get.assert_not_awaited()
        pipeline.semantic_cache.set.assert_not_awaited()
        assert pipeline.cache.get_calls == []
        assert pipeline.cache.set_calls == []


class TestOrganizationDeletionGuardsKnowledgeBases:
    @pytest.mark.asyncio
    async def test_kb_only_org_delete_raises_conflict_with_kb_count(self):
        """Mutation: remove ("知识库", KnowledgeBase) from
        organization_service._ATTACHED_DATA_MODELS -> the delete proceeds past
        the attachment check and no ConflictError is raised; assertion turns red.
        The message must name "知识库 1 条" so operators know what to migrate.
        """
        engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                session.add_all(
                    [
                        Organization(id=1, name="kb-only-org", owner_id=7),
                        KnowledgeBase(id="kb-only", organization_id=1, name="Only KB"),
                    ]
                )
                await session.commit()
                with pytest.raises(ConflictError, match="知识库 1 条"):
                    await organization_service.delete_organization_thoroughly(session, 1)
                assert await session.get(Organization, 1) is not None
        finally:
            await engine.dispose()


class TestDeleteUnlinkCrossOrg:
    @pytest.mark.asyncio
    async def test_delete_does_not_unlink_org2_document_referencing_org1_kb(self, kb_db):
        """Mutation: remove `Document.organization_id == organization_id` from
        service.delete's UPDATE at line 161 -> doc-org2.knowledge_base_id becomes
        None; the assertion `== "kb-org1"` turns red.
        """
        async with kb_db() as session:
            await knowledge_base_service.delete(session, 1, "kb-org1")
            by_id = {
                row.id: row for row in (await session.execute(select(Document))).scalars().all()
            }
            assert by_id["doc-org1"].knowledge_base_id is None
            assert by_id["doc-org2"].knowledge_base_id == "kb-org1"
            assert by_id["doc-org1-null"].knowledge_base_id is None
            with pytest.raises(NotFoundError):
                await knowledge_base_service.get(session, 1, "kb-org1")


class TestMigration033StaticShape:
    @classmethod
    def _module(cls):
        path = _BACKEND / "alembic" / "versions" / "033_knowledge_base.py"
        spec = importlib.util.spec_from_file_location("kb033_hardening", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module, path.read_text(encoding="utf-8")

    def test_revision_chain_and_object_names(self):
        module, source = self._module()
        assert module.revision == "033_knowledge_base"
        assert module.down_revision == "032_review_rule_snapshot"
        assert module.TABLE == "knowledge_bases"
        assert module.DOC_TABLE == "documents"
        assert module.COLUMN == "knowledge_base_id"
        assert module.INDEX == "ix_documents_knowledge_base_id"
        assert module.FK == "fk_documents_knowledge_base_id_knowledge_bases"

    def test_kb_org_fk_and_nullable_document_column(self):
        module, source = self._module()
        assert 'sa.ForeignKey("organizations.id")' in source
        assert '"created_by",' in source and 'sa.ForeignKey("users.id")' in source
        # Both real DDL and ORM metadata must keep the column nullable: NULL is
        # the legacy "all documents in org" semantics, never an implicit KB.
        assert module.upgrade is not None
        assert "nullable=True" in source
        from app.models.document import Document as DocumentModel

        assert DocumentModel.__table__.c.knowledge_base_id.nullable is True

    def test_doc_kb_index_and_mysql_only_fk(self):
        _, source = self._module()
        assert "op.create_index(INDEX, DOC_TABLE, [COLUMN])" in source
        assert "if not _is_sqlite() and FK not in _foreign_keys(DOC_TABLE):" in source
        assert 'op.create_foreign_key(FK, DOC_TABLE, TABLE, [COLUMN], ["id"])' in source

    def test_orm_kb_index_matches_migration(self):
        """create_all 与 alembic 路径的 KB 索引必须同形。

        变异点：把 KnowledgeBase.organization_id 改回 index=True（生成
        ix_knowledge_bases_organization_id）或删掉复合 Index，本用例红。
        """
        module, source = self._module()
        from app.models.knowledge_base import KnowledgeBase

        orm_indexes = {
            idx.name: tuple(col.name for col in idx.columns)
            for idx in KnowledgeBase.__table__.indexes
        }
        assert orm_indexes[module.KB_ORG_INDEX] == ("organization_id", "created_at")
        assert "ix_knowledge_bases_organization_id" not in orm_indexes
        assert 'op.create_index(KB_ORG_INDEX, TABLE, ["organization_id", "created_at"])' in source

    def test_downgrade_drops_doc_objects_before_kb_table(self):
        """Static order assertion: SQLite cannot ALTER DROP FK, but the
        downgrade must still drop constraint -> index -> column -> KB index ->
        KB table in that order on MySQL. Mutation = moving `op.drop_table(TABLE)`
        above the document-column block -> red.
        """
        _, source = self._module()
        downgrade = source.split("def downgrade() -> None:", 1)[1]
        positions = [
            downgrade.index("op.drop_constraint(FK, DOC_TABLE"),
            downgrade.index("op.drop_index(INDEX, table_name=DOC_TABLE)"),
            downgrade.index("op.drop_column(DOC_TABLE, COLUMN)"),
            downgrade.index("op.drop_index(KB_ORG_INDEX, table_name=TABLE)"),
            downgrade.index("op.drop_table(TABLE)"),
        ]
        assert positions == sorted(positions)


class TestSchemaBlankKnowledgeBaseId:
    @pytest.mark.parametrize("bad", ["", "   ", "\t\n"])
    def test_blank_kb_id_rejected_not_coerced_to_none(self, bad):
        """Mutation: delete the validator at knowledge.py:74-80 -> blank string
        becomes the stripped '' and `pytest.raises(ValidationError)` turns red;
        without stripping, '' would silently disable the KB filter.
        """
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            KnowledgeSearchRequest(query="q", organization_id=1, knowledge_base_id=bad)

    def test_whitespace_padded_id_is_trimmed_not_blanked(self):
        req = KnowledgeSearchRequest(query="q", organization_id=1, knowledge_base_id="  kb-1  ")
        assert req.knowledge_base_id == "kb-1"


class TestSearchEndpointKbPropagation:
    @pytest.mark.asyncio
    async def test_search_passes_kb_id_to_service(self, monkeypatch):
        """Mutation: drop `knowledge_base_id=request.knowledge_base_id` at
        knowledge.py:321 -> spy receives None; `assert kwargs[...]` turns red.
        Direct endpoint call with a service spy, no external services.
        """
        seen: dict = {}

        async def _spy(**kwargs):
            seen.update(kwargs)
            return [], None

        monkeypatch.setattr(
            knowledge_ep,
            "knowledge_service",
            SimpleNamespace(search_knowledge_with_meta=AsyncMock(side_effect=_spy)),
        )
        monkeypatch.setattr(
            knowledge_ep.organization_settings_service,
            "resolve_retrieval_top_k",
            AsyncMock(return_value=7),
        )
        monkeypatch.setattr(knowledge_ep, "require_organization", AsyncMock(return_value=1))
        monkeypatch.setattr(
            knowledge_ep, "_diagnose_empty_results", AsyncMock(return_value=("ok", 1))
        )

        request = KnowledgeSearchRequest(
            query="q",
            organization_id=1,
            knowledge_base_id="kb-org1",
            top_k=5,
        )
        await knowledge_ep.search_knowledge(
            request=request,
            current_user=SimpleNamespace(id=42, organization_id=1, is_superuser=False),
            db=MagicMock(),
        )

        assert seen["knowledge_base_id"] == "kb-org1"
        assert seen["viewer_user_id"] == 42
        assert seen["organization_id"] == 1
        assert seen["top_k"] == 5


class TestViewerZeroWithKbFilter:
    @pytest.mark.asyncio
    async def test_viewer_zero_remains_scoped_with_kb_filter(self, kb_db, monkeypatch):
        """Mutation A: `acl_enabled = bool(viewer_user_id)` -> 0 treated as no
        viewer, `get_blocked_document_ids` never called; assertion on blocked
        turns red. Mutation B: cache gate omits `not acl_enabled` -> cache.get
        called with viewer=0; `assert_not_awaited` turns red.
        """
        assert _viewer_segment(0, "r1") != _viewer_segment(None, "r1")

        pipeline = RAGPipeline()
        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        blocked_calls: list = []

        async def _fake_blocked(db, org_id, viewer_id):
            blocked_calls.append((org_id, viewer_id))
            return []

        pipeline.cache.get = AsyncMock(side_effect=AssertionError("ACL-scoped must skip cache"))
        seen: dict = {}

        async def _fake_retrieve(*args, **kwargs):
            seen.update(kwargs)
            seen["document_ids"] = args[3] if len(args) > 3 else kwargs.get("document_ids")
            return [], [], None, []

        pipeline.retriever.retrieve = AsyncMock(side_effect=_fake_retrieve)
        with (
            patch("app.core.database.AsyncSessionLocal", return_value=session),
            patch(
                "app.services.document_acl.get_blocked_document_ids",
                new=_fake_blocked,
            ),
            patch(
                "app.rag.knowledge_base_filter.resolve_knowledge_base_document_ids",
                new=AsyncMock(return_value=["doc-org1"]),
            ),
        ):
            await pipeline.search_knowledge_base(
                "q", 1, top_k=5, knowledge_base_id="kb-org1", viewer_user_id=0
            )

        assert blocked_calls == [(1, 0)]
        assert seen["blocked_document_ids"] == []
        assert seen["document_ids"] == ["doc-org1"]
        pipeline.cache.get.assert_not_awaited()
