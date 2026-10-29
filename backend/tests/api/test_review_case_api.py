"""核验案件 API（真实 SQLite + ASGI）：建案/列表/详情/证据选择。"""

import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.review import ReviewDocumentMeta

ORG = 1
POLICY = "doc-api-policy"
BASE = "doc-api-base"
REVISED = "doc-api-revised"
LEGACY = "doc-api-legacy"


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-case-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=0))
        docs = []
        for doc_id in (POLICY, BASE, REVISED, LEGACY):
            docs.append(
                Document(
                    id=doc_id,
                    filename=f"{doc_id}.pdf",
                    file_path=f"1/{doc_id}.pdf",
                    file_size=100,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=1,
                )
            )
        db.add_all(docs)
        db.add_all(
            [
                ReviewDocumentMeta(
                    document_id=POLICY,
                    organization_id=ORG,
                    doc_role="policy",
                    content_sha256="p" * 64,
                    registered_by=1,
                ),
                ReviewDocumentMeta(
                    document_id=BASE,
                    organization_id=ORG,
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="V1",
                    version_seq=1,
                    content_sha256="b" * 64,
                    registered_by=1,
                ),
                ReviewDocumentMeta(
                    document_id=REVISED,
                    organization_id=ORG,
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="V2",
                    version_seq=2,
                    content_sha256="r" * 64,
                    registered_by=1,
                ),
                ReviewDocumentMeta(
                    document_id=LEGACY,
                    organization_id=ORG,
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="V3",
                    version_seq=3,
                    content_sha256="l" * 64,
                    registered_by=1,
                ),
            ]
        )

        def chunk(doc_id, idx, original, *, page=1, meta=None):
            return DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                chunk_index=idx,
                chunk_text=f"【文档背景：摘要】{original}",
                original_text=original,
                chunk_length=len(original),
                page_number=page,
                meta_data=meta or {"text_provenance": "parser_original"},
            )

        db.add_all(
            [
                chunk(POLICY, 0, "第3条 采购付款应遵循本制度，另有约定的从其约定。"),
                chunk(BASE, 0, "第一条 付款期限为30天，自收到发票之日起算。"),
                chunk(BASE, 1, "第二条 验收合格后支付价款。"),
                chunk(REVISED, 0, "第一条 付款期限为60天，自收到发票之日起算。"),
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=LEGACY,
                    chunk_index=0,
                    chunk_text="【文档背景：摘要】旧版付款条款",
                    original_text=None,
                    chunk_length=8,
                    page_number=1,
                    meta_data={"text_provenance": "legacy_unverified"},
                ),
            ]
        )
        await db.commit()
    yield maker
    await engine.dispose()


class _Ctx:
    def __init__(self, user, factory):
        self.user = user
        self.factory = factory

    async def __aenter__(self):
        from app.main import app

        async def _user():
            return self.user

        async def _db():
            async with self.factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://review")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(org=ORG, *, superuser=False):
    return SimpleNamespace(
        id=1,
        username="tester",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="admin" if superuser else "user",
    )


def _case_payload(*, revised=REVISED, title="采购合同 V1→V2 核验"):
    return {
        "title": title,
        "policy_document_id": POLICY,
        "base_document_id": BASE,
        "revised_document_id": revised,
    }


async def _create_case(client):
    r = await client.post("/api/v1/reviews", json=_case_payload())
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.asyncio
async def test_create_list_detail_and_select_evidence_flow(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        assert case["status"] == "ready", case
        assert case["live_source_stale"] is False
        assert [i["item_key"] for i in case["items"]] == [
            "payment_term",
            "start_condition",
            "acceptance_invoice",
            "exception",
        ]
        assert case["documents"]["base"]["version_label"] == "V1"
        assert case["documents"]["revised"]["version_label"] == "V2"
        assert case["documents"]["base"]["readiness"]["status"] == "reviewable"

        listed = await ac.get("/api/v1/reviews")
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"]["total"] == 1

        detail = await ac.get(f"/api/v1/reviews/{case['id']}")
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert detail_data["id"] == case["id"]

        payment = next(i for i in detail_data["items"] if i["item_key"] == "payment_term")
        base_evidence = payment["evidence"]["base"]
        assert base_evidence, "建案时应在 base 侧抓到候选证据"
        candidate = base_evidence[0]
        assert candidate["text_provenance"] == "parser_original"
        assert "【文档背景" not in candidate["quote"]

        selected = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={
                "side": "base",
                "chunk_id": candidate["chunk_id_at_capture"],
                "role": "main",
            },
        )
        assert selected.status_code == 200, selected.text
        row = selected.json()["data"]
        assert row["selected"] is True
        assert row["role"] == "main"
        assert row["quote"] == candidate["quote"]
        assert row["quote_sha256"] == candidate["quote_sha256"]


@pytest.mark.asyncio
async def test_legacy_revised_blocks_case_and_evidence_is_409(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        r = await ac.post("/api/v1/reviews", json=_case_payload(revised=LEGACY))
        assert r.status_code == 200, r.text
        case = r.json()["data"]
        assert case["status"] == "blocked"
        assert "revised:no_original_text" in case["blocking_reasons"]

        payment = case["items"][0]
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={"side": "base", "chunk_id": str(uuid.uuid4())},
        )
        assert put.status_code == 409, put.text
        assert put.json()["error_code"] == "REVIEW_CASE_NOT_READY"


@pytest.mark.asyncio
async def test_legacy_chunk_selection_is_409_source_unavailable(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        payment = next(i for i in case["items"] if i["item_key"] == "payment_term")

        async with factory() as db:
            other_doc_chunk_id = (
                await db.execute(
                    select(DocumentChunk.id).where(DocumentChunk.document_id == LEGACY)
                )
            ).scalar_one()
        # 分块不属于该侧文档：404，而不是把别的文档正文当作证据。
        cross = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={"side": "revised", "chunk_id": other_doc_chunk_id},
        )
        assert cross.status_code == 404, cross.text

        # 建案后文档被写入一个 legacy 分块（缺 parser 标记）：选择时必须 409，
        # 不得回退 chunk_text 快照成「原文证据」。
        async with factory() as db:
            legacy_chunk = DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=BASE,
                chunk_index=9,
                chunk_text="旧文本 付款期限30天",
                original_text=None,
                chunk_length=10,
                page_number=1,
                meta_data={"text_provenance": "legacy_unverified"},
            )
            db.add(legacy_chunk)
            await db.commit()
            legacy_chunk_id = legacy_chunk.id
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={"side": "base", "chunk_id": legacy_chunk_id},
        )
        assert put.status_code == 409, put.text
        assert put.json()["error_code"] == "SOURCE_ORIGINAL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_cross_org_access_is_forbidden(factory, monkeypatch):
    async def _grant_all(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value, PermissionType.DOCUMENT_UPDATE.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant_all)

    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)

    async with _Ctx(_user(org=2), factory) as ac:
        detail = await ac.get(f"/api/v1/reviews/{case['id']}")
        assert detail.status_code == 403, detail.text

        post = await ac.post("/api/v1/reviews", json=_case_payload())
        assert post.status_code == 403, post.text

        item = case["items"][0]
        chunk_id = item["evidence"]["base"][0]["chunk_id_at_capture"]
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{item['id']}/evidence",
            json={"side": "base", "chunk_id": chunk_id},
        )
        assert put.status_code == 403, put.text

        # 列表按调用者组织过滤：别的组织看不到本案件，返回空而不是泄露。
        listed = await ac.get("/api/v1/reviews")
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_review_endpoints_require_document_permissions(factory):
    async with _Ctx(_user(org=ORG), factory) as ac:
        listed = await ac.get("/api/v1/reviews")
        post = await ac.post("/api/v1/reviews", json=_case_payload())

    assert listed.status_code == 403, listed.text
    assert post.status_code == 403, post.text


@pytest.mark.asyncio
async def test_read_only_role_cannot_create_or_select_but_can_list(factory, monkeypatch):
    async def _read_only(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _read_only)

    async with _Ctx(_user(org=ORG), factory) as ac:
        listed = await ac.get("/api/v1/reviews")
        post = await ac.post("/api/v1/reviews", json=_case_payload())

    assert listed.status_code == 200, listed.text
    assert post.status_code == 403, post.text


@pytest.mark.asyncio
async def test_unknown_case_and_item_are_404(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        missing = await ac.get(f"/api/v1/reviews/{uuid.uuid4()}")
        assert missing.status_code == 404, missing.text

        case = await _create_case(ac)
        item = case["items"][0]
        chunk_id = item["evidence"]["base"][0]["chunk_id_at_capture"]
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{uuid.uuid4()}/evidence",
            json={"side": "base", "chunk_id": chunk_id},
        )
        assert put.status_code == 404, put.text


@pytest.mark.asyncio
async def test_analyze_case_endpoint_updates_items_and_is_idempotent(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        r = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        items = {i["item_key"]: i for i in data["items"]}
        payment = items["payment_term"]
        assert payment["change_type"] == "modified"
        assert payment["facts"]["base"]["value"] == 30
        assert payment["facts"]["revised"]["value"] == 60
        assert payment["facts"]["extractor_version"] == 1

        r2 = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r2.status_code == 200, r2.text
        second = {i["item_key"]: (i["change_type"], i["facts"]) for i in r2.json()["data"]["items"]}
        first = {i["item_key"]: (i["change_type"], i["facts"]) for i in data["items"]}
        assert second == first, "同一输入重复分析必须一致"


@pytest.mark.asyncio
async def test_analyze_blocked_case_409_cross_org_403_and_unknown_404(factory, monkeypatch):
    async with _Ctx(_user(superuser=True), factory) as ac:
        blocked = (await ac.post("/api/v1/reviews", json=_case_payload(revised=LEGACY))).json()[
            "data"
        ]
        r = await ac.post(f"/api/v1/reviews/{blocked['id']}/analyze")
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "REVIEW_CASE_NOT_READY"
        case = await _create_case(ac)

    async def _grant_all(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value, PermissionType.DOCUMENT_UPDATE.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant_all)

    async with _Ctx(_user(org=2), factory) as ac:
        cross = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert cross.status_code == 403, cross.text
        missing = await ac.post(f"/api/v1/reviews/{uuid.uuid4()}/analyze")
        assert missing.status_code == 404, missing.text


@pytest.mark.asyncio
async def test_analyze_requires_write_permission(factory, monkeypatch):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)

    async def _read_only(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _read_only)

    async with _Ctx(_user(org=ORG), factory) as ac:
        r = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_analyze_legacy_after_creation_maps_409(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        async with factory() as db:
            await db.execute(
                DocumentChunk.__table__.delete().where(DocumentChunk.document_id == BASE)
            )
            db.add(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=BASE,
                    chunk_index=0,
                    chunk_text="旧文 付款期限30天",
                    original_text=None,
                    chunk_length=10,
                    page_number=1,
                    meta_data={"text_provenance": "legacy_unverified"},
                )
            )
            await db.commit()
        r = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "SOURCE_ORIGINAL_UNAVAILABLE"
