"""Authenticated document download URL and legacy redirect contracts."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — register all tables for create_all
from app.api.v1.endpoints import documents as document_api
from app.core.database import Base, get_db
from app.core.minio_client import minio_client
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.organization import Organization, user_organization
from app.models.rbac import Permission, PermissionType, Role
from app.models.user import User
from app.services.api_key_service import api_key_service
from app.services.auth_service import auth_service

ORG_ID = 42
USER_ID = 7
DOC_ID = "doc-download-1"
NEW_URL = f"/api/v1/documents/{DOC_ID}/download-url"
LEGACY_URL = f"/api/v1/documents/{DOC_ID}/download"


@pytest.fixture
async def env(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    permissions = [Permission(name=p.value) for p in PermissionType]
    async with factory() as session:
        session.add(Organization(id=ORG_ID, name="Download Org", owner_id=USER_ID))
        session.add(
            User(
                id=USER_ID,
                username="download-user",
                email="download@example.invalid",
                hashed_password="x",
                is_active=True,
                organization_id=ORG_ID,
            )
        )
        session.add(Role(name="User", is_system_role=True, permissions=permissions))
        session.add_all(permissions)
        session.add(
            Document(
                id=DOC_ID,
                filename="scope.pdf",
                file_path=f"{ORG_ID}/scope.pdf",
                file_size=10,
                file_type=DocumentType.PDF,
                status=DocumentStatus.INDEXED,
                organization_id=ORG_ID,
                uploaded_by=USER_ID,
                title="Download Doc",
            )
        )
        await session.execute(
            user_organization.insert().values(user_id=USER_ID, organization_id=ORG_ID)
        )
        await session.commit()

    sign_calls: list[tuple[str, timedelta]] = []

    def fake_presign(object_name: str, expires: timedelta) -> str:
        sign_calls.append((object_name, expires))
        return f"https://minio.test/{object_name}?X-Amz-Signature=fake"

    async def no_blacklist(_token: str) -> bool:
        return False

    monkeypatch.setattr(minio_client, "get_presigned_url", fake_presign)
    monkeypatch.setattr(auth_service, "is_token_blacklisted", no_blacklist)

    async def override_db():
        async with factory() as session:
            yield session

    from app.main import app

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://download.test",
        follow_redirects=False,
    ) as client:
        yield SimpleNamespace(client=client, factory=factory, sign_calls=sign_calls)
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


def jwt() -> str:
    return auth_service.create_access_token({"user_id": USER_ID, "role": "user"})


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make_key(env, scopes: list[str]) -> str:
    async with env.factory() as session:
        user = await session.get(User, USER_ID)
        _, raw = await api_key_service.create_key(
            session, user=user, name="download-scope-key", scopes=scopes
        )
        return raw


@pytest.mark.asyncio
async def test_json_and_legacy_share_authorization_signing_and_expiry(env, monkeypatch):
    helper_calls: list[str] = []
    original_helper = document_api._get_document_download_url

    async def spy_helper(db, current_user, document_id):
        helper_calls.append(document_id)
        return await original_helper(db, current_user, document_id)

    monkeypatch.setattr(document_api, "_get_document_download_url", spy_helper)
    token = jwt()

    json_response = await env.client.get(NEW_URL, headers=auth(token))
    assert json_response.status_code == 200, json_response.text
    envelope = json_response.json()
    assert envelope["success"] is True
    payload = envelope["data"]
    assert payload["filename"] == "scope.pdf"
    assert payload["expiresIn"] == 3600
    assert payload["downloadUrl"].startswith("https://minio.test/42/scope.pdf?")
    assert token not in json_response.text
    assert "token" not in payload["downloadUrl"].lower()

    legacy_response = await env.client.get(LEGACY_URL, headers=auth(token))
    assert legacy_response.status_code == 307, legacy_response.text
    assert legacy_response.headers["location"] == payload["downloadUrl"]

    assert helper_calls == [DOC_ID, DOC_ID]
    assert env.sign_calls == [
        ("42/scope.pdf", document_api.DOCUMENT_DOWNLOAD_URL_EXPIRES),
        ("42/scope.pdf", document_api.DOCUMENT_DOWNLOAD_URL_EXPIRES),
    ]


@pytest.mark.asyncio
async def test_missing_credentials_and_scope_never_reach_signing(env):
    for path in (NEW_URL, LEGACY_URL):
        response = await env.client.get(path)
        assert response.status_code == 401, response.text
        assert "downloadUrl" not in response.text
        assert "location" not in response.headers

    raw_key = await make_key(env, ["rpc"])
    for path in (NEW_URL, LEGACY_URL):
        response = await env.client.get(path, headers=auth(raw_key))
        assert response.status_code == 403, response.text
        assert response.json()["error_code"] == "AUTHORIZATION_ERROR"
        assert "downloadUrl" not in response.text
        assert "location" not in response.headers
    assert env.sign_calls == []


@pytest.mark.asyncio
async def test_cross_organization_document_is_rejected_without_signing(env):
    other_org_id = 99
    other_user_id = 8
    other_doc_id = "doc-other-org"
    async with env.factory() as session:
        session.add(Organization(id=other_org_id, name="Other Org", owner_id=other_user_id))
        session.add(
            User(
                id=other_user_id,
                username="other-user",
                email="other@example.invalid",
                hashed_password="x",
                is_active=True,
                organization_id=other_org_id,
            )
        )
        session.add(
            Document(
                id=other_doc_id,
                filename="other.pdf",
                file_path=f"{other_org_id}/other.pdf",
                file_size=10,
                file_type=DocumentType.PDF,
                status=DocumentStatus.INDEXED,
                organization_id=other_org_id,
                uploaded_by=other_user_id,
            )
        )
        await session.commit()

    for path in (
        f"/api/v1/documents/{other_doc_id}/download-url",
        f"/api/v1/documents/{other_doc_id}/download",
    ):
        response = await env.client.get(path, headers=auth(jwt()))
        assert response.status_code == 403, response.text
        assert response.json()["error_code"] == "AUTHORIZATION_ERROR"
        assert "downloadUrl" not in response.text
        assert "location" not in response.headers
    assert env.sign_calls == []


@pytest.mark.asyncio
async def test_signing_failure_is_an_error_not_a_download_url(env, monkeypatch):
    def fail_signing(_object_name: str, _expires: timedelta) -> str:
        raise RuntimeError("signing unavailable")

    monkeypatch.setattr(minio_client, "get_presigned_url", fail_signing)
    for path in (NEW_URL, LEGACY_URL):
        response = await env.client.get(path, headers=auth(jwt()))
        assert response.status_code == 500, response.text
        assert response.json()["error_code"] == "INTERNAL_ERROR"
        assert "downloadUrl" not in response.text
        assert "location" not in response.headers
