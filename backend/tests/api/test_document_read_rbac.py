"""文档读取/下载的 DOCUMENT_READ RBAC 终局回归。

真实 FastAPI ASGI + 真实 ``auth_service`` / ``permission_service`` + 内存
SQLite；仅替身 Redis token 黑名单、MinIO 预签名、Elasticsearch 检索
（与 ``security_audit_document_read_20261027`` 同口径）。

核心断言：有效权限不含 ``document:read`` 的同组织 JWT / API Key 对
details、content、download-url、download 四个端点必须 403，且拒绝路径
MinIO 签名调用为 0；有权限、默认种子、匿名、跨组织、scope 不足、超管
旁路均保留既有语义。角色种子刻意最小化（授权用户只拿 document:read），
避免「全权限角色」把缺失依赖再次掩盖。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.core.database import Base, get_db
from app.core.elasticsearch import ElasticsearchTools
from app.core.minio_client import minio_client
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.organization import Organization, user_organization
from app.models.rbac import (
    Permission,
    PermissionType,
    Role,
    user_organization_role_association,
)
from app.models.user import User
from app.services.api_key_service import api_key_service
from app.services.auth_service import auth_service
from app.services.permission_service import permission_service

ORG_ID = 42
OTHER_ORG_ID = 99
DENIED_USER_ID = 101
ALLOWED_USER_ID = 102
OTHER_ORG_USER_ID = 103
SUPERUSER_USER_ID = 104
DEFAULT_USER_ID = 7

DOC_ID = "doc-rbac-1"
OTHER_ORG_DOC_ID = "doc-rbac-other-1"
CONTENT_TEXT = "RBAC read body"

DOC_PATH = f"/api/v1/documents/{DOC_ID}"
DOC_PATHS = (
    DOC_PATH,
    f"{DOC_PATH}/content",
    f"{DOC_PATH}/download-url",
    f"{DOC_PATH}/download",
)
OTHER_ORG_DOC_PATHS = tuple(path.replace(DOC_ID, OTHER_ORG_DOC_ID) for path in DOC_PATHS)


async def _new_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _user(user_id: int, username: str, org_id: int | None, *, is_superuser: bool = False):
    return User(
        id=user_id,
        username=username,
        email=f"{username}@example.invalid",
        hashed_password="x",
        is_active=True,
        organization_id=org_id,
        is_superuser=is_superuser,
    )


def _document(doc_id: str, org_id: int, uploader_id: int) -> Document:
    return Document(
        id=doc_id,
        filename="read.pdf",
        file_path=f"{org_id}/read.pdf",
        file_size=10,
        file_type=DocumentType.PDF,
        status=DocumentStatus.INDEXED,
        organization_id=org_id,
        uploaded_by=uploader_id,
        title="Read Doc",
    )


def _patch_external_io(monkeypatch, sign_calls: list, es_calls: list) -> None:
    """只隔离 I/O 替身；认证/RBAC/组织判定全部走真实代码。"""

    def fake_presign(object_name: str, expires):  # noqa: ANN001
        sign_calls.append((object_name, expires))
        return f"https://minio.test/{object_name}?X-Amz-Signature=fake"

    async def fake_search(query: dict):  # noqa: ANN001
        es_calls.append(query)
        return {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "chunk_text": CONTENT_TEXT,
                            "metadata": {"chunk_index": 0},
                        }
                    }
                ]
            }
        }

    async def no_blacklist(_token: str) -> bool:
        return False

    monkeypatch.setattr(minio_client, "get_presigned_url", fake_presign)
    monkeypatch.setattr(ElasticsearchTools, "search_documents", fake_search)
    monkeypatch.setattr(auth_service, "is_token_blacklisted", no_blacklist)


@asynccontextmanager
async def _asgi_client(factory, sign_calls: list, es_calls: list):
    from app.main import app

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://document-rbac.test",
        follow_redirects=False,
    ) as client:
        yield SimpleNamespace(
            client=client,
            factory=factory,
            sign_calls=sign_calls,
            es_calls=es_calls,
        )
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def env(monkeypatch):
    """受限种子：系统 User 角色刻意不含 document:read。

    授权用户只通过本组织的 ``DocReader`` 角色获得 ``document:read`` 一项，
    系统 User 角色另给一个无关权限（knowledge_base:search），避免空权限集
    让 403 的来源含混。
    """
    engine, factory = await _new_engine()
    async with factory() as session:
        unrelated = Permission(
            name=PermissionType.SEARCH_KNOWLEDGE_BASE.value, description="unrelated"
        )
        document_read = Permission(name=PermissionType.DOCUMENT_READ.value, description="read")
        session.add_all([unrelated, document_read])
        await session.flush()

        session.add_all(
            [
                Organization(id=ORG_ID, name="RBAC Org", owner_id=DENIED_USER_ID),
                Organization(id=OTHER_ORG_ID, name="Other Org", owner_id=OTHER_ORG_USER_ID),
                _user(DENIED_USER_ID, "rbac-denied", ORG_ID),
                _user(ALLOWED_USER_ID, "rbac-allowed", ORG_ID),
                _user(OTHER_ORG_USER_ID, "rbac-other-org", OTHER_ORG_ID),
                _user(SUPERUSER_USER_ID, "rbac-root", None, is_superuser=True),
            ]
        )
        await session.flush()

        system_user_role = Role(name="User", is_system_role=True, permissions=[unrelated])
        org_reader_role = Role(name="DocReader", is_system_role=False, permissions=[document_read])
        session.add_all([system_user_role, org_reader_role])
        await session.flush()

        session.add_all(
            [
                _document(DOC_ID, ORG_ID, DENIED_USER_ID),
                _document(OTHER_ORG_DOC_ID, OTHER_ORG_ID, OTHER_ORG_USER_ID),
                DocumentChunk(
                    id="chunk-rbac-1",
                    document_id=DOC_ID,
                    chunk_index=0,
                    chunk_text=CONTENT_TEXT,
                    chunk_length=len(CONTENT_TEXT),
                ),
            ]
        )
        await session.execute(
            user_organization.insert(),
            [
                {"user_id": DENIED_USER_ID, "organization_id": ORG_ID},
                {"user_id": ALLOWED_USER_ID, "organization_id": ORG_ID},
                {"user_id": OTHER_ORG_USER_ID, "organization_id": OTHER_ORG_ID},
            ],
        )
        await session.execute(
            user_organization_role_association.insert(),
            [
                {
                    "user_id": ALLOWED_USER_ID,
                    "organization_id": ORG_ID,
                    "role_id": org_reader_role.id,
                },
            ],
        )
        await session.commit()

    sign_calls: list = []
    es_calls: list = []
    _patch_external_io(monkeypatch, sign_calls, es_calls)
    async with _asgi_client(factory, sign_calls, es_calls) as env:
        yield env
    await engine.dispose()


@pytest.fixture
async def default_env(monkeypatch):
    """产品原生启动种子：系统 User 角色含 document:read（默认部署对照）。"""
    engine, factory = await _new_engine()
    async with factory() as session:
        session.add(Organization(id=ORG_ID, name="Default Org", owner_id=DEFAULT_USER_ID))
        session.add(_user(DEFAULT_USER_ID, "rbac-default", ORG_ID))
        await session.flush()
        await session.execute(
            user_organization.insert().values(user_id=DEFAULT_USER_ID, organization_id=ORG_ID)
        )
        session.add(_document(DOC_ID, ORG_ID, DEFAULT_USER_ID))
        await session.commit()
        # 真实种子函数（不是测试自造的「全权限角色」）。
        await permission_service.initialize_default_permissions_and_roles(session)

    sign_calls: list = []
    es_calls: list = []
    _patch_external_io(monkeypatch, sign_calls, es_calls)
    async with _asgi_client(factory, sign_calls, es_calls) as env:
        yield env
    await engine.dispose()


def _jwt(user_id: int) -> str:
    return auth_service.create_access_token({"user_id": user_id, "role": "user"})


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_key(env, user_id: int, scopes: list[str]) -> str:
    async with env.factory() as session:
        user = await session.get(User, user_id)
        _, raw = await api_key_service.create_key(
            session, user=user, name="document-rbac-key", scopes=list(scopes)
        )
        return raw


def _assert_missing_permission(response: httpx.Response, permission: str) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["message"] == f"用户无此权限: {permission}", response.text


def _assert_scope_denied(response: httpx.Response) -> None:
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error_code"] == "AUTHORIZATION_ERROR", response.text
    assert "scope" in body["message"], response.text


def _assert_no_signing_artifacts(response: httpx.Response) -> None:
    assert "downloadUrl" not in response.text
    assert "location" not in response.headers


# ── 负向：角色无 document:read ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_jwt_role_without_document_read_denied_on_all_four_without_signing(env):
    headers = _auth(_jwt(DENIED_USER_ID))

    for path in DOC_PATHS:
        response = await env.client.get(path, headers=headers)
        _assert_missing_permission(response, PermissionType.DOCUMENT_READ.value)
        _assert_no_signing_artifacts(response)

    # 拒绝发生在 handler 之前：MinIO 签名与 ES 检索都是 0 次。
    assert env.sign_calls == []
    assert env.es_calls == []

    # 同主体的知识库列表 / 上传仍然是 403（RBAC 管线本身工作正常，
    # 四个文档读端点的 403 不是「权限解析整体坏掉」的假象）。
    _assert_missing_permission(
        await env.client.get("/api/v1/knowledge/", headers=headers),
        PermissionType.VIEW_KNOWLEDGE_BASE.value,
    )
    _assert_missing_permission(
        await env.client.post(
            "/api/v1/documents/upload",
            files={"file": ("rbac.txt", b"hello", "text/plain")},
            headers=headers,
        ),
        PermissionType.DOCUMENT_UPLOAD.value,
    )
    assert env.sign_calls == []


@pytest.mark.asyncio
async def test_knowledge_read_api_key_without_document_role_denied_on_all_four(env):
    """API Key：scope 通过后仍必须受用户角色 RBAC 约束（审计中的绕过通道）。"""
    raw = await _make_key(env, DENIED_USER_ID, ["knowledge:read"])
    headers = _auth(raw)

    for path in DOC_PATHS:
        response = await env.client.get(path, headers=headers)
        _assert_missing_permission(response, PermissionType.DOCUMENT_READ.value)
        _assert_no_signing_artifacts(response)

    assert env.sign_calls == []


@pytest.mark.asyncio
async def test_scope_denial_precedes_rbac_and_never_signs(env):
    """rpc-only key：即使角色有 document:read，也先被 scope 拒绝且不签名。"""
    raw = await _make_key(env, ALLOWED_USER_ID, ["rpc"])
    headers = _auth(raw)

    for path in DOC_PATHS:
        _assert_scope_denied(await env.client.get(path, headers=headers))

    assert env.sign_calls == []


@pytest.mark.asyncio
async def test_anonymous_denied_on_all_four_without_signing(env):
    for path in DOC_PATHS:
        response = await env.client.get(path)
        assert response.status_code == 401, response.text
        _assert_no_signing_artifacts(response)

    assert env.sign_calls == []


@pytest.mark.asyncio
async def test_cross_org_still_denied_after_rbac_allows(env):
    """RBAC 放行后组织归属仍必须拒绝（跨 org 403、不签名）。"""
    headers = _auth(_jwt(ALLOWED_USER_ID))

    for path in OTHER_ORG_DOC_PATHS:
        response = await env.client.get(path, headers=headers)
        assert response.status_code == 403, response.text
        assert response.json()["error_code"] == "AUTHORIZATION_ERROR", response.text
        _assert_no_signing_artifacts(response)

    assert env.sign_calls == []


# ── 正向对照：有权限 / 默认种子 / 超管 ─────────────────────────────────────


async def _assert_all_four_readable(env, headers: dict[str, str]) -> None:
    detail = await env.client.get(DOC_PATH, headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["id"] == DOC_ID

    content = await env.client.get(f"{DOC_PATH}/content", headers=headers)
    assert content.status_code == 200, content.text
    assert CONTENT_TEXT in content.json()["data"]["content"]

    url_response = await env.client.get(f"{DOC_PATH}/download-url", headers=headers)
    assert url_response.status_code == 200, url_response.text
    download_url = url_response.json()["data"]["downloadUrl"]
    assert download_url.startswith(f"https://minio.test/{ORG_ID}/read.pdf?")
    assert url_response.json()["data"]["expiresIn"] == 3600

    legacy = await env.client.get(f"{DOC_PATH}/download", headers=headers)
    assert legacy.status_code == 307, legacy.text
    assert legacy.headers["location"] == download_url

    # 只有两个下载端点触发签名；详情/全文不签名。
    assert len(env.sign_calls) == 2
    assert {call[0] for call in env.sign_calls} == {f"{ORG_ID}/read.pdf"}


@pytest.mark.asyncio
async def test_jwt_role_with_document_read_can_read_all_four(env):
    await _assert_all_four_readable(env, _auth(_jwt(ALLOWED_USER_ID)))


@pytest.mark.asyncio
async def test_default_seed_user_can_read_all_four(default_env):
    """原生种子（系统 User 含 document:read）下行为中性：仍全部可读。"""
    await _assert_all_four_readable(default_env, _auth(_jwt(DEFAULT_USER_ID)))


@pytest.mark.asyncio
async def test_knowledge_read_api_key_with_document_role_can_read(env):
    raw = await _make_key(env, ALLOWED_USER_ID, ["knowledge:read"])
    await _assert_all_four_readable(env, _auth(raw))


@pytest.mark.asyncio
async def test_superuser_bypass_is_preserved(env):
    """显式超管无组织也保留全局旁路：可读任意组织文档并正常签名。"""
    headers = _auth(_jwt(SUPERUSER_USER_ID))

    detail = await env.client.get(DOC_PATH, headers=headers)
    assert detail.status_code == 200, detail.text

    url_response = await env.client.get(f"{DOC_PATH}/download-url", headers=headers)
    assert url_response.status_code == 200, url_response.text

    legacy = await env.client.get(f"{DOC_PATH}/download", headers=headers)
    assert legacy.status_code == 307, legacy.text
    assert legacy.headers["location"] == url_response.json()["data"]["downloadUrl"]
    assert len(env.sign_calls) == 2
