"""OrgAdmin global credentials and org-bound API keys must respect tenant boundaries.

These tests use the real FastAPI ASGI app, real JWT/API-key authentication and
SQLite tables. Only Redis blacklist checks and external document deletion are
replaced. The database is disposable and in-memory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 -- register every model for create_all
from app.core.database import Base, get_db
from app.models.api_key import ApiKey
from app.models.document import Document, DocumentType
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.models.organization import Organization, user_organization
from app.models.rbac import Role, user_organization_role_association
from app.models.user import User
from app.services.api_key_service import api_key_service
from app.services.auth_service import auth_service
from app.services.permission_service import permission_service

ORG_A = 1
ORG_B = 2
SUPERUSER_ID = 1
ORG_ADMIN_ID = 10
PURE_A_USER_ID = 11
NULL_PRIMARY_FOREIGN_MEMBER_ID = 20
MULTI_ORG_USER_ID = 30
EXTERNAL_ROLE_USER_ID = 31
UNAFFILIATED_USER_ID = 40
KEY_USER_ID = 50
KEY_SUPERUSER_ID = 60
PURE_B_USER_ID = 70


def _jwt(user_id: int, organization_id: int | None) -> dict[str, str]:
    token = auth_service.create_access_token(
        {"user_id": user_id, "role": "user", "organization_id": organization_id}
    )
    return {"Authorization": f"Bearer {token}"}


def _key_auth(raw_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw_key}"}


def _knowledge_ids(response: httpx.Response) -> set[str]:
    assert response.status_code == 200, response.text
    payload = response.json()["data"]
    assert payload["total"] == len(payload["data"])
    return {item["id"] for item in payload["data"]}


async def _settings_operations(env, organization_id: int, headers: dict[str, str]):
    base = f"/api/v1/organizations/{organization_id}/settings"
    return [
        await env.client.get(base, headers=headers),
        await env.client.put(base, json={"retrieval_top_k": 7}, headers=headers),
        await env.client.get(f"{base}/audit", headers=headers),
    ]


async def _seed(factory) -> None:
    async with factory() as db:
        db.add_all(
            [
                User(
                    id=SUPERUSER_ID,
                    username="platform-root",
                    email="platform-root@example.test",
                    hashed_password="root-hash",
                    organization_id=ORG_A,
                    is_active=True,
                    is_superuser=True,
                ),
                User(
                    id=ORG_ADMIN_ID,
                    username="org-a-admin",
                    email="org-a-admin@example.test",
                    hashed_password="admin-hash",
                    organization_id=ORG_A,
                    is_active=True,
                ),
                User(
                    id=PURE_A_USER_ID,
                    username="pure-a",
                    email="pure-a@example.test",
                    hashed_password="pure-a-hash",
                    organization_id=ORG_A,
                    is_active=True,
                ),
                User(
                    id=NULL_PRIMARY_FOREIGN_MEMBER_ID,
                    username="null-primary-b-member",
                    email="null-primary-b-member@example.test",
                    hashed_password="victim-hash",
                    organization_id=None,
                    is_active=True,
                ),
                User(
                    id=MULTI_ORG_USER_ID,
                    username="primary-a-secondary-b",
                    email="primary-a-secondary-b@example.test",
                    hashed_password="multi-org-hash",
                    organization_id=ORG_A,
                    is_active=True,
                ),
                User(
                    id=EXTERNAL_ROLE_USER_ID,
                    username="primary-a-role-b",
                    email="primary-a-role-b@example.test",
                    hashed_password="external-role-hash",
                    organization_id=ORG_A,
                    is_active=True,
                ),
                User(
                    id=UNAFFILIATED_USER_ID,
                    username="unaffiliated",
                    email="unaffiliated@example.test",
                    hashed_password="unaffiliated-hash",
                    organization_id=None,
                    is_active=True,
                ),
                User(
                    id=KEY_USER_ID,
                    username="key-user",
                    email="key-user@example.test",
                    hashed_password="key-user-hash",
                    organization_id=ORG_A,
                    is_active=True,
                ),
                User(
                    id=KEY_SUPERUSER_ID,
                    username="key-superuser",
                    email="key-superuser@example.test",
                    hashed_password="key-superuser-hash",
                    organization_id=ORG_B,
                    is_active=True,
                    is_superuser=True,
                ),
                User(
                    id=PURE_B_USER_ID,
                    username="pure-b",
                    email="pure-b@example.test",
                    hashed_password="pure-b-hash",
                    organization_id=ORG_B,
                    is_active=True,
                ),
            ]
        )
        await db.flush()
        db.add_all(
            [
                Organization(id=ORG_A, name="Tenant A", owner_id=SUPERUSER_ID),
                Organization(id=ORG_B, name="Tenant B", owner_id=SUPERUSER_ID),
            ]
        )
        await db.flush()
        memberships = [
            (ORG_ADMIN_ID, ORG_A),
            (PURE_A_USER_ID, ORG_A),
            (NULL_PRIMARY_FOREIGN_MEMBER_ID, ORG_B),
            (MULTI_ORG_USER_ID, ORG_A),
            (MULTI_ORG_USER_ID, ORG_B),
            (EXTERNAL_ROLE_USER_ID, ORG_A),
            (KEY_USER_ID, ORG_A),
            (KEY_USER_ID, ORG_B),
            (PURE_B_USER_ID, ORG_B),
        ]
        await db.execute(
            user_organization.insert(),
            [
                {"user_id": user_id, "organization_id": organization_id}
                for user_id, organization_id in memberships
            ],
        )
        db.add_all(
            [
                Document(
                    id="doc-a",
                    filename="a.txt",
                    file_path="/a",
                    file_size=111,
                    file_type=DocumentType.TXT,
                    organization_id=ORG_A,
                    uploaded_by=KEY_USER_ID,
                ),
                Document(
                    id="doc-b",
                    filename="b.txt",
                    file_path="/b",
                    file_size=999,
                    file_type=DocumentType.TXT,
                    organization_id=ORG_B,
                    uploaded_by=KEY_USER_ID,
                ),
                KnowledgeProcessingJob(
                    id=101,
                    document_id="doc-a",
                    organization_id=ORG_A,
                    trigger_type="upload",
                    status=KnowledgeJobStatus.SUCCESS,
                ),
                KnowledgeProcessingJob(
                    id=999,
                    document_id="doc-b",
                    organization_id=ORG_B,
                    trigger_type="upload",
                    status=KnowledgeJobStatus.FAILED,
                    error_message="B-999",
                ),
            ]
        )
        await db.commit()

        await permission_service.initialize_default_permissions_and_roles(db)
        await permission_service.set_org_admin(db, ORG_ADMIN_ID, ORG_A, True)
        await permission_service.set_org_admin(db, KEY_USER_ID, ORG_A, True)
        await permission_service.set_org_admin(db, KEY_USER_ID, ORG_B, True)
        user_role_id = (await db.execute(select(Role.id).where(Role.name == "User"))).scalar_one()
        # A role association is an affiliation even without a matching membership row.
        await db.execute(
            user_organization_role_association.insert().values(
                user_id=EXTERNAL_ROLE_USER_ID,
                organization_id=ORG_B,
                role_id=user_role_id,
            )
        )
        await db.commit()


@asynccontextmanager
async def _asgi_context(monkeypatch: pytest.MonkeyPatch):
    from app.main import app

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(factory)

    async def override_db():
        async with factory() as session:
            yield session

    missing = object()
    previous_override = app.dependency_overrides.get(get_db, missing)
    app.dependency_overrides[get_db] = override_db
    monkeypatch.setattr(auth_service, "is_token_blacklisted", AsyncMock(return_value=False))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://tenant-test") as client:
            yield SimpleNamespace(client=client, factory=factory)
    finally:
        if previous_override is missing:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        await engine.dispose()


async def _create_key(env, *, user_id: int = KEY_USER_ID, org_id: int = ORG_A) -> str:
    async with env.factory() as db:
        user = await db.get(User, user_id)
        _, raw_key = await api_key_service.create_key(
            db,
            user=user,
            name=f"key-for-{user_id}",
            scopes=["knowledge:read", "knowledge:write"],
            org_id=org_id,
        )
        return raw_key


async def _membership_org_ids(db, user_id: int) -> set[int]:
    rows = await db.execute(
        select(user_organization.c.organization_id).where(user_organization.c.user_id == user_id)
    )
    return set(rows.scalars().all())


async def _role_org_ids(db, user_id: int) -> set[int]:
    rows = await db.execute(
        select(user_organization_role_association.c.organization_id).where(
            user_organization_role_association.c.user_id == user_id
        )
    )
    return set(rows.scalars().all())


@pytest.mark.asyncio
async def test_orgadmin_add_rejects_null_primary_foreign_member_without_mutation(monkeypatch):
    async with _asgi_context(monkeypatch) as env:
        response = await env.client.post(
            f"/api/v1/organizations/{ORG_A}/members/{NULL_PRIMARY_FOREIGN_MEMBER_ID}",
            headers=_jwt(ORG_ADMIN_ID, ORG_A),
        )
        assert response.status_code == 403, response.text

        response = await env.client.put(
            f"/api/v1/users/{NULL_PRIMARY_FOREIGN_MEMBER_ID}/password",
            json={"new_password": "TakenOver123!"},
            headers=_jwt(ORG_ADMIN_ID, ORG_A),
        )
        assert response.status_code == 403, response.text

        async with env.factory() as db:
            victim = await db.get(User, NULL_PRIMARY_FOREIGN_MEMBER_ID)
            assert victim.organization_id is None
            assert victim.hashed_password == "victim-hash"
            assert await _membership_org_ids(db, victim.id) == {ORG_B}
            assert await _role_org_ids(db, victim.id) == set()


@pytest.mark.asyncio
async def test_orgadmin_reset_rejects_external_membership_and_role_affiliation(monkeypatch):
    async with _asgi_context(monkeypatch) as env:
        for user_id, original_hash in (
            (MULTI_ORG_USER_ID, "multi-org-hash"),
            (EXTERNAL_ROLE_USER_ID, "external-role-hash"),
        ):
            response = await env.client.put(
                f"/api/v1/users/{user_id}/password",
                json={"new_password": "TakenOver123!"},
                headers=_jwt(ORG_ADMIN_ID, ORG_A),
            )
            assert response.status_code == 403, response.text
            async with env.factory() as db:
                target = await db.get(User, user_id)
                assert target.organization_id == ORG_A
                assert target.hashed_password == original_hash

        async with env.factory() as db:
            assert await _membership_org_ids(db, MULTI_ORG_USER_ID) == {ORG_A, ORG_B}
            assert await _membership_org_ids(db, EXTERNAL_ROLE_USER_ID) == {ORG_A}
            assert await _role_org_ids(db, EXTERNAL_ROLE_USER_ID) == {ORG_B}


@pytest.mark.asyncio
async def test_orgadmin_positive_controls_and_superuser_reset_are_preserved(monkeypatch):
    async with _asgi_context(monkeypatch) as env:
        response = await env.client.post(
            f"/api/v1/organizations/{ORG_A}/members/{UNAFFILIATED_USER_ID}",
            headers=_jwt(ORG_ADMIN_ID, ORG_A),
        )
        assert response.status_code == 200, response.text
        async with env.factory() as db:
            added = await db.get(User, UNAFFILIATED_USER_ID)
            assert added.organization_id == ORG_A
            assert await _membership_org_ids(db, added.id) == {ORG_A}

        for user_id in (UNAFFILIATED_USER_ID, PURE_A_USER_ID):
            response = await env.client.put(
                f"/api/v1/users/{user_id}/password",
                json={"new_password": "Managed123!"},
                headers=_jwt(ORG_ADMIN_ID, ORG_A),
            )
            assert response.status_code == 200, response.text
            async with env.factory() as db:
                target = await db.get(User, user_id)
                assert auth_service.verify_password("Managed123!", target.hashed_password)

        response = await env.client.put(
            f"/api/v1/users/{MULTI_ORG_USER_ID}/password",
            json={"new_password": "PlatformManaged123!"},
            headers=_jwt(SUPERUSER_ID, ORG_A),
        )
        assert response.status_code == 200, response.text
        async with env.factory() as db:
            target = await db.get(User, MULTI_ORG_USER_ID)
            assert auth_service.verify_password("PlatformManaged123!", target.hashed_password)
            assert target.organization_id == ORG_A
            assert await _membership_org_ids(db, target.id) == {ORG_A, ORG_B}


@pytest.mark.asyncio
async def test_orgadmin_key_password_reset_uses_key_org_not_raw_primary(monkeypatch):
    async with _asgi_context(monkeypatch) as env:
        raw_key = await _create_key(env)
        response = await env.client.post(
            f"/api/v1/users/me/organizations/{ORG_B}/switch",
            headers=_jwt(KEY_USER_ID, ORG_A),
        )
        assert response.status_code == 200, response.text
        key_headers = _key_auth(raw_key)

        cross_org_response = await env.client.put(
            f"/api/v1/users/{PURE_B_USER_ID}/password",
            json={"new_password": "CrossOrg123!"},
            headers=key_headers,
        )
        key_org_response = await env.client.put(
            f"/api/v1/users/{PURE_A_USER_ID}/password",
            json={"new_password": "KeyOrg123!"},
            headers=key_headers,
        )
        assert (cross_org_response.status_code, key_org_response.status_code) == (403, 200), (
            f"cross-org={cross_org_response.text}; key-org={key_org_response.text}"
        )

        async with env.factory() as db:
            cross_org_target = await db.get(User, PURE_B_USER_ID)
            key_org_target = await db.get(User, PURE_A_USER_ID)
            assert cross_org_target.hashed_password == "pure-b-hash"
            assert auth_service.verify_password("KeyOrg123!", key_org_target.hashed_password)


@pytest.mark.asyncio
async def test_org_bound_key_is_stable_across_primary_org_changes(monkeypatch):
    from app.services.audit_service import audit_service
    from app.services.knowledge_service import knowledge_service

    delete_knowledge = AsyncMock(return_value=True)
    monkeypatch.setattr(knowledge_service, "delete_knowledge", delete_knowledge)
    monkeypatch.setattr(audit_service, "log_activity", AsyncMock())

    async with _asgi_context(monkeypatch) as env:
        raw_key = await _create_key(env)
        key_headers = _key_auth(raw_key)
        old_jwt_headers = _jwt(KEY_USER_ID, ORG_A)

        response = await env.client.get("/api/v1/documents/doc-a", headers=key_headers)
        assert response.status_code == 200, response.text
        response = await env.client.get("/api/v1/documents/doc-b", headers=key_headers)
        assert response.status_code == 403, response.text
        response = await env.client.get("/api/v1/knowledge/", headers=key_headers)
        assert _knowledge_ids(response) == {"doc-a"}

        response = await env.client.post(
            f"/api/v1/users/me/organizations/{ORG_B}/switch", headers=old_jwt_headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["organization_id"] == ORG_B

        # JWT remains primary-first even when an old token names A. Only the API key
        # receives hard-bound key-org resolution.
        response = await env.client.get("/api/v1/documents/doc-b", headers=old_jwt_headers)
        assert response.status_code == 200, response.text
        response = await env.client.get("/api/v1/documents/doc-a", headers=old_jwt_headers)
        assert response.status_code == 403, response.text
        response = await env.client.get("/api/v1/knowledge/", headers=old_jwt_headers)
        assert _knowledge_ids(response) == {"doc-b"}
        response = await env.client.get("/api/v1/knowledge/", headers=key_headers)
        assert _knowledge_ids(response) == {"doc-a"}

        response = await env.client.get("/api/v1/knowledge/jobs", headers=old_jwt_headers)
        assert response.status_code == 200, response.text
        assert {item["id"] for item in response.json()["data"]["data"]} == {999}
        response = await env.client.get(
            "/api/v1/monitoring/storage-analysis", headers=old_jwt_headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["total_used_size"] == 999

        response = await env.client.get("/api/v1/knowledge/jobs", headers=key_headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["total"] == 1
        assert {item["id"] for item in response.json()["data"]["data"]} == {101}
        assert "999" not in response.text
        response = await env.client.get("/api/v1/monitoring/storage-analysis", headers=key_headers)
        assert response.status_code == 200, response.text
        storage = response.json()["data"]
        assert storage["total_used_size"] == 111
        assert storage["total_count"] == 1
        assert storage["type_stats"] == [{"type": "txt", "size": 111}]
        assert "999" not in response.text

        response = await env.client.get("/api/v1/documents/doc-b", headers=key_headers)
        assert response.status_code == 403, response.text
        response = await env.client.delete("/api/v1/knowledge/document/doc-b", headers=key_headers)
        assert response.status_code == 403, response.text
        delete_knowledge.assert_not_awaited()

        response = await env.client.get("/api/v1/documents/doc-a", headers=key_headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["id"] == "doc-a"

        async with env.factory() as db:
            key_user = await db.get(User, KEY_USER_ID)
            key_user.organization_id = None
            await db.commit()

        response = await env.client.get("/api/v1/documents/doc-a", headers=key_headers)
        assert response.status_code == 200, response.text
        response = await env.client.get("/api/v1/documents/doc-b", headers=key_headers)
        assert response.status_code == 403, response.text
        response = await env.client.get("/api/v1/knowledge/", headers=key_headers)
        assert _knowledge_ids(response) == {"doc-a"}
        response = await env.client.get("/api/v1/knowledge/", headers=_jwt(KEY_USER_ID, None))
        assert response.status_code == 403, response.text
        # A NULL-primary JWT uses its validated token-org fallback. Documents from
        # the former org must not return merely because the same user uploaded them.
        response = await env.client.get("/api/v1/knowledge/", headers=_jwt(KEY_USER_ID, ORG_A))
        assert _knowledge_ids(response) == {"doc-a"}

        async with env.factory() as db:
            key_row = (
                await db.execute(select(ApiKey).where(ApiKey.user_id == KEY_USER_ID))
            ).scalar_one()
            assert key_row.org_id == ORG_A
            assert await _membership_org_ids(db, KEY_USER_ID) == {ORG_A, ORG_B}
            assert await db.get(Document, "doc-b") is not None


@pytest.mark.parametrize("primary_org", [ORG_A, ORG_B, None])
@pytest.mark.asyncio
async def test_org_bound_key_fails_after_membership_removal(monkeypatch, primary_org):
    async with _asgi_context(monkeypatch) as env:
        raw_key = await _create_key(env)
        async with env.factory() as db:
            await db.execute(
                user_organization.delete().where(
                    user_organization.c.user_id == KEY_USER_ID,
                    user_organization.c.organization_id == ORG_A,
                )
            )
            key_user = await db.get(User, KEY_USER_ID)
            key_user.organization_id = primary_org
            await db.commit()

        response = await env.client.get("/api/v1/documents/doc-a", headers=_key_auth(raw_key))
        assert response.status_code == 401, response.text
        assert response.json()["detail"] == "认证失败"
        async with env.factory() as db:
            key_row = (
                await db.execute(select(ApiKey).where(ApiKey.user_id == KEY_USER_ID))
            ).scalar_one()
            assert key_row.last_used_at is None


@pytest.mark.asyncio
async def test_platform_superuser_key_preserves_bypass_but_uses_key_org(monkeypatch):
    from app.core.security import get_user_org_id

    async with _asgi_context(monkeypatch) as env:
        raw_key = await _create_key(env, user_id=KEY_SUPERUSER_ID)
        response = await env.client.get("/api/v1/knowledge/", headers=_key_auth(raw_key))
        assert _knowledge_ids(response) == {"doc-a", "doc-b"}
        response = await env.client.get("/api/v1/knowledge/jobs", headers=_key_auth(raw_key))
        assert response.status_code == 200, response.text
        assert {item["id"] for item in response.json()["data"]["data"]} == {101, 999}
        response = await env.client.get(
            "/api/v1/monitoring/storage-analysis", headers=_key_auth(raw_key)
        )
        assert response.status_code == 200, response.text
        storage = response.json()["data"]
        assert storage["total_used_size"] == 1110
        assert storage["total_count"] == 2
        assert storage["type_stats"] == [{"type": "txt", "size": 1110}]

        async with env.factory() as db:
            # Existing explicit-superuser semantics: a non-null primary does not
            # require a membership row. Hard binding must not revoke that capability.
            assert await _membership_org_ids(db, KEY_SUPERUSER_ID) == set()
            authenticated = await api_key_service.authenticate(raw_key, db)
            assert authenticated is not None
            assert authenticated.is_superuser is True
            assert authenticated.organization_id == ORG_B
            assert authenticated.token_organization_id == ORG_A
            assert get_user_org_id(authenticated) == ORG_A

            # Do not silently expand the old bypass: the existing NULL-primary
            # fallback still requires the key-org membership row.
            key_superuser = await db.get(User, KEY_SUPERUSER_ID)
            key_superuser.organization_id = None
            await db.commit()

        async with env.factory() as db:
            assert await api_key_service.authenticate(raw_key, db) is None


@pytest.mark.asyncio
async def test_org_bound_key_settings_cannot_escape_key_org(monkeypatch):
    from app.services.audit_service import audit_service
    from app.services.organization_settings_service import organization_settings_service

    update_settings = AsyncMock(return_value=(None, {"retrieval_top_k": {"old": None, "new": 7}}))
    audit_log = AsyncMock()
    monkeypatch.setattr(organization_settings_service, "update_settings", update_settings)
    monkeypatch.setattr(audit_service, "log_activity", audit_log)

    async with _asgi_context(monkeypatch) as env:
        jwt_headers = _jwt(KEY_USER_ID, ORG_A)
        # JWT remains multi-org: primary A plus B membership and B OrgAdmin is enough.
        jwt_b_responses = await _settings_operations(env, ORG_B, jwt_headers)
        assert [response.status_code for response in jwt_b_responses] == [200, 200, 200]
        update_settings.reset_mock()
        audit_log.reset_mock()

        raw_key = await _create_key(env)
        response = await env.client.post(
            f"/api/v1/users/me/organizations/{ORG_B}/switch", headers=jwt_headers
        )
        assert response.status_code == 200, response.text
        key_headers = _key_auth(raw_key)

        bound_b_responses = await _settings_operations(env, ORG_B, key_headers)
        b_update_calls = update_settings.await_count
        b_audit_calls = audit_log.await_count
        bound_a_responses = await _settings_operations(env, ORG_A, key_headers)

        assert [response.status_code for response in bound_b_responses] == [403, 403, 403]
        assert [response.status_code for response in bound_a_responses] == [200, 200, 200]
        assert (b_update_calls, b_audit_calls) == (0, 0)
        assert update_settings.await_count == 1
        assert audit_log.await_count == 1


@pytest.mark.asyncio
async def test_unbound_key_and_superuser_settings_access_is_preserved(monkeypatch):
    from app.services.audit_service import audit_service
    from app.services.organization_settings_service import organization_settings_service

    update_settings = AsyncMock(return_value=(None, {"retrieval_top_k": {"old": None, "new": 7}}))
    audit_log = AsyncMock()
    monkeypatch.setattr(organization_settings_service, "update_settings", update_settings)
    monkeypatch.setattr(audit_service, "log_activity", audit_log)

    async with _asgi_context(monkeypatch) as env:
        raw_unbound_key = await _create_key(env)
        async with env.factory() as db:
            key_row = (
                await db.execute(select(ApiKey).where(ApiKey.user_id == KEY_USER_ID))
            ).scalar_one()
            key_row.org_id = None
            key_user = await db.get(User, KEY_USER_ID)
            key_user.organization_id = ORG_B
            await db.commit()

        unbound_responses = await _settings_operations(env, ORG_B, _key_auth(raw_unbound_key))
        assert [response.status_code for response in unbound_responses] == [200, 200, 200]
        assert update_settings.await_count == 1
        assert audit_log.await_count == 1

        raw_superuser_key = await _create_key(env, user_id=KEY_SUPERUSER_ID)
        superuser_responses = await _settings_operations(env, ORG_B, _key_auth(raw_superuser_key))
        assert [response.status_code for response in superuser_responses] == [200, 200, 200]
        assert update_settings.await_count == 2
        assert audit_log.await_count == 2
