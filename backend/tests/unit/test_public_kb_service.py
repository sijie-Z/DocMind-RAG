"""公开知识库服务（批次十三）判别性测试。

变异点（逐条对应断言）：
- 白名单查询漏掉 organization_id / knowledge_base_id / status / is_enabled
  任一条件 → 隔离用例红；
- 公开检索去掉结果白名单复查 → 非白名单命中进入响应（红）；
- 空白名单回退到 KB 全量/组织检索 → 空集合用例红（假 pipeline 未被调用）；
- token 明文落库 / 状态回显 token → token 用例红；
- 撤销/过期/禁用检查缺失 → 404 同形用例红；
- snippet 不去 HTML / 不截断 → 片段用例红；
- set_documents 部分写入 → 非法目标用例红（白名单保持原状）。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.exceptions import NotFoundError, ValidationError
from app.models.document import Document, DocumentStatus, DocumentType, DocumentVisibility
from app.models.knowledge_base import KnowledgeBase
from app.models.organization import Organization
from app.models.public_kb import DocumentPublication, KbPublication
from app.services import public_kb_service as service

ORG = 1
OTHER_ORG = 2
USER = 1
KB = "kb-main"
OTHER_KB = "kb-other"


def _doc(
    doc_id: str,
    *,
    org_id: int = ORG,
    kb_id: str | None = KB,
    status: DocumentStatus = DocumentStatus.INDEXED,
    enabled: bool = True,
    title: str | None = None,
    visibility: DocumentVisibility = DocumentVisibility.ORGANIZATION,
) -> Document:
    return Document(
        id=doc_id,
        filename=f"{doc_id}.txt",
        file_path=f"{org_id}/{doc_id}.txt",
        file_size=1,
        file_type=DocumentType.TXT,
        status=status,
        organization_id=org_id,
        uploaded_by=USER,
        knowledge_base_id=kb_id,
        title=title,
        is_enabled=enabled,
        visibility=visibility,
    )


@pytest.fixture
async def db_factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Org", owner_id=USER),
                Organization(id=OTHER_ORG, name="Other", owner_id=USER),
                KnowledgeBase(id=KB, organization_id=ORG, name="Main KB"),
                KnowledgeBase(id=OTHER_KB, organization_id=ORG, name="Other KB"),
                _doc("doc-a", title="制度 A"),
                _doc("doc-b", title="制度 B"),
                _doc("doc-other-kb", kb_id=OTHER_KB),
                _doc("doc-org2", org_id=OTHER_ORG),
                _doc("doc-parsed", status=DocumentStatus.PARSED),
                _doc("doc-disabled", enabled=False),
                _doc("doc-private", visibility=DocumentVisibility.PRIVATE),
            ]
        )
        await db.commit()
    yield maker
    await engine.dispose()


async def _create(db, **overrides):
    payload = {
        "organization_id": ORG,
        "knowledge_base_id": KB,
        "title": "公开制度库",
        "description": "仅公开白名单文档",
        "expires_in_days": None,
        "created_by": USER,
    }
    payload.update(overrides)
    return await service.create_publication(db, **payload)


async def test_create_stores_only_token_digest_and_returns_token_once(db_factory):
    async with db_factory() as db:
        publication, token = await _create(db, document_ids=["doc-a", "doc-b"])
        await db.commit()

        assert token and len(token) >= 20
        row = (
            await db.execute(select(KbPublication).where(KbPublication.id == publication.id))
        ).scalar_one()
        assert row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
        # 明文不得出现在任何持久列。
        serialized = " ".join(
            str(getattr(row, column.name)) for column in KbPublication.__table__.columns
        )
        assert token not in serialized

        view = service.publication_view(row, knowledge_base_name="Main KB", document_count=2)
        assert "token" not in view and "token_hash" not in view
        listed = await service.list_publications(db, organization_id=ORG)
        assert len(listed) == 1 and "token" not in listed[0]


async def test_create_validates_kb_title_description_expiry(db_factory):
    async with db_factory() as db:
        with pytest.raises(NotFoundError):
            await _create(db, knowledge_base_id="kb-missing")
        with pytest.raises(NotFoundError):
            await _create(db, organization_id=OTHER_ORG, knowledge_base_id=KB)
        with pytest.raises(ValidationError):
            await _create(db, title="   ")
        with pytest.raises(ValidationError):
            await _create(db, title="x" * 101)
        with pytest.raises(ValidationError):
            await _create(db, description="x" * 501)
        with pytest.raises(ValidationError):
            await _create(db, expires_in_days=0)
        with pytest.raises(ValidationError):
            await _create(db, expires_in_days=91)

        publication, _token = await _create(
            db, description="<b>纯文本</b>\n描述", expires_in_days=7
        )
        assert publication.description == "纯文本 描述"
        assert publication.expires_at is not None


async def test_whitelist_isolation_mutation_points(db_factory):
    """同 KB 未发布 / 跨组织 / 非 INDEXED / 停用 / 已删除文档绝不进入检索集合。"""
    async with db_factory() as db:
        publication, _token = await _create(db, document_ids=["doc-a"])
        await db.commit()

        whitelist = await service._searchable_whitelist(db, publication)
        assert set(whitelist) == {"doc-a"}

        # 直接写入越权白名单行（模拟历史脏数据/绕过服务写库）也必须被读侧条件挡住：
        # 跨组织、跨知识库、非 INDEXED、停用各一条。
        for doc_id in ("doc-org2", "doc-other-kb", "doc-parsed", "doc-disabled", "doc-private"):
            db.add(
                DocumentPublication(
                    id=f"dp-{doc_id}",
                    organization_id=ORG,
                    publication_id=publication.id,
                    document_id=doc_id,
                    created_by=USER,
                    created_at=datetime.now(UTC),
                )
            )
        await db.commit()

        whitelist = await service._searchable_whitelist(db, publication)
        assert set(whitelist) == {"doc-a"}, (
            "白名单读侧必须同时校验 org/knowledge_base/INDEXED/is_enabled"
        )

        # 文档被删除后（白名单行仍可能存在），检索集合必须为空。
        await db.execute(delete(Document).where(Document.id == "doc-a"))
        await db.commit()
        assert await service._searchable_whitelist(db, publication) == {}


async def test_set_documents_modes_and_atomic_validation(db_factory):
    async with db_factory() as db:
        publication, _token = await _create(db, document_ids=["doc-a"])
        await db.commit()

        added = await service.set_documents(
            db,
            publication_id=publication.id,
            organization_id=ORG,
            document_ids=["doc-b", "doc-b"],
            mode="add",
            operator_id=USER,
        )
        assert added == ["doc-a", "doc-b"]

        removed = await service.set_documents(
            db,
            publication_id=publication.id,
            organization_id=ORG,
            document_ids=["doc-a"],
            mode="remove",
            operator_id=USER,
        )
        assert removed == ["doc-b"]

        replaced = await service.set_documents(
            db,
            publication_id=publication.id,
            organization_id=ORG,
            document_ids=["doc-b", "doc-a"],
            mode="replace",
            operator_id=USER,
        )
        assert replaced == ["doc-a", "doc-b"]

        # 任一非法目标 → 整体拒绝，原白名单不变（不允许部分写入）。
        with pytest.raises(NotFoundError):
            await service.set_documents(
                db,
                publication_id=publication.id,
                organization_id=ORG,
                document_ids=["doc-a", "doc-org2"],
                mode="replace",
                operator_id=USER,
            )
        current = await service.list_documents(
            db, publication_id=publication.id, organization_id=ORG
        )
        assert {row["id"] for row in current} == {"doc-a", "doc-b"}

        with pytest.raises(NotFoundError):
            await service.set_documents(
                db,
                publication_id=publication.id,
                organization_id=ORG,
                document_ids=["doc-other-kb"],
                mode="add",
                operator_id=USER,
            )
        with pytest.raises(NotFoundError):
            await service.set_documents(
                db,
                publication_id=publication.id,
                organization_id=ORG,
                document_ids=["doc-parsed"],
                mode="add",
                operator_id=USER,
            )
        with pytest.raises(NotFoundError):
            await service.set_documents(
                db,
                publication_id=publication.id,
                organization_id=ORG,
                document_ids=["doc-private"],
                mode="add",
                operator_id=USER,
            )
        with pytest.raises(ValidationError):
            await service.set_documents(
                db,
                publication_id=publication.id,
                organization_id=ORG,
                document_ids=[f"doc-{i}" for i in range(201)],
                mode="replace",
                operator_id=USER,
            )


async def test_rotate_invalidates_old_token_and_does_not_revive(db_factory):
    async with db_factory() as db:
        publication, old_token = await _create(db)
        await db.commit()
        assert (await service.resolve_public(db, old_token)).id == publication.id

        rotated, new_token = await service.rotate_publication(
            db, publication_id=publication.id, organization_id=ORG, operator_id=USER
        )
        await db.commit()
        assert new_token != old_token
        with pytest.raises(NotFoundError):
            await service.resolve_public(db, old_token)
        assert (await service.resolve_public(db, new_token)).id == publication.id

        # 轮换不复活已撤销发布。
        await service.revoke_publication(
            db, publication_id=publication.id, organization_id=ORG, operator_id=USER
        )
        await db.commit()
        rotated2, token2 = await service.rotate_publication(
            db, publication_id=publication.id, organization_id=ORG, operator_id=USER
        )
        await db.commit()
        assert rotated2.revoked_at is not None
        with pytest.raises(NotFoundError):
            await service.resolve_public(db, token2)


async def test_revoked_expired_disabled_missing_share_uniform_404(db_factory):
    async with db_factory() as db:
        publication, token = await _create(db)
        await db.commit()
        await service.revoke_publication(
            db, publication_id=publication.id, organization_id=ORG, operator_id=USER
        )
        await db.commit()
        with pytest.raises(NotFoundError) as revoked_exc:
            await service.resolve_public(db, token)

        publication.revoked_at = None
        publication.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()
        with pytest.raises(NotFoundError) as expired_exc:
            await service.resolve_public(db, token)

        publication.expires_at = None
        publication.enabled = False
        await db.commit()
        with pytest.raises(NotFoundError) as disabled_exc:
            await service.resolve_public(db, token)

        with pytest.raises(NotFoundError) as missing_exc:
            await service.resolve_public(db, "never-existed-token")
        with pytest.raises(NotFoundError) as long_exc:
            await service.resolve_public(db, "x" * 200)

        messages = {
            revoked_exc.value.message,
            expired_exc.value.message,
            disabled_exc.value.message,
            missing_exc.value.message,
            long_exc.value.message,
        }
        assert messages == {service.PUBLICATION_INVALID}


async def test_public_search_filters_whitelist_and_hides_internal_fields(db_factory, monkeypatch):
    calls: dict = {}

    class _FakePipeline:
        async def search_knowledge_base(
            self, query, organization_id, top_k=5, document_ids=None, viewer_user_id=None, **kwargs
        ):
            calls.update(
                query=query,
                organization_id=organization_id,
                top_k=top_k,
                document_ids=document_ids,
                viewer_user_id=viewer_user_id,
                search_mode=kwargs.get("search_mode"),
            )
            return (
                [
                    {
                        "document_id": "doc-a",
                        "chunk_id": "chunk-a-1",
                        "snippet": "<p>制度 <b>A</b> 的第 1 条</p>",
                        "score": 0.91,
                        "file_path": "1/secret.txt",
                        "organization_id": ORG,
                        "uploaded_by": USER,
                    },
                    {
                        "document_id": "doc-b",
                        "snippet": "未发布文档不得出现",
                        "score": 0.88,
                    },
                    {
                        "document_id": "doc-org2",
                        "snippet": "跨组织文档不得出现",
                        "score": 0.87,
                    },
                    {
                        "_source": {"doc_id": "doc-a", "content": "x" * 500},
                        "score": 0.5,
                    },
                ],
                None,
            )

    monkeypatch.setattr(service, "get_rag_pipeline", lambda: _FakePipeline())

    async with db_factory() as db:
        publication, _token = await _create(db, document_ids=["doc-a"])
        await db.commit()

        results = await service.public_search(
            db, publication=publication, query="  制度  ", top_k=5
        )
        assert calls["query"] == "制度"
        assert calls["organization_id"] == ORG
        assert calls["document_ids"] == ["doc-a"]
        assert calls["viewer_user_id"] is None
        assert calls["search_mode"] == "hybrid"

        # 2 条 doc-a 命中：第 1 条按 score，第 4 条按 _source.doc_id 识别。
        assert [row["rank"] for row in results] == [1, 2]
        assert [row["title"] for row in results] == ["制度 A", "制度 A"]
        assert results[0]["snippet"] == "制度 A 的第 1 条"
        assert len(results[1]["snippet"]) == service.SNIPPET_MAX_CHARS
        assert set(results[0]) == {"rank", "title", "snippet", "score"}
        body = str(results)
        for forbidden in ("doc-a", "chunk-a-1", "secret.txt", "uploaded_by"):
            assert forbidden not in body, f"公开响应泄露内部字段: {forbidden}"


async def test_public_search_empty_whitelist_never_calls_pipeline(db_factory, monkeypatch):
    def _must_not_call():
        raise AssertionError("空白名单绝不能回退到 KB 全量/组织级检索")

    monkeypatch.setattr(service, "get_rag_pipeline", _must_not_call)

    async with db_factory() as db:
        publication, _token = await _create(db)
        # 直接插入一条不可检索的白名单行（非 INDEXED）——读侧必须收窄为空。
        db.add(
            DocumentPublication(
                id="dp-parsed",
                organization_id=ORG,
                publication_id=publication.id,
                document_id="doc-parsed",
                created_by=USER,
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()
        assert await service.public_search(db, publication=publication, query="制度") == []


async def test_public_search_input_bounds(db_factory):
    async with db_factory() as db:
        publication, _token = await _create(db)
        await db.commit()
        for query, top_k in (("", 5), ("   ", 5), ("x" * 201, 5), ("ok", 0), ("ok", 11)):
            with pytest.raises(ValidationError):
                await service.public_search(db, publication=publication, query=query, top_k=top_k)


async def test_list_candidates_and_list_documents_searchable_flags(db_factory):
    async with db_factory() as db:
        publication, _token = await _create(db, document_ids=["doc-a"])
        # 停用文档不允许经服务加入白名单；这里直接写入「历史脏行」，
        # 管理面列表必须如实标注 searchable=False。
        for doc_id, dp_id in (("doc-disabled", "dp-disabled"), ("doc-private", "dp-private")):
            db.add(
                DocumentPublication(
                    id=dp_id,
                    organization_id=ORG,
                    publication_id=publication.id,
                    document_id=doc_id,
                    created_by=USER,
                    created_at=datetime.now(UTC),
                )
            )
        await db.commit()

        candidates = await service.list_candidates(db, organization_id=ORG, knowledge_base_id=KB)
        ids = {row["id"] for row in candidates["documents"]}
        assert ids == {"doc-a", "doc-b"}
        assert "doc-private" not in ids, "私有文档不得出现在公开候选里"
        assert candidates["truncated"] is False

        with pytest.raises(NotFoundError):
            await service.list_candidates(db, organization_id=ORG, knowledge_base_id="kb-missing")

        documents = await service.list_documents(
            db, publication_id=publication.id, organization_id=ORG
        )
        by_id = {row["id"]: row for row in documents}
        assert set(by_id) == {"doc-a", "doc-disabled", "doc-private"}
        assert by_id["doc-a"]["searchable"] is True
        assert by_id["doc-disabled"]["searchable"] is False
        assert by_id["doc-private"]["searchable"] is False, "私有文档在管理面也不得标为可检索"

        # 不同组织读同一发布：404 同形，不回显存在性。
        with pytest.raises(NotFoundError):
            await service.get_publication(
                db, publication_id=publication.id, organization_id=OTHER_ORG
            )


async def test_kb_delete_removes_publications_and_whitelist(db_factory):
    """知识库删除必须连带清理发布/白名单行（悬空公开页不得继续存在）。"""
    from app.services.knowledge_base_service import knowledge_base_service

    async with db_factory() as db:
        publication, token = await _create(db, document_ids=["doc-a"])
        await db.commit()
        await knowledge_base_service.delete(db, ORG, KB)

        publications = (
            await db.execute(
                select(func.count())
                .select_from(KbPublication)
                .where(KbPublication.id == publication.id)
            )
        ).scalar()
        whitelist = (
            await db.execute(
                select(func.count())
                .select_from(DocumentPublication)
                .where(DocumentPublication.publication_id == publication.id)
            )
        ).scalar()
        assert publications == 0
        assert whitelist == 0
        document = await db.get(Document, "doc-a")
        assert document is not None and document.knowledge_base_id is None
        with pytest.raises(NotFoundError):
            await service.resolve_public(db, token)


async def test_org_delete_is_blocked_by_public_kb_attached_data(db_factory):
    """组织删除保护：两张新表的挂载数据必须触发 409 文案（而非 MySQL 1451）。"""
    from app.exceptions import ConflictError
    from app.services.organization_service import organization_service

    async with db_factory() as db:
        await _create(db, document_ids=["doc-a"])
        await db.commit()
        with pytest.raises(ConflictError) as exc:
            await organization_service.delete_organization_thoroughly(db, ORG)
        message = str(exc.value)
        assert "公开知识库发布 1 条" in message
        assert "公开知识库文档 1 条" in message
