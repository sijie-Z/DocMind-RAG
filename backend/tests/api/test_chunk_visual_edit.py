"""Chunk 浏览/人工修正的判别性回归（批次四）。

每一组用例都对应一个可注入变异，变异后必须精确变红：

1. 把 ``browse_chunks`` 里的 ``get_document_for_user`` 换成裸 DB 查询 → 跨组织/
   私有文档越权用例红；
2. 把 upsert 写成 ``chunk.chunk_text = text`` 或改 ``original_text`` /
   ``meta_data`` / ``ReviewEvidence.quote_sha256`` → 不可变证据用例红；
3. 去掉 ``retrieval_text_provenance=manual_override`` 或把它写进 ``text_provenance``
   → manual provenance 用例红；
4. 删掉 ``build_retrieval_filters`` 的 chunk 级 must_not → 禁用退出召回用例红；
5. 审计 detail 拼接正文 → 私有正文泄漏用例红；
6. 放宽 page_size 上限 → 分页边界用例红。
"""

from __future__ import annotations

import hashlib
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select as sa_select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 注册全部表
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.exceptions import AuthorizationError, NotFoundError
from app.models.document import (
    Document,
    DocumentAcl,
    DocumentChunk,
    DocumentChunkOverride,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)
from app.models.organization import Organization
from app.models.review import ReviewCase, ReviewEvidence, ReviewItem
from app.models.user import User
from app.rag.retriever import build_retrieval_filters
from app.rag.source_provenance import COMPAT, LEGACY_UNVERIFIED, PARSER_ORIGINAL, STRICT
from app.rag.source_provenance import normalize_result_for_evidence as normalize_evidence
from app.services import chunk_override_service as svc

ORG = 11
OTHER_ORG = 12
OWNER = 101
GRANTED = 102
STRANGER = 103
DOC = "chunk-edit-doc"
PRIVATE_DOC = "chunk-edit-private"
FOREIGN_DOC = "chunk-edit-foreign"
SECRET = "第一条 这是私有合同正文 SECRET-CANARY-9821"
ORIGINAL = "第一条 付款期限为30天。"
INDEXED = "【文档背景】第一条 付款期限为30天。"


def _user(user_id: int, org_id: int | None = ORG, *, superuser: bool = False) -> User:
    return User(
        id=user_id,
        username=f"u{user_id}",
        email=f"u{user_id}@example.invalid",
        hashed_password="x",
        is_active=True,
        organization_id=org_id,
        is_superuser=superuser,
    )


def _doc(doc_id: str, org_id: int, *, visibility=DocumentVisibility.ORGANIZATION, owner=OWNER):
    return Document(
        id=doc_id,
        filename=f"{doc_id}.pdf",
        title=doc_id,
        file_path=f"{org_id}/{doc_id}.pdf",
        file_size=100,
        file_type=DocumentType.PDF,
        status=DocumentStatus.INDEXED,
        organization_id=org_id,
        uploaded_by=owner,
        visibility=visibility,
    )


def _chunk(chunk_id: str, doc_id: str, index: int, *, original: str | None, indexed: str):
    return DocumentChunk(
        id=chunk_id,
        document_id=doc_id,
        chunk_index=index,
        chunk_text=indexed,
        original_text=original,
        chunk_length=len(indexed),
        page_number=index + 1,
        start_pos=0,
        end_pos=len(original or indexed),
        meta_data={
            "text_provenance": PARSER_ORIGINAL if original else LEGACY_UNVERIFIED,
            "clause_no": [1] if original else [],
            "section_no": ["2.3"] if original else [],
            "section_title": "付款",
        },
    )


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'chunk-edit.db'}",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    chunk_ids = [f"chunk-doc-{i}" for i in range(3)]
    private_chunk = "chunk-private-0"
    async with maker() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Org A", owner_id=OWNER),
                Organization(id=OTHER_ORG, name="Org B", owner_id=STRANGER),
                _user(OWNER, ORG),
                _user(GRANTED, ORG),
                _user(STRANGER, OTHER_ORG),
                _doc(DOC, ORG),
                _doc(PRIVATE_DOC, ORG, visibility=DocumentVisibility.PRIVATE),
                _doc(FOREIGN_DOC, OTHER_ORG),
                DocumentAcl(
                    id=str(uuid.uuid4()),
                    document_id=PRIVATE_DOC,
                    subject_type="user",
                    subject_id=GRANTED,
                ),
                *[
                    _chunk(cid, DOC, i, original=ORIGINAL, indexed=INDEXED)
                    for i, cid in enumerate(chunk_ids)
                ],
                _chunk(private_chunk, PRIVATE_DOC, 0, original=SECRET, indexed=SECRET),
                _chunk("chunk-foreign-0", FOREIGN_DOC, 0, original="外部正文", indexed="外部正文"),
                ReviewCase(
                    id="case-1",
                    organization_id=ORG,
                    title="case",
                    policy_document_id=DOC,
                    base_document_id=DOC,
                    revised_document_id=DOC,
                    created_by=OWNER,
                ),
            ]
        )
        await db.flush()
        db.add(ReviewItem(id="item-1", case_id="case-1", item_key="payment_term", ordinal=0))
        await db.flush()
        db.add(
            ReviewEvidence(
                id="evidence-1",
                case_id="case-1",
                item_id="item-1",
                side="base",
                document_id=PRIVATE_DOC,
                doc_role="base",
                document_sha256="d" * 64,
                chunk_id_at_capture=private_chunk,
                chunk_index=0,
                quote_text=SECRET,
                quote_sha256=hashlib.sha256(SECRET.encode("utf-8")).hexdigest(),
            )
        )
        await db.commit()
    yield maker, chunk_ids
    await engine.dispose()


def _patch_external():
    return (
        patch(
            "app.core.elasticsearch.ElasticsearchTools.update_document",
            new_callable=AsyncMock,
        ),
        patch("app.rag.cache.clear_all_for_org", new_callable=AsyncMock),
        patch(
            "app.services.audit_service.audit_service.log_activity",
            new_callable=AsyncMock,
        ),
    )


@pytest.mark.asyncio
async def test_browser_pagination_and_server_derived_context(factory):
    maker, chunk_ids = factory
    async with maker() as db:
        result = await svc.browse_chunks(db, _user(OWNER), DOC, page=2, page_size=2, query=None)
    data = result["data"]
    assert data["total"] == 3
    assert data["pages"] == 2
    assert [row["chunk_index"] for row in data["items"]] == [2]
    row = data["items"][0]
    assert row["document_id"] == DOC
    assert row["document_title"] == DOC
    assert row["indexed_text"] == INDEXED
    assert row["parser_original_available"] is True
    assert row["parser_original_text"] == ORIGINAL
    assert row["page_number"] == 3
    assert row["clause_no"] == [1] and row["section_no"] == ["2.3"]
    assert row["is_enabled"] is True
    assert row["override_state"] == "none"
    assert data["document"]["organization_id"] == ORG


@pytest.mark.asyncio
async def test_locators_derive_from_parser_text_when_meta_has_no_clause_fields(factory):
    """真实索引路径只把 clause_no 写进 ES；DB meta 缺失时也必须能定位。"""
    maker, _ = factory
    async with maker() as db:
        chunk = _chunk("chunk-no-meta", DOC, 0, original=ORIGINAL, indexed=INDEXED)
        chunk.meta_data = {"text_provenance": PARSER_ORIGINAL}
        doc = await db.get(Document, DOC)
        item = svc.chunk_to_item(chunk, doc, None)
    assert item.clause_no == [1]
    assert item.section_no == []
    assert item.parser_original_text == ORIGINAL


@pytest.mark.asyncio
async def test_cross_org_and_private_acl_cannot_browse_or_edit(factory):
    maker, _ = factory
    stranger = _user(STRANGER, OTHER_ORG)
    async with maker() as db:
        with pytest.raises(AuthorizationError):
            await svc.browse_chunks(db, stranger, DOC, page=1, page_size=20)
        with pytest.raises(AuthorizationError):
            await svc.browse_chunks(db, stranger, PRIVATE_DOC, page=1, page_size=20)
        # 同组织但私有文档非上传者、无 ACL：读也看不到（NotFound 防枚举）。
        with pytest.raises(NotFoundError):
            await svc.browse_chunks(db, _user(999, ORG), PRIVATE_DOC, page=1, page_size=20)
        # ACL 授权可读，但写仅上传者/管理员：仍不能编辑。
        granted = await svc.browse_chunks(db, _user(GRANTED), PRIVATE_DOC, page=1, page_size=20)
        assert granted["data"]["items"][0]["parser_original_text"] == SECRET
        with pytest.raises(AuthorizationError):
            await svc.set_chunk_enabled(
                db, _user(GRANTED), PRIVATE_DOC, "chunk-private-0", enabled=False
            )


@pytest.mark.asyncio
async def test_manual_override_never_mutates_immutable_evidence(factory):
    maker, chunk_ids = factory
    patches = _patch_external()
    with patches[0] as es, patches[1], patches[2] as audit:
        async with maker() as db:
            before = await db.get(DocumentChunk, chunk_ids[0])
            old_text, old_original, old_meta = (
                before.chunk_text,
                before.original_text,
                dict(before.meta_data),
            )
            item = await svc.upsert_chunk_override(
                db,
                _user(OWNER),
                DOC,
                chunk_ids[0],
                text="人工修正后的检索文本",
                apply_to_retrieval=True,
            )
            await db.refresh(before)
            assert before.chunk_text == old_text
            assert before.original_text == old_original
            assert before.meta_data == old_meta
            override = await db.get(DocumentChunkOverride, item["override_id"])
            assert override.override_text == "人工修正后的检索文本"
            assert override.apply_to_retrieval is True
            evidence = (await db.execute(sa_select(ReviewEvidence))).scalar_one()
            assert evidence.quote_text == SECRET
            assert evidence.quote_sha256 == hashlib.sha256(SECRET.encode("utf-8")).hexdigest()
            fields = es.await_args.args[1]
            assert fields["retrieval_text_provenance"] == svc.MANUAL_OVERRIDE
            assert "text_provenance" not in fields
            assert "original_text" not in fields
            assert item["text_provenance"] == PARSER_ORIGINAL
            assert item["effective_text_provenance"] == svc.MANUAL_OVERRIDE
            assert item["parser_original_text"] == ORIGINAL
            assert SECRET not in audit.await_args.kwargs["detail"]
            assert "text_sha256" in audit.await_args.kwargs["detail"]


@pytest.mark.asyncio
async def test_manual_text_cannot_masquerade_as_parser_original_in_strict_replay(factory):
    """Strict replay 只读 parser 原文；manual 标记不会被当成原文的证明。"""
    maker, chunk_ids = factory
    patches = _patch_external()
    with patches[0], patches[1], patches[2]:
        async with maker() as db:
            await svc.upsert_chunk_override(
                db, _user(OWNER), DOC, chunk_ids[0], text="人工改写", apply_to_retrieval=True
            )
    hit = {
        "text": "人工改写",
        "original_text": ORIGINAL,
        "text_provenance": PARSER_ORIGINAL,
        "retrieval_text_provenance": svc.MANUAL_OVERRIDE,
    }
    strict = normalize_evidence(hit, query="付款", mode=STRICT, snippet_builder=lambda t, q: t)
    assert strict["original_text"] == ORIGINAL
    assert strict["prompt_text"] == ORIGINAL  # 不是人工文本
    compat = normalize_evidence(hit, query="付款", mode=COMPAT, snippet_builder=lambda t, q: t)
    assert compat["prompt_text"] == ORIGINAL
    unverified = {**hit, "text_provenance": LEGACY_UNVERIFIED, "original_text": None}
    blank = normalize_evidence(
        unverified, query="付款", mode=STRICT, snippet_builder=lambda t, q: t
    )
    assert blank["prompt_text"] == ""  # manual 标记不能救 legacy strict
    # 即便有人把 manual 标记错写进 text_provenance，也绝不被认作 parser 原文。
    forged = normalize_evidence(
        {**hit, "text_provenance": svc.MANUAL_OVERRIDE},
        query="付款",
        mode=STRICT,
        snippet_builder=lambda t, q: t,
    )
    assert forged["text_provenance"] == LEGACY_UNVERIFIED
    assert forged["prompt_text"] == ""


@pytest.mark.asyncio
async def test_private_document_audit_has_no_raw_text(factory):
    """私有文档人工修正的审计 detail 只含哈希/计数，不含原文或修正正文。"""
    maker, _ = factory
    patches = _patch_external()
    with patches[0], patches[1], patches[2] as audit:
        async with maker() as db:
            await svc.upsert_chunk_override(
                db,
                _user(OWNER),
                PRIVATE_DOC,
                "chunk-private-0",
                text="人工修正私有文本 CANARY-MANUAL-4471",
                apply_to_retrieval=True,
            )
    detail = audit.await_args.kwargs["detail"]
    assert SECRET not in detail
    assert "CANARY-MANUAL-4471" not in detail
    assert "text_sha256" in detail
    assert "text_chars" in detail


@pytest.mark.asyncio
async def test_disable_affects_retrieval_immediately(factory):
    maker, chunk_ids = factory
    patches = _patch_external()
    with patches[0] as es, patches[1] as clear_cache, patches[2]:
        async with maker() as db:
            item = await svc.set_chunk_enabled(db, _user(OWNER), DOC, chunk_ids[0], enabled=False)
            assert item["is_enabled"] is False
            assert es.await_args.args[1]["is_chunk_enabled"] is False
            clear_cache.assert_awaited_once()
            result = await svc.browse_chunks(
                db, _user(OWNER), DOC, page=1, page_size=20, enabled=False
            )
            assert [row["chunk_id"] for row in result["data"]["items"]] == [chunk_ids[0]]
    filters = build_retrieval_filters(ORG)
    assert {"bool": {"must_not": [{"term": {"is_chunk_enabled": False}}]}} in filters


@pytest.mark.asyncio
async def test_es_failure_rolls_back_override(factory):
    maker, chunk_ids = factory
    with (
        patch(
            "app.core.elasticsearch.ElasticsearchTools.update_document",
            new_callable=AsyncMock,
            side_effect=RuntimeError("es down"),
        ),
        patch("app.rag.cache.clear_all_for_org", new_callable=AsyncMock),
    ):
        async with maker() as db:
            with pytest.raises(RuntimeError, match="es down"):
                await svc.upsert_chunk_override(
                    db, _user(OWNER), DOC, chunk_ids[0], text="不会落库", apply_to_retrieval=True
                )
    async with maker() as db:
        from sqlalchemy import select as sa_select

        rows = (await db.execute(sa_select(DocumentChunkOverride))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_api_pagination_bounds_and_private_text_leak(factory):
    maker, _ = factory

    @asynccontextmanager
    async def _ctx(user):
        from app.main import app

        async def _db():
            async with maker() as session:
                yield session

        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_db] = _db
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://chunk-edit"
            ) as client:
                yield client
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(get_db, None)

    admin = _user(999, ORG, superuser=True)
    async with _ctx(admin) as client:
        path = f"/api/v1/documents/{DOC}/chunks"
        assert (await client.get(path, params={"page": 0})).status_code == 422
        assert (await client.get(path, params={"page_size": 101})).status_code == 422
        assert (await client.get(path, params={"override_state": "bogus"})).status_code == 422
        ok = await client.get(path, params={"page": 1, "page_size": 1})
        assert ok.status_code == 200
        assert ok.json()["data"]["total"] == 3
        leak = await client.get(
            f"/api/v1/documents/{PRIVATE_DOC}/chunks",
            params={"page": 1, "page_size": 20},
        )
        assert leak.status_code == 200  # 超管有审计访问权
        assert SECRET in leak.text  # 超管可见正文，这是授权路径


# ── 评审 REQUEST CHANGES 的判别用例 ────────────────────────────────


@pytest.mark.asyncio
async def test_correction_keeps_disabled_state(factory):
    """保存修正不得静默重新启用 chunk；只有显式 PATCH enabled 才能改。"""
    maker, chunk_ids = factory
    patches = _patch_external()
    with patches[0] as es, patches[1], patches[2]:
        async with maker() as db:
            await svc.set_chunk_enabled(db, _user(OWNER), DOC, chunk_ids[0], enabled=False)
            es.reset_mock()
            item = await svc.upsert_chunk_override(
                db,
                _user(OWNER),
                DOC,
                chunk_ids[0],
                text="禁用后仍可修正文本",
                apply_to_retrieval=True,
            )
    assert item["is_enabled"] is False
    assert item["override_state"] == "disabled"
    assert es.await_args.args[1]["is_chunk_enabled"] is False
    assert es.await_args.args[1]["retrieval_text_provenance"] == svc.MANUAL_OVERRIDE


@pytest.mark.asyncio
async def test_override_state_projections_draft_active_disabled(factory):
    """列表过滤与写入响应共用 draft/active/disabled/none 投影。"""
    maker, chunk_ids = factory
    patches = _patch_external()
    with patches[0], patches[1], patches[2]:
        async with maker() as db:
            draft = await svc.upsert_chunk_override(
                db, _user(OWNER), DOC, chunk_ids[0], text="草稿正文", apply_to_retrieval=False
            )
            assert draft["override_state"] == "draft"
            active = await svc.upsert_chunk_override(
                db, _user(OWNER), DOC, chunk_ids[1], text="生效正文", apply_to_retrieval=True
            )
            assert active["override_state"] == "active"
            disabled = await svc.set_chunk_enabled(
                db, _user(OWNER), DOC, chunk_ids[1], enabled=False
            )
            assert disabled["override_state"] == "disabled"
            drafts = await svc.browse_chunks(
                db, _user(OWNER), DOC, page=1, page_size=20, override_state="draft"
            )
            actives = await svc.browse_chunks(
                db, _user(OWNER), DOC, page=1, page_size=20, override_state="active"
            )
            disableds = await svc.browse_chunks(
                db, _user(OWNER), DOC, page=1, page_size=20, override_state="disabled"
            )
    assert [row["chunk_id"] for row in drafts["data"]["items"]] == [chunk_ids[0]]
    assert [row["chunk_id"] for row in actives["data"]["items"]] == []
    assert [row["chunk_id"] for row in disableds["data"]["items"]] == [chunk_ids[1]]


@pytest.mark.asyncio
async def test_es_single_doc_update_refreshes_by_default():
    """分块级 ES 写入必须 refresh=True，否则「立即生效」是假承诺。"""
    from app.core import elasticsearch as es_mod
    from app.core.elasticsearch import ElasticsearchTools

    client = MagicMock()

    async def _update(**kwargs):
        return kwargs

    client.update = AsyncMock(side_effect=_update)
    with patch.object(es_mod, "get_elasticsearch", AsyncMock(return_value=client)):
        await ElasticsearchTools.update_document("doc_chunk-1", {"is_chunk_enabled": False})
        await ElasticsearchTools.update_document(
            "doc_chunk-1", {"is_chunk_enabled": True}, refresh=False
        )
    assert client.update.await_args_list[0].kwargs["refresh"] is True
    assert client.update.await_args_list[1].kwargs["refresh"] is False


@pytest.mark.asyncio
async def test_browse_override_state_uses_strip_semantics(factory):
    """列表过滤与 ``_override_state`` 同口径：纯空白 override 不算 active/draft。

    变异点：把 ``func.trim`` 去掉（退回只比长度）→ 空白行被当成 active，用例红。
    """
    maker, chunk_ids = factory
    patches = _patch_external()
    with patches[0], patches[1], patches[2]:
        async with maker() as db:
            # 直接落库绕过写入校验，模拟历史/外部写入的纯空白文本。
            db.add(
                DocumentChunkOverride(
                    id="blank-override",
                    document_chunk_id=chunk_ids[2],
                    document_id=DOC,
                    organization_id=ORG,
                    created_by=OWNER,
                    updated_by=OWNER,
                    is_enabled=True,
                    apply_to_retrieval=True,
                    override_text="   ",
                )
            )
            await db.commit()
            actives = await svc.browse_chunks(
                db, _user(OWNER), DOC, page=1, page_size=20, override_state="active"
            )
    assert [row["chunk_id"] for row in actives["data"]["items"]] == []
    assert (
        svc._override_state(
            SimpleNamespace(is_enabled=True, apply_to_retrieval=True, override_text="   ")
        )
        == "none"
    )


# ── MySQL（无 INSERT ... RETURNING）语义的判别用例 ────────────────────


# ── MySQL（无 INSERT ... RETURNING）语义的判别用例 ────────────────────


def _mysql_like_engine(db_path):
    """构造一个「没有 RETURNING」的 SQLite 引擎，模拟生产 mysql+aiomysql。

    生产库是 MySQL：``INSERT`` 不返回任何列，SQLAlchemy 只能把带
    ``server_default`` 的 ``created_at`` / ``updated_at`` 标记为 expired，
    留待下一次属性访问再取。此后同步上下文里的属性读取就是异步会话上的
    lazy load → ``MissingGreenlet``。SQLite 默认支持 RETURNING（本机 SQLite
    3.35+），会把服务端默认值直接回填，恰好把这个缺陷藏住；因此判别用例
    必须显式关掉 RETURNING，只测生产语义。
    """
    from sqlalchemy.ext.asyncio import create_async_engine as _create

    engine = _create(f"sqlite+aiosqlite:///{db_path}")
    assert engine.dialect.insert_returning is True, "前提：SQLite 默认有 RETURNING"
    engine.dialect.insert_returning = False
    engine.dialect.insert_executemany_returning = False
    return engine


@pytest.mark.asyncio
async def test_first_enable_patch_does_not_lazy_load_timestamps_on_mysql(tmp_path):
    """首次 PATCH /chunks/enabled（无既有 override 行）不得触发时间戳 lazy load。

    变异点：删掉 ``set_chunk_enabled`` 新建行上的 ``updated_at``/``created_at``
    显式赋值，也删掉模型侧 ``default=`` → 本用例在生产语义下红
    （``override.updated_at`` 读成 MissingGreenlet / 仍是 expired）。
    """
    maker_engine = _mysql_like_engine(tmp_path / "mysql-like.db")
    async with maker_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(maker_engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Org A", owner_id=OWNER),
                _user(OWNER, ORG),
                _doc(DOC, ORG),
                _chunk("chunk-mysql-0", DOC, 0, original=ORIGINAL, indexed=INDEXED),
            ]
        )
        await db.commit()

    patches = _patch_external()
    try:
        with patches[0], patches[1], patches[2]:
            async with maker() as db:
                item = await svc.set_chunk_enabled(
                    db, _user(OWNER), DOC, "chunk-mysql-0", enabled=False
                )
                # 投影必须拿到真实时间戳（不是 None、不是异常），且读取
                # 不依赖数据库回填。
                assert item["override_updated_at"] is not None
                override = await db.get(DocumentChunkOverride, item["override_id"])
                unloaded = set(sa_inspect(override).unloaded)
                assert "updated_at" not in unloaded, (
                    "新建 override 的 updated_at 不得依赖 post-fetch：MySQL 无 "
                    f"INSERT ... RETURNING，expired 属性会触发 MissingGreenlet，unloaded={unloaded}"
                )
                assert "created_at" not in unloaded
                # 显式构造的同一语义（不经 flush）：default= 在 INSERT 时才求值，
                # 因此这里断言的是「赋值走模型列默认」——实例化后首次 flush
                # 前并非已填值；真正的判别在上面的 unloaded / 投影时间戳。
                fresh = DocumentChunkOverride(
                    id="probe",
                    document_chunk_id="x",
                    document_id="y",
                    organization_id=ORG,
                    created_by=OWNER,
                    updated_by=OWNER,
                    is_enabled=True,
                )
                db.add(fresh)
                await db.flush()
                assert set(sa_inspect(fresh).unloaded).isdisjoint({"created_at", "updated_at"}), (
                    "客户端 default= 必须在 flush 后直接可用（不依赖 RETURNING 回填）"
                )
                db.expunge(fresh)
    finally:
        await maker_engine.dispose()


@pytest.mark.asyncio
async def test_upsert_override_timestamps_do_not_rely_on_returning(tmp_path):
    """upsert 新建行的 updated_at 同样不得依赖 post-fetch（生产 MySQL 语义）。"""
    maker_engine = _mysql_like_engine(tmp_path / "mysql-like-upsert.db")
    async with maker_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(maker_engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Org A", owner_id=OWNER),
                _user(OWNER, ORG),
                _doc(DOC, ORG),
                _chunk("chunk-upsert-0", DOC, 0, original=ORIGINAL, indexed=INDEXED),
            ]
        )
        await db.commit()

    patches = _patch_external()
    try:
        with patches[0], patches[1], patches[2]:
            async with maker() as db:
                item = await svc.upsert_chunk_override(
                    db,
                    _user(OWNER),
                    DOC,
                    "chunk-upsert-0",
                    text="人工文本",
                    apply_to_retrieval=True,
                )
                assert item["override_updated_at"] is not None
                override = await db.get(DocumentChunkOverride, item["override_id"])
                unloaded = set(sa_inspect(override).unloaded)
                assert "updated_at" not in unloaded, f"unloaded={unloaded}"
    finally:
        await maker_engine.dispose()
