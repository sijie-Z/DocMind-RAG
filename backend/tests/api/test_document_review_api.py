"""文档核验身份/readiness/evidence API（真实 SQLite + ASGI）。"""

import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.review import ReviewDocumentMeta
from app.services import review_document_service as svc

ORG = 1
DOC = "doc-review-api"
LEGACY = "doc-review-legacy"


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=0))
        db.add_all(
            [
                Document(
                    id=DOC,
                    filename="contract.pdf",
                    file_path=f"1/{DOC}.pdf",
                    file_size=10,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=1,
                ),
                Document(
                    id=LEGACY,
                    filename="legacy.pdf",
                    file_path=f"1/{LEGACY}.pdf",
                    file_size=10,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=1,
                ),
            ]
        )
        db.add_all(
            [
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=DOC,
                    chunk_index=0,
                    chunk_text="【文档背景：摘要】第一条 付款期限为30天。",
                    original_text="第一条 付款期限为30天。",
                    chunk_length=20,
                    page_number=1,
                    meta_data={"text_provenance": "parser_original"},
                ),
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=LEGACY,
                    chunk_index=0,
                    chunk_text="【文档背景：摘要】旧文",
                    original_text=None,
                    chunk_length=10,
                    page_number=1,
                    meta_data={"text_provenance": "legacy_unverified"},
                ),
                ReviewDocumentMeta(
                    document_id=DOC,
                    organization_id=ORG,
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="v1",
                    version_seq=1,
                    content_sha256="c" * 64,
                    registered_by=1,
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


@pytest.mark.asyncio
async def test_readiness_and_evidence_reviewable(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        readiness = await ac.get(f"/api/v1/documents/{DOC}/readiness")
        evidence = await ac.get(f"/api/v1/documents/{DOC}/evidence", params={"clause_no": 1})

    assert readiness.status_code == 200, readiness.text
    data = readiness.json()["data"]
    assert data["status"] == "reviewable"
    assert data["meta"]["version_label"] == "v1"

    assert evidence.status_code == 200, evidence.text
    item = evidence.json()["data"]["items"][0]
    assert item["quote"] == "第一条 付款期限为30天。"
    assert "【文档背景" not in item["quote"]
    assert item["text_provenance"] == "parser_original"


@pytest.mark.asyncio
async def test_legacy_evidence_is_409_and_readiness_blocked(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        readiness = await ac.get(f"/api/v1/documents/{LEGACY}/readiness")
        evidence = await ac.get(f"/api/v1/documents/{LEGACY}/evidence")

    assert readiness.status_code == 200
    assert readiness.json()["data"]["status"] == "blocked"
    assert "no_original_text" in readiness.json()["data"]["blocking_reasons"]
    assert readiness.json()["data"]["legacy_chunk_count"] == 1

    assert evidence.status_code == 409, evidence.text
    body = evidence.json()
    assert body["error_code"] == "SOURCE_ORIGINAL_UNAVAILABLE"
    assert body["detail"]["status"] == "needs_rebuild"


@pytest.mark.asyncio
async def test_cross_org_readiness_and_evidence_are_404(factory, monkeypatch):
    async def _grant(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant)

    async with _Ctx(_user(org=2), factory) as ac:
        readiness = await ac.get(f"/api/v1/documents/{DOC}/readiness")
        evidence = await ac.get(f"/api/v1/documents/{DOC}/evidence")

    # 文档资源越权按既有 get_document_for_user 口径返回 403（审计允许 403/404）。
    assert readiness.status_code == 403, readiness.text
    assert evidence.status_code == 403, evidence.text


@pytest.mark.asyncio
async def test_put_review_meta_computes_hash_and_rejects_duplicate(factory, monkeypatch):
    async def _hash(_path):
        return "d" * 64

    monkeypatch.setattr(svc, "_sha256_storage_object", _hash)

    async with _Ctx(_user(superuser=True), factory) as ac:
        r = await ac.put(
            f"/api/v1/documents/{LEGACY}/review-meta",
            json={
                "doc_role": "contract",
                "family_key": "supplier-x",
                "version_label": "v1",
                "version_seq": 1,
            },
        )
    assert r.status_code == 409, r.text
    assert r.json()["error_code"] == "REVIEW_META_DUPLICATE"

    async with _Ctx(_user(superuser=True), factory) as ac:
        r2 = await ac.put(
            f"/api/v1/documents/{LEGACY}/review-meta",
            json={"doc_role": "contract", "family_key": "supplier-x"},
        )
    assert r2.status_code == 400, r2.text


@pytest.mark.asyncio
async def test_review_endpoints_require_document_permissions(factory):
    """普通用户无 document:read/update 时，三个新端点都必须 403（不是仅靠 scope）。"""
    async with _Ctx(_user(org=ORG), factory) as ac:
        readiness = await ac.get(f"/api/v1/documents/{DOC}/readiness")
        evidence = await ac.get(f"/api/v1/documents/{DOC}/evidence")
        put = await ac.put(
            f"/api/v1/documents/{DOC}/review-meta",
            json={
                "doc_role": "contract",
                "family_key": "supplier-y",
                "version_label": "v2",
                "version_seq": 2,
            },
        )

    assert readiness.status_code == 403, readiness.text
    assert evidence.status_code == 403, evidence.text
    assert put.status_code == 403, put.text


@pytest.mark.asyncio
async def test_put_review_meta_requires_document_update(factory, monkeypatch):
    """只读角色（document:read）不得注册核验身份。"""

    async def _grant(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant)

    async with _Ctx(_user(org=ORG), factory) as ac:
        readiness = await ac.get(f"/api/v1/documents/{DOC}/readiness")
        put = await ac.put(
            f"/api/v1/documents/{DOC}/review-meta",
            json={
                "doc_role": "contract",
                "family_key": "supplier-z",
                "version_label": "v3",
                "version_seq": 3,
            },
        )

    assert readiness.status_code == 200, readiness.text
    assert put.status_code == 403, put.text


@pytest.mark.asyncio
async def test_put_review_meta_policy_without_version_succeeds(factory, monkeypatch):
    async def _hash(_path):
        return "e" * 64

    monkeypatch.setattr(svc, "_sha256_storage_object", _hash)

    async with _Ctx(_user(superuser=True), factory) as ac:
        r = await ac.put(
            f"/api/v1/documents/{LEGACY}/review-meta",
            json={"doc_role": "policy"},
        )

    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["doc_role"] == "policy"
    assert data["family_key"] is None
    assert data["content_sha256"] == "e" * 64
