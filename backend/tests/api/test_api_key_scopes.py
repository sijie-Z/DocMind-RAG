"""API Key scope 强制（issue #496）。

真实路由 + 真实 ``auth_service.get_current_user`` + sqlite 全链（同
``test_api_keys.py`` 的口径），只对 Redis 黑名单检查用替身。
RBAC 用真实 Role/Permission 行种子（User 系统角色持有全部权限），
让 403 的来源可判定为 scope 缺失而不是 RBAC 缺失。

矩阵（任务书 §1）：
- rpc-only key → chat / agent sessions / api-keys 一律 403；
- chat:read key → 可读会话列表；POST 聊天（chat:write）403；
- knowledge:read key → 可读知识库/文档；上传（knowledge:write）403；
- knowledge:write-only key → 读知识库/文档 403（读写分离）；
- ``/api-keys`` 对任何 ``dm_`` key 一律 403（即使 scope 齐全）；
- JWT 用户全部 200（行为与修复前一致）。
"""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.core.database import Base, get_db
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.organization import Organization, user_organization
from app.models.rbac import Permission, PermissionType, Role
from app.models.user import User
from app.services.api_key_service import DEFAULT_SCOPES, api_key_service
from app.services.auth_service import auth_service

ORG_ID = 42
USER_ID = 7
DOC_ID = "doc-scope-1"

CHAT_LIST = "/api/v1/chat/conversations"
AGENT_SESSIONS = "/api/v1/agent/sessions"
API_KEYS = "/api/v1/api-keys"
KNOWLEDGE_STATS = "/api/v1/knowledge/rag-stats"
KNOWLEDGE_LIST = "/api/v1/knowledge/"
DOCUMENT = f"/api/v1/documents/{DOC_ID}"
DOCUMENT_UPLOAD = "/api/v1/documents/upload"

# issue #496 已加 scope 的 GET 端点全集：rpc-only key 对每一个都必须 403。
SCOPED_GET_PATHS = [
    CHAT_LIST,
    f"{CHAT_LIST}/nope",
    AGENT_SESSIONS,
    f"{AGENT_SESSIONS}/nope",
    "/api/v1/knowledge/",
    f"/api/v1/knowledge/suggestions?q=scope&organization_id={ORG_ID}",
    f"/api/v1/knowledge/stats/{ORG_ID}",
    "/api/v1/knowledge/graph/stats",
    "/api/v1/knowledge/jobs",
    f"/api/v1/knowledge/{DOC_ID}/events",
    "/api/v1/knowledge/health",
    KNOWLEDGE_STATS,
    "/api/v1/knowledge/graph",
    DOCUMENT,
    f"{DOCUMENT}/content",
    f"{DOCUMENT}/chunk?chunk_id=chunk-scope",
    f"{DOCUMENT}/download-url",
    f"{DOCUMENT}/download",
]


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
        session.add(Organization(id=ORG_ID, name="Scope Org", owner_id=USER_ID))
        session.add(
            User(
                id=USER_ID,
                username="scopeuser",
                email="scopeuser@example.com",
                hashed_password="x",
                is_active=True,
                organization_id=ORG_ID,
            )
        )
        # 系统 User 角色持有全部权限：scope 拒绝才是 403 的唯一来源。
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
                title="Scope Doc",
            )
        )
        await session.execute(
            user_organization.insert().values(user_id=USER_ID, organization_id=ORG_ID)
        )
        await session.commit()

    async def _override_db():
        async with factory() as session:
            yield session

    async def _no_blacklist(token):  # noqa: ANN001
        return False

    monkeypatch.setattr(auth_service, "is_token_blacklisted", _no_blacklist)

    from app.main import app

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://scopetest") as client:
        yield SimpleNamespace(client=client, factory=factory)
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


def _jwt() -> str:
    return auth_service.create_access_token({"user_id": USER_ID, "role": "user"})


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _make_key(env, scopes: list[str]) -> str:
    async with env.factory() as session:
        user = await session.get(User, USER_ID)
        _, raw = await api_key_service.create_key(
            session, user=user, name="scope-key", scopes=list(scopes)
        )
        return raw


def _assert_scope_denied(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "AUTHORIZATION_ERROR", response.text


# ── rpc-only key：chat / agent / api-keys 全部拒绝 ──────────────────────────


@pytest.mark.asyncio
async def test_rpc_only_key_cannot_touch_chat_agent_or_api_keys(env):
    raw = await _make_key(env, ["rpc"])
    headers = _auth(raw)

    paths = (
        CHAT_LIST,
        f"{CHAT_LIST}/nope",
        AGENT_SESSIONS,
        f"{AGENT_SESSIONS}/nope",
        API_KEYS,
    )
    for path in paths:
        _assert_scope_denied(await env.client.get(path, headers=headers))

    # 写路径同样拒绝（POST 聊天需要 chat:write）。
    _assert_scope_denied(
        await env.client.post("/api/v1/agent/chat", json={"query": "hi"}, headers=headers)
    )
    _assert_scope_denied(
        await env.client.post("/api/v1/chat/stream", json={"content": "hi"}, headers=headers)
    )


# ── 全部已加 scope 的 GET：rpc-only key 逐一 403（删任一依赖都会变红） ──────


@pytest.mark.parametrize("path", SCOPED_GET_PATHS)
@pytest.mark.asyncio
async def test_every_scoped_get_endpoint_rejects_rpc_only_key(env, path):
    raw = await _make_key(env, ["rpc"])
    _assert_scope_denied(await env.client.get(path, headers=_auth(raw)))


CHUNK_LIST = f"/api/v1/documents/{DOC_ID}/chunks"
CHUNK_OVERRIDE = f"/api/v1/documents/{DOC_ID}/chunks/override"
CHUNK_ENABLED = f"/api/v1/documents/{DOC_ID}/chunks/enabled"
# 每个写端点单独参数化：移除任一端点上的 require_scope("knowledge:write")
# 时，对应那条用例必须独立变红（而不是被其它端点掩盖）。
CHUNK_WRITE_CALLS = (
    (
        "override",
        "put",
        CHUNK_OVERRIDE,
        {"chunk_id": "chunk-scope", "text": "x", "apply_to_retrieval": False},
    ),
    ("enabled", "patch", CHUNK_ENABLED, {"chunk_id": "chunk-scope", "enabled": False}),
    ("reset", "delete", CHUNK_OVERRIDE, None),
)


@pytest.mark.asyncio
async def test_chunk_browser_endpoints_require_knowledge_read_scope(env):
    """GET chunk 列表要求 knowledge:read；rpc-only / write-only key 均不得读。"""
    for scopes in (["rpc"], ["knowledge:write"]):
        raw = await _make_key(env, scopes)
        _assert_scope_denied(await env.client.get(CHUNK_LIST, headers=_auth(raw)))


@pytest.mark.asyncio
@pytest.mark.parametrize("name,method,path,body", CHUNK_WRITE_CALLS)
async def test_chunk_write_endpoints_require_knowledge_write_scope(env, name, method, path, body):
    """三条 chunk 写端点逐条验证 scope；rpc-only / read-only key 一律 403。"""
    for scopes in (["rpc"], ["knowledge:read"]):
        raw = await _make_key(env, scopes)
        client = env.client
        if method == "delete":
            response = await client.delete(
                path, params={"chunk_id": "chunk-scope"}, headers=_auth(raw)
            )
        else:
            response = await getattr(client, method)(path, json=body, headers=_auth(raw))
        _assert_scope_denied(response)


@pytest.mark.asyncio
async def test_chunk_browser_scopes_accept_matching_keys(env):
    """正向对照：读 key 能列、写 key 能写（错误只来自 scope，不是 RBAC 缺失）。"""
    reader = await _make_key(env, ["knowledge:read"])
    writer = await _make_key(env, ["knowledge:write"])
    assert (await env.client.get(CHUNK_LIST, headers=_auth(reader))).status_code == 200
    write_response = await env.client.patch(
        CHUNK_ENABLED,
        json={"chunk_id": "chunk-scope", "enabled": False},
        headers=_auth(writer),
    )
    assert write_response.status_code in (200, 404), write_response.text


# ── 合同审查模板库：写端点逐条要求 knowledge:write ─────────────────────────
TEMPLATE_LIST = "/api/v1/reviews/templates"
TEMPLATE_CREATE_CALLS = (
    ("create", "post", TEMPLATE_LIST, {"name": "scope-template"}),
    (
        "bootstrap",
        "post",
        f"{TEMPLATE_LIST}/bootstrap",
        {"name": "scope-bootstrap"},
    ),
)


@pytest.mark.asyncio
async def test_template_list_requires_knowledge_read_scope(env):
    """模板列表读要求 knowledge:read；rpc-only / write-only key 都不得读。"""
    for scopes in (["rpc"], ["knowledge:write"]):
        raw = await _make_key(env, scopes)
        _assert_scope_denied(await env.client.get(TEMPLATE_LIST, headers=_auth(raw)))


@pytest.mark.asyncio
@pytest.mark.parametrize("name,method,path,body", TEMPLATE_CREATE_CALLS)
async def test_template_write_endpoints_require_knowledge_write_scope(
    env, name, method, path, body
):
    """模板写端点按端点验证 knowledge:write；rpc-only / read-only key 一律 403。

    变异点：删除 create_review_template / bootstrap_review_template 的
    `_WRITE_DEPS` 中 require_scope("knowledge:write") → 对应参数化用例红。
    """
    for scopes in (["rpc"], ["knowledge:read"]):
        raw = await _make_key(env, scopes)
        response = await getattr(env.client, method)(path, json=body, headers=_auth(raw))
        _assert_scope_denied(response)


# ── chat:read：可读会话，不可写 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_read_key_can_read_sessions_but_not_chat(env):
    raw = await _make_key(env, ["chat:read"])
    headers = _auth(raw)

    r = await env.client.get(CHAT_LIST, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    r = await env.client.get(AGENT_SESSIONS, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    # 按 id 读取：scope 放行后进入 handler（chat 端点软失败 200 / agent 端点 404），
    # 证明 403 只在 scope 缺失时发生。
    r = await env.client.get(f"{CHAT_LIST}/nope", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is False
    r = await env.client.get(f"{AGENT_SESSIONS}/nope", headers=headers)
    assert r.status_code == 404, r.text

    _assert_scope_denied(
        await env.client.post("/api/v1/agent/chat", json={"query": "hi"}, headers=headers)
    )
    _assert_scope_denied(
        await env.client.post("/api/v1/chat/stream", json={"content": "hi"}, headers=headers)
    )


# ── knowledge:read：可读知识库/文档，不可上传 ──────────────────────────────


@pytest.mark.asyncio
async def test_knowledge_read_key_can_read_but_not_upload(env):
    raw = await _make_key(env, ["knowledge:read"])
    headers = _auth(raw)

    r = await env.client.get(KNOWLEDGE_STATS, headers=headers)
    assert r.status_code == 200, r.text

    r = await env.client.get(KNOWLEDGE_LIST, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True

    r = await env.client.get(DOCUMENT, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["id"] == DOC_ID

    _assert_scope_denied(
        await env.client.post(
            DOCUMENT_UPLOAD,
            files={"file": ("scope.txt", b"hello", "text/plain")},
            headers=headers,
        )
    )


@pytest.mark.asyncio
async def test_knowledge_write_key_cannot_read(env):
    raw = await _make_key(env, ["knowledge:write"])
    headers = _auth(raw)

    _assert_scope_denied(await env.client.get(KNOWLEDGE_STATS, headers=headers))
    _assert_scope_denied(await env.client.get(KNOWLEDGE_LIST, headers=headers))
    _assert_scope_denied(await env.client.get(DOCUMENT, headers=headers))


@pytest.mark.asyncio
async def test_knowledge_write_scope_is_issuable_via_http_and_upload_passes_gate(env, monkeypatch):
    """B1 回归：knowledge:write 必须能通过公开 API 签发，且该 key 能过 upload 门禁。

    外部依赖用替身：MinIO stat 直接命中（跳过上传）、Kafka 投递确认（不调度本地
    处理）——本用例只验证 scope 签发与门禁顺序，不触及真实存储。
    """
    from app.api.v1.endpoints import documents as documents_module

    monkeypatch.setattr(documents_module.minio_client, "stat_object", lambda *a, **k: True)

    async def _send(*args, **kwargs):  # noqa: ANN002, ANN003
        return True

    monkeypatch.setattr(documents_module.kafka_producer, "send_message", _send)

    r = await env.client.post(
        API_KEYS,
        json={"name": "writer", "scopes": ["knowledge:write"]},
        headers=_auth(_jwt()),
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["scopes"] == ["knowledge:write"]
    raw = data["api_key"]

    up = await env.client.post(
        DOCUMENT_UPLOAD,
        files={"file": ("writer.txt", b"hello", "text/plain")},
        headers=_auth(raw),
    )
    assert up.status_code == 201, up.text
    assert up.json()["success"] is True

    # 默认签发集合不变：不显式请求就绝不带 knowledge:write。
    r2 = await env.client.post(API_KEYS, json={"name": "default"}, headers=_auth(_jwt()))
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["scopes"] == list(DEFAULT_SCOPES)


# ── /api-keys：API Key 一律 403，JWT 正常 ───────────────────────────────────


@pytest.mark.asyncio
async def test_api_key_management_rejects_every_api_key(env):
    raw = await _make_key(env, list(DEFAULT_SCOPES))
    headers = _auth(raw)

    _assert_scope_denied(await env.client.get(API_KEYS, headers=headers))
    _assert_scope_denied(await env.client.post(API_KEYS, json={"name": "新 key"}, headers=headers))
    _assert_scope_denied(await env.client.delete(f"{API_KEYS}/whatever", headers=headers))

    jwt_headers = _auth(_jwt())
    r = await env.client.get(API_KEYS, headers=jwt_headers)
    assert r.status_code == 200, r.text


# ── JWT：行为与修复前一致（全部 200） ──────────────────────────────────────


@pytest.mark.asyncio
async def test_jwt_user_keeps_full_access(env):
    headers = _auth(_jwt())

    for path in (CHAT_LIST, AGENT_SESSIONS, API_KEYS, KNOWLEDGE_STATS, KNOWLEDGE_LIST, DOCUMENT):
        r = await env.client.get(path, headers=headers)
        assert r.status_code == 200, f"{path}: {r.text}"
