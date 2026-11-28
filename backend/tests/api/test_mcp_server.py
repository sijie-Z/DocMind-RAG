"""批次十六：入站 MCP Server（``/api/v1/mcp/server``，只读知识库）。

验收矩阵（与任务书/计划对应）：

- 标准 ``mcp`` SDK 客户端完成 initialize / tools/list / tools/call；
- 认证：无 key / 坏 key / 吊销 key / JWT → 401；缺 ``knowledge:read`` → 403；
  org-unbound key → 403；``MCP_SERVER_ENABLED=false`` → 404；
- 组织边界：检索只返回 key 绑定组织的文档；get_document 跨组织统一 404；
- 私有 ACL：私有文档不进 search；他人私有 get_document 统一 404；
  上传者本人/显式授权用户可读；
- 治理：per-key 限流 429 + Retry-After；top_k / max_chars 上限；
  审计只记 tool/org/result_count/latency/outcome（无正文、无原始 key）；
- lifespan：Session Manager 进入/退出干净，bootstrap lifespan 接线正确。

变异判别力（任一被删则对应用例红）：
- 去掉 dm_ key 校验 → 401 用例红；
- 去掉 knowledge:read scope → 403 用例红；
- 去掉 key-bound org（回退 primary org）→ org-unbound 403 / 跨组织用例红；
- 去掉 viewer_user_id / viewer ACL → 私有文档用例红；
- 去掉 MCP_SERVER_ENABLED 开关 → 404 用例红；
- 去掉 per-key 限流 → 429 用例红；
- 去掉 top_k / max_chars 钳制 → 上限用例红；
- 去掉审计 → 审计用例红。
"""

from __future__ import annotations

import io
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.api.v1.endpoints import mcp as mcp_endpoint
from app.core.config import settings
from app.core.database import Base
from app.mcp_server import auth as mcp_auth
from app.mcp_server import server as mcp_server_module
from app.mcp_server import tools as mcp_tools
from app.models.api_key import ApiKey
from app.models.document import (
    Document,
    DocumentAcl,
    DocumentChunk,
    DocumentStatus,
    DocumentType,
    DocumentVisibility,
)
from app.models.organization import Organization, user_organization
from app.models.user import User
from app.services.api_key_service import api_key_service
from app.services.auth_service import auth_service

BASE_URL = "http://testserver"
MCP_URL = f"{BASE_URL}/api/v1/mcp/server"

ORG_ID = 42
OTHER_ORG_ID = 43
USER_ID = 7
OTHER_ORG_USER_ID = 8
NO_ORG_USER_ID = 9
PEER_USER_ID = 10
UNBOUND_KEY_USER_ID = 11
SUPERUSER_USER_ID = 12

DOC_PUBLIC = "doc-public"
DOC_PRIVATE_OWN = "doc-private-own"
DOC_PRIVATE_OTHER = "doc-private-other"
DOC_PRIVATE_GRANTED = "doc-private-granted"
DOC_OTHER_ORG = "doc-other-org"

PUBLIC_TEXT = "公开正文内容。" * 20
OWN_TEXT = "本人私有正文。" * 5
GRANTED_TEXT = "授权可见正文。" * 5

INIT_REQUEST = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "1"},
    },
}


class FakeAudit:
    """记录审计调用；不写真实 DB。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def log_activity(self, **kwargs) -> None:
        self.calls.append(kwargs)


class FakeKnowledge:
    """按组织 + viewer ACL 模拟检索层，并记录调用参数。"""

    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.calls: list[dict] = []
        self.error: Exception | None = None

    async def search_knowledge_with_meta(
        self,
        *,
        query,
        organization_id,
        top_k=5,
        search_mode=None,
        document_ids=None,
        viewer_user_id=None,
        strict=False,
    ):
        self.calls.append(
            {
                "query": query,
                "organization_id": organization_id,
                "top_k": top_k,
                "search_mode": search_mode,
                "document_ids": document_ids,
                "viewer_user_id": viewer_user_id,
                "strict": strict,
            }
        )
        if self.error is not None:
            raise self.error

        results = []
        for document in self.documents:
            if document["organization_id"] != organization_id:
                continue
            if document_ids and document["id"] not in document_ids:
                continue
            if document["visibility"] == "private" and viewer_user_id is not None:
                granted = viewer_user_id in document.get("granted_user_ids", set())
                if document["uploaded_by"] != viewer_user_id and not granted:
                    continue
            results.append(
                {
                    "id": document["id"],
                    "text": document["text"],
                    "content": document["text"],
                    "score": 0.9,
                    "source": {"filename": document["filename"]},
                    "metadata": {"filename": document["filename"]},
                }
            )
        return results[:top_k], None


def _tool_text(result) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


def _tool_json(result) -> dict:
    return json.loads(_tool_text(result))


@asynccontextmanager
async def _sdk_session(env, raw_key: str):
    """标准 MCP SDK 客户端（在测试任务内进出 Session Manager）。"""
    headers = {"Authorization": f"Bearer {raw_key}"}
    async with env.inbound.session_lifespan():
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=env.app),
            base_url=BASE_URL,
            headers=headers,
        ) as http_client:
            async with streamable_http_client(MCP_URL, http_client=http_client) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session


@asynccontextmanager
async def _served(env):
    """只跑 Session Manager，供不经过 SDK 客户端的原始 HTTP 用例使用。"""
    async with env.inbound.session_lifespan():
        yield env


async def _raw_post(app, *, headers=None, body=None, path="/api/v1/mcp/server"):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.post(path, json=body or INIT_REQUEST, headers=headers or {})


async def _raw_get(app, *, headers=None, path="/api/v1/mcp/server"):
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        return await client.get(path, headers=headers or {})


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

    async with factory() as session:
        session.add_all(
            [
                Organization(id=ORG_ID, name="Org Own", owner_id=USER_ID),
                Organization(id=OTHER_ORG_ID, name="Org Other", owner_id=OTHER_ORG_USER_ID),
                User(
                    id=USER_ID,
                    username="mcp-owner",
                    email="mcp-owner@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=ORG_ID,
                ),
                User(
                    id=OTHER_ORG_USER_ID,
                    username="mcp-other",
                    email="mcp-other@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=OTHER_ORG_ID,
                ),
                User(
                    id=NO_ORG_USER_ID,
                    username="mcp-no-org",
                    email="mcp-no-org@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=None,
                ),
                User(
                    id=PEER_USER_ID,
                    username="mcp-peer",
                    email="mcp-peer@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=ORG_ID,
                ),
                User(
                    id=UNBOUND_KEY_USER_ID,
                    username="mcp-unbound-key",
                    email="mcp-unbound-key@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=ORG_ID,
                ),
                User(
                    id=SUPERUSER_USER_ID,
                    username="mcp-superuser",
                    email="mcp-superuser@example.com",
                    hashed_password="x",
                    is_active=True,
                    is_superuser=True,
                    organization_id=ORG_ID,
                ),
                Document(
                    id=DOC_PUBLIC,
                    filename="public.pdf",
                    file_path="42/public.pdf",
                    file_size=1024,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG_ID,
                    uploaded_by=USER_ID,
                    visibility=DocumentVisibility.ORGANIZATION,
                ),
                Document(
                    id=DOC_PRIVATE_OWN,
                    filename="private-own.pdf",
                    file_path="42/private-own.pdf",
                    file_size=1024,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG_ID,
                    uploaded_by=USER_ID,
                    visibility=DocumentVisibility.PRIVATE,
                ),
                Document(
                    id=DOC_PRIVATE_OTHER,
                    filename="private-other.pdf",
                    file_path="42/private-other.pdf",
                    file_size=1024,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG_ID,
                    uploaded_by=PEER_USER_ID,
                    visibility=DocumentVisibility.PRIVATE,
                ),
                Document(
                    id=DOC_PRIVATE_GRANTED,
                    filename="private-granted.pdf",
                    file_path="42/private-granted.pdf",
                    file_size=1024,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG_ID,
                    uploaded_by=PEER_USER_ID,
                    visibility=DocumentVisibility.PRIVATE,
                ),
                Document(
                    id=DOC_OTHER_ORG,
                    filename="other-org.pdf",
                    file_path="43/other-org.pdf",
                    file_size=1024,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=OTHER_ORG_ID,
                    uploaded_by=OTHER_ORG_USER_ID,
                    visibility=DocumentVisibility.ORGANIZATION,
                ),
                DocumentChunk(
                    id="chunk-public",
                    document_id=DOC_PUBLIC,
                    chunk_index=0,
                    chunk_text="legacy/public chunk text",
                    original_text=PUBLIC_TEXT,
                    chunk_length=len(PUBLIC_TEXT),
                    meta_data={"text_provenance": "parser_original"},
                ),
                DocumentChunk(
                    id="chunk-private-own",
                    document_id=DOC_PRIVATE_OWN,
                    chunk_index=0,
                    chunk_text="legacy/private own chunk text",
                    original_text=OWN_TEXT,
                    chunk_length=len(OWN_TEXT),
                    meta_data={"text_provenance": "parser_original"},
                ),
                DocumentChunk(
                    id="chunk-private-granted",
                    document_id=DOC_PRIVATE_GRANTED,
                    chunk_index=0,
                    chunk_text="legacy/granted chunk text",
                    original_text=GRANTED_TEXT,
                    chunk_length=len(GRANTED_TEXT),
                    meta_data={"text_provenance": "parser_original"},
                ),
                DocumentAcl(
                    id="acl-granted",
                    document_id=DOC_PRIVATE_GRANTED,
                    subject_type="user",
                    subject_id=USER_ID,
                ),
            ]
        )
        await session.execute(
            user_organization.insert(),
            [
                {"user_id": USER_ID, "organization_id": ORG_ID},
                # 同一用户也可以是第二个组织的成员：用于证明 MCP 只认 key 绑定组织。
                {"user_id": USER_ID, "organization_id": OTHER_ORG_ID},
                {"user_id": OTHER_ORG_USER_ID, "organization_id": OTHER_ORG_ID},
                {"user_id": PEER_USER_ID, "organization_id": ORG_ID},
                {"user_id": UNBOUND_KEY_USER_ID, "organization_id": ORG_ID},
                {"user_id": SUPERUSER_USER_ID, "organization_id": ORG_ID},
            ],
        )
        await session.commit()

    documents = [
        {
            "id": DOC_PUBLIC,
            "filename": "public.pdf",
            "organization_id": ORG_ID,
            "visibility": "organization",
            "uploaded_by": USER_ID,
            "text": PUBLIC_TEXT,
        },
        {
            "id": DOC_PRIVATE_OWN,
            "filename": "private-own.pdf",
            "organization_id": ORG_ID,
            "visibility": "private",
            "uploaded_by": USER_ID,
            "text": OWN_TEXT,
        },
        {
            "id": DOC_PRIVATE_OTHER,
            "filename": "private-other.pdf",
            "organization_id": ORG_ID,
            "visibility": "private",
            "uploaded_by": PEER_USER_ID,
            "text": "他人私有正文。",
        },
        {
            "id": DOC_PRIVATE_GRANTED,
            "filename": "private-granted.pdf",
            "organization_id": ORG_ID,
            "visibility": "private",
            "uploaded_by": PEER_USER_ID,
            "granted_user_ids": {USER_ID},
            "text": GRANTED_TEXT,
        },
        {
            "id": DOC_OTHER_ORG,
            "filename": "other-org.pdf",
            "organization_id": OTHER_ORG_ID,
            "visibility": "organization",
            "uploaded_by": OTHER_ORG_USER_ID,
            "text": "他组织正文。",
        },
    ]
    fake_knowledge = FakeKnowledge(documents)
    fake_audit = FakeAudit()

    monkeypatch.setattr(mcp_tools, "knowledge_service", fake_knowledge)
    monkeypatch.setattr(mcp_tools, "audit_service", fake_audit)
    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", True)
    monkeypatch.setattr(settings, "MCP_SERVER_RATE_LIMIT_PER_MINUTE", 1000)
    monkeypatch.setattr(settings, "MCP_SERVER_MAX_TOP_K", 5)
    monkeypatch.setattr(settings, "MCP_SERVER_MAX_CHARS", 20_000)

    inbound = mcp_server_module.create_inbound_mcp_server(session_factory=factory)
    monkeypatch.setattr(mcp_server_module, "get_inbound_mcp_server", lambda: inbound)

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(mcp_endpoint.router, prefix="/api/v1/mcp")

    async def make_key(
        *,
        scopes=("knowledge:read",),
        user_id=USER_ID,
        org_id=None,
    ) -> str:
        async with factory() as session:
            user = await session.get(User, user_id)
            _, raw = await api_key_service.create_key(
                session,
                user=user,
                name="mcp-test",
                scopes=list(scopes),
                org_id=org_id,
            )
            return raw

    yield SimpleNamespace(
        app=test_app,
        inbound=inbound,
        factory=factory,
        make_key=make_key,
        knowledge=fake_knowledge,
        audit=fake_audit,
    )

    await engine.dispose()


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


# ── SDK 客户端主链路 ────────────────────────────────────────────────


async def test_sdk_initialize_and_tools_list(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        init = await session.initialize()
        assert init.server_info.name == "docmind-knowledge"
        tools = await session.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "search_knowledge_base",
            "get_document",
        }
        for tool in tools.tools:
            assert tool.description
            assert tool.input_schema["type"] == "object"


async def test_transport_is_stateless_json_response(env):
    """M1 硬性配置：POST 返回 application/json，且不产生 mcp-session-id。"""
    raw = await env.make_key()
    async with _served(env):
        first = await _raw_post(env.app, headers=_auth(raw))
        second = await _raw_post(env.app, headers=_auth(raw))
    for response in (first, second):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")
        assert "mcp-session-id" not in response.headers


async def test_sdk_search_passes_viewer_and_clamps_top_k(env, monkeypatch):
    monkeypatch.setattr(settings, "MCP_SERVER_MAX_TOP_K", 3)
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool(
            "search_knowledge_base",
            {"query": "合同", "top_k": 99},
        )
    assert result.is_error is False
    payload = _tool_json(result)
    assert payload["count"] <= 3
    call = env.knowledge.calls[-1]
    assert call["top_k"] == 3
    assert call["organization_id"] == ORG_ID
    assert call["viewer_user_id"] == USER_ID
    assert call["search_mode"] == "hybrid"
    assert call["strict"] is True


async def test_search_excludes_private_peer_docs_and_other_org(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool("search_knowledge_base", {"query": "内容"})
    ids = {item["document_id"] for item in _tool_json(result)["results"]}
    assert DOC_PUBLIC in ids
    assert DOC_PRIVATE_OWN in ids
    assert DOC_PRIVATE_GRANTED in ids
    # 他人私有文档 / 他组织文档都不得进入检索结果。
    assert DOC_PRIVATE_OTHER not in ids
    assert DOC_OTHER_ORG not in ids


async def test_search_document_ids_allow_list(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool(
            "search_knowledge_base",
            {"query": "内容", "document_ids": [DOC_PUBLIC]},
        )
    assert [item["document_id"] for item in _tool_json(result)["results"]] == [DOC_PUBLIC]
    assert env.knowledge.calls[-1]["document_ids"] == [DOC_PUBLIC]


async def test_search_result_fields_are_minimal(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool("search_knowledge_base", {"query": "内容"})
    item = _tool_json(result)["results"][0]
    assert set(item) == {"document_id", "title", "snippet", "score"}
    assert item["title"]
    assert len(item["snippet"]) <= mcp_tools.SNIPPET_MAX_CHARS


async def test_search_snippet_is_truncated_with_score_and_count(env):
    long_text = "长" * 2_000
    env.knowledge.documents.append(
        {
            "id": "doc-long",
            "filename": "long.txt",
            "organization_id": ORG_ID,
            "visibility": "organization",
            "uploaded_by": USER_ID,
            "text": long_text,
        }
    )
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool("search_knowledge_base", {"query": "长"})
    payload = _tool_json(result)
    item = next(row for row in payload["results"] if row["document_id"] == "doc-long")
    assert item["snippet"] == long_text[: mcp_tools.SNIPPET_MAX_CHARS]
    assert item["score"] == 0.9
    assert payload["count"] == len(payload["results"])


# ── get_document 组织 / ACL / 截断 ─────────────────────────────────


async def test_get_document_public_is_bounded(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool(
            "get_document",
            {"document_id": DOC_PUBLIC, "max_chars": 10},
        )
    assert result.is_error is False
    payload = _tool_json(result)
    assert payload["document_id"] == DOC_PUBLIC
    assert payload["title"] == "public.pdf"
    assert payload["content"] == PUBLIC_TEXT[:10]
    assert payload["truncated"] is True
    assert payload["total_chars"] == len(PUBLIC_TEXT)
    assert payload["text_provenance"] == "parser_original"


async def test_get_document_own_private_and_granted_are_visible(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        own = await session.call_tool(
            "get_document", {"document_id": DOC_PRIVATE_OWN, "max_chars": 100}
        )
        granted = await session.call_tool(
            "get_document", {"document_id": DOC_PRIVATE_GRANTED, "max_chars": 100}
        )
    assert own.is_error is False
    assert _tool_json(own)["content"] == OWN_TEXT
    assert granted.is_error is False
    assert _tool_json(granted)["content"] == GRANTED_TEXT


async def test_get_document_not_truncated_flag(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool(
            "get_document", {"document_id": DOC_PRIVATE_OWN, "max_chars": 10_000}
        )
    payload = _tool_json(result)
    assert payload["truncated"] is False
    assert payload["content"] == OWN_TEXT


async def test_get_document_cross_org_and_private_share_same_not_found(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        cross_org = await session.call_tool("get_document", {"document_id": DOC_OTHER_ORG})
        private_peer = await session.call_tool("get_document", {"document_id": DOC_PRIVATE_OTHER})
        missing = await session.call_tool("get_document", {"document_id": "no-such-doc"})
    assert cross_org.is_error is True
    assert private_peer.is_error is True
    assert missing.is_error is True
    texts = {_tool_text(cross_org), _tool_text(private_peer), _tool_text(missing)}
    assert len(texts) == 1
    assert "Document not found" in texts.pop()
    # 不得回显文档存在性/内容/内部异常。
    assert "private" not in _tool_text(private_peer).lower()


async def test_get_document_max_chars_config_cap(env, monkeypatch):
    monkeypatch.setattr(settings, "MCP_SERVER_MAX_CHARS", 5)
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool(
            "get_document", {"document_id": DOC_PUBLIC, "max_chars": 999}
        )
    payload = _tool_json(result)
    assert payload["content"] == PUBLIC_TEXT[:5]
    assert payload["truncated"] is True


async def test_get_document_audits_not_found(env):
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        await session.call_tool("get_document", {"document_id": DOC_OTHER_ORG})
    call = env.audit.calls[-1]
    detail = json.loads(call["detail"])
    assert detail["tool"] == "get_document"
    assert detail["outcome"] == "not_found"
    assert detail["result_count"] == 0


# ── 认证 / scope / 组织 ────────────────────────────────────────────


async def test_missing_key_is_401(env):
    response = await _raw_post(env.app)
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


async def test_invalid_key_is_401(env):
    presented = "dm_does-not-exist"
    response = await _raw_post(env.app, headers=_auth(presented))
    assert response.status_code == 401
    assert presented not in response.text


async def test_revoked_key_is_401(env):
    raw = await env.make_key()
    async with env.factory() as session:
        record = (
            await session.execute(select(ApiKey).where(ApiKey.prefix == raw[:8]))
        ).scalar_one()
        await api_key_service.revoke_key(session, record)
    response = await _raw_post(env.app, headers=_auth(raw))
    assert response.status_code == 401


async def test_jwt_is_rejected_even_if_valid(env):
    token = auth_service.create_access_token(
        {"user_id": USER_ID, "type": "access", "organization_id": ORG_ID}
    )
    response = await _raw_post(env.app, headers=_auth(token))
    assert response.status_code == 401
    # 显式拒绝 JWT，不进入 MCP 协议层。
    assert response.json()["error"] == "invalid_api_key"


async def test_jwt_never_reaches_api_key_lookup(env, monkeypatch):
    """JWT/非 dm_ Bearer 必须在查 API Key 之前被显式拒绝（不是靠查无此 key）。"""
    seen: list[str] = []

    async def spy_authenticate(token, db):
        seen.append(token)
        return

    monkeypatch.setattr(mcp_auth.api_key_service, "authenticate", spy_authenticate)
    token = auth_service.create_access_token(
        {"user_id": USER_ID, "type": "access", "organization_id": ORG_ID}
    )
    response = await _raw_post(env.app, headers=_auth(token))
    assert response.status_code == 401
    assert seen == []


async def test_key_without_knowledge_scope_is_403(env):
    raw = await env.make_key(scopes=("chat:read",))
    response = await _raw_post(env.app, headers=_auth(raw))
    assert response.status_code == 403
    assert response.json()["error"] == "insufficient_scope"
    assert env.knowledge.calls == []


async def test_org_unbound_key_is_403(env):
    raw = await env.make_key(user_id=NO_ORG_USER_ID)
    response = await _raw_post(env.app, headers=_auth(raw))
    assert response.status_code == 403
    assert response.json()["error"] == "organization_required"


async def test_unbound_key_with_primary_org_still_403(env):
    """key 未绑定组织时，即使 user 有 primary org 也必须 403（禁止回落）。"""
    raw = await env.make_key(user_id=UNBOUND_KEY_USER_ID)
    async with env.factory() as session:
        record = (
            await session.execute(select(ApiKey).where(ApiKey.prefix == raw[:8]))
        ).scalar_one()
        record.org_id = None
        await session.commit()
    response = await _raw_post(env.app, headers=_auth(raw))
    assert response.status_code == 403
    assert response.json()["error"] == "organization_required"


async def test_superuser_key_does_not_bypass_org_or_private_acl(env):
    """平台超管的 key 也必须受 key 绑定组织 + viewer ACL 约束。"""
    raw = await env.make_key(user_id=SUPERUSER_USER_ID)
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        search = await session.call_tool("search_knowledge_base", {"query": "内容"})
        private_peer = await session.call_tool("get_document", {"document_id": DOC_PRIVATE_OTHER})
        cross_org = await session.call_tool("get_document", {"document_id": DOC_OTHER_ORG})
    ids = {item["document_id"] for item in _tool_json(search)["results"]}
    assert DOC_PRIVATE_OTHER not in ids
    assert DOC_OTHER_ORG not in ids
    assert private_peer.is_error is True
    assert cross_org.is_error is True
    assert _tool_text(private_peer) == _tool_text(cross_org)


async def test_search_uses_key_bound_org_not_primary_org(env):
    """key 绑定 43、用户 primary org 是 42：MCP 只认 key 绑定组织。"""
    raw = await env.make_key(org_id=OTHER_ORG_ID)
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool("search_knowledge_base", {"query": "内容"})
    payload = _tool_json(result)
    assert {item["document_id"] for item in payload["results"]} == {DOC_OTHER_ORG}
    assert env.knowledge.calls[-1]["organization_id"] == OTHER_ORG_ID


async def test_other_org_key_cannot_search_own_org_documents(env):
    raw = await env.make_key(user_id=OTHER_ORG_USER_ID)
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool("search_knowledge_base", {"query": "内容"})
    payload = _tool_json(result)
    ids = {item["document_id"] for item in payload["results"]}
    assert ids == {DOC_OTHER_ORG}
    assert env.knowledge.calls[-1]["organization_id"] == OTHER_ORG_ID
    assert env.knowledge.calls[-1]["viewer_user_id"] == OTHER_ORG_USER_ID


async def test_disabled_switch_is_404(env, monkeypatch):
    raw = await env.make_key()
    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", False)
    response = await _raw_post(env.app, headers=_auth(raw))
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"


# ── 限流 / 审计 ────────────────────────────────────────────────────


async def test_per_key_rate_limit_returns_429_with_retry_after(env, monkeypatch):
    monkeypatch.setattr(settings, "MCP_SERVER_RATE_LIMIT_PER_MINUTE", 1)
    raw = await env.make_key()
    async with _served(env):
        first = await _raw_post(env.app, headers=_auth(raw))
        second = await _raw_post(env.app, headers=_auth(raw))
    assert first.status_code in (200, 202)
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json()["error"] == "rate_limited"


async def test_rate_limit_is_per_key_not_per_user_or_ip(env, monkeypatch):
    """同一 user 的两把 key 各有独立桶；不是按 user 也不是按 IP。"""
    monkeypatch.setattr(settings, "MCP_SERVER_RATE_LIMIT_PER_MINUTE", 1)
    key_a = await env.make_key()
    key_b = await env.make_key()
    async with _served(env):
        first_a = await _raw_post(env.app, headers=_auth(key_a))
        first_b = await _raw_post(env.app, headers=_auth(key_b))
        second_a = await _raw_post(env.app, headers=_auth(key_a))
    assert first_a.status_code in (200, 202)
    assert first_b.status_code in (200, 202)
    assert second_a.status_code == 429


async def test_audit_contains_no_query_body_or_raw_key(env):
    raw = await env.make_key()
    secret_query = "TOP-SECRET-QUERY"
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        await session.call_tool("search_knowledge_base", {"query": secret_query})
        await session.call_tool("get_document", {"document_id": DOC_PUBLIC, "max_chars": 50})

    assert len(env.audit.calls) == 2
    for call in env.audit.calls:
        assert call["action"] == "mcp_tool_call"
        assert call["target_type"] == "mcp_tool"
        detail_text = call["detail"]
        assert secret_query not in detail_text
        assert raw not in detail_text
        assert PUBLIC_TEXT[:20] not in detail_text
        detail = json.loads(detail_text)
        assert set(detail) == {
            "tool",
            "organization_id",
            "result_count",
            "latency_ms",
            "outcome",
        }
        assert detail["organization_id"] == ORG_ID
        assert detail["latency_ms"] >= 0
        # 整个审计 kwargs 都不得出现查询正文、文档正文或原始 key。
        full_call = json.dumps(call, ensure_ascii=False)
        assert secret_query not in full_call
        assert raw not in full_call
        assert PUBLIC_TEXT[:20] not in full_call
    assert [
        (
            json.loads(call["detail"])["tool"],
            json.loads(call["detail"])["outcome"],
            json.loads(call["detail"])["result_count"],
        )
        for call in env.audit.calls
    ] == [("search_knowledge_base", "ok", 3), ("get_document", "ok", 1)]


async def test_search_backend_failure_is_tool_error_and_audited(env):
    from app.rag.errors import RetrievalUnavailableError

    env.knowledge.error = RetrievalUnavailableError("boom")
    raw = await env.make_key()
    async with _sdk_session(env, raw) as session:
        await session.initialize()
        result = await session.call_tool("search_knowledge_base", {"query": "x"})
    assert result.is_error is True
    assert _tool_text(result) == (
        "Error executing tool search_knowledge_base: Knowledge retrieval is temporarily unavailable"
    )
    assert "boom" not in _tool_text(result)
    assert json.loads(env.audit.calls[-1]["detail"])["outcome"] == "retrieval_unavailable"


# ── 生命周期 / 生产挂载 ────────────────────────────────────────────


async def test_session_manager_exits_cleanly(monkeypatch):
    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    inbound = mcp_server_module.create_inbound_mcp_server(session_factory=lambda: _Session())
    assert inbound.is_running is False
    async with inbound.session_lifespan():
        assert inbound.is_running is True
        assert inbound.session_manager._task_group is not None
    assert inbound.is_running is False
    assert inbound.session_manager._task_group is None


async def test_production_app_mounts_exact_path(monkeypatch):
    """真实 app.main 的 /api/v1/mcp/server：无尾斜杠、无 307，且受开关门控。"""
    import httpx

    from app.main import app as production_app

    async def _post() -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=production_app),
            base_url=BASE_URL,
        ) as client:
            return await client.post("/api/v1/mcp/server", json=INIT_REQUEST)

    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", True)
    enabled = await _post()
    assert enabled.status_code == 401  # 路由存在且认证中间件生效（未带 key）
    assert enabled.json()["error"] == "invalid_api_key"

    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", False)
    disabled = await _post()
    assert disabled.status_code == 404
    assert disabled.json()["error"] == "not_found"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=production_app),
        base_url=BASE_URL,
    ) as client:
        slash = await client.post("/api/v1/mcp/server/", json=INIT_REQUEST)
    assert slash.status_code == 404  # 关闭时带尾斜杠同样 404，不再 307
    assert "location" not in slash.headers


async def test_manager_not_running_returns_503(env):
    """开关打开但 lifespan 未启动：诚实 503，不把内部异常抛给客户端。"""
    raw = await env.make_key()
    response = await _raw_post(env.app, headers=_auth(raw))
    assert response.status_code == 503
    assert response.json()["error"] == "unavailable"
    assert env.knowledge.calls == []


async def test_get_is_405_and_cannot_hold_a_stream(env):
    """stateless + json_response：GET 不提供 SSE，直接 405（不挂起连接）。"""
    raw = await env.make_key()
    response = await _raw_get(env.app, headers=_auth(raw))
    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


async def test_disabled_trailing_slash_is_404_without_redirect(env, monkeypatch):
    raw = await env.make_key()
    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", False)
    response = await _raw_post(env.app, headers=_auth(raw), path="/api/v1/mcp/server/")
    assert response.status_code == 404
    assert "location" not in response.headers


def test_mcp_server_defaults_to_disabled():
    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["MCP_SERVER_ENABLED"].default is False


def test_default_lifespan_resolver_uses_inbound_singleton(monkeypatch):
    from app import bootstrap

    marker = object()

    class _FakeInbound:
        def session_lifespan(self):
            return marker

    monkeypatch.setattr(mcp_server_module, "get_inbound_mcp_server", lambda: _FakeInbound())
    assert bootstrap.default_mcp_inbound_lifespan() is marker
    assert (
        bootstrap.LifespanDeps.__dataclass_fields__["mcp_inbound_lifespan"].default
        is bootstrap.default_mcp_inbound_lifespan
    )


async def test_bootstrap_lifespan_enters_and_exits_inbound_mcp(monkeypatch):
    """bootstrap lifespan 必须进出 ``mcp_inbound_lifespan``（含异常安全收尾）。"""
    from contextlib import asynccontextmanager

    from app import bootstrap
    from app import dependencies as deps_mod
    from app.agent.mcp import manager as mcp_client_mod
    from app.services import embedding_service, local_processing
    from app.services import permission_service as ps_mod

    events: list[str] = []

    @asynccontextmanager
    async def fake_mcp_lifespan():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    class _Kafka:
        start = AsyncMock()
        stop = AsyncMock()

    deps = bootstrap.LifespanDeps(
        init_db=AsyncMock(),
        init_redis=AsyncMock(),
        init_elasticsearch=AsyncMock(),
        close_redis=AsyncMock(),
        close_db=AsyncMock(),
        kafka_producer=_Kafka(),
        es_client=None,
        resolve_backend_port=lambda: 8000,
        metrics_snapshot_task=lambda: None,
        scheduled_tasks_loop=lambda: None,
        mcp_inbound_lifespan=fake_mcp_lifespan,
    )
    monkeypatch.setattr(bootstrap, "open", lambda *_args, **_kwargs: io.StringIO(), raising=False)
    monkeypatch.setattr(bootstrap, "get_active_entrypoint", lambda: "worker")
    monkeypatch.setattr(
        embedding_service,
        "verify_embedding_consistency",
        AsyncMock(return_value={"skipped": True}),
    )
    monkeypatch.setattr(
        ps_mod.permission_service,
        "initialize_default_permissions_and_roles",
        AsyncMock(),
    )
    monkeypatch.setattr(deps_mod, "wire_memory_embedding_provider", AsyncMock())
    monkeypatch.setattr(local_processing, "reconcile_local_jobs", AsyncMock(return_value=0))
    monkeypatch.setattr(mcp_client_mod.mcp_manager, "initialize", AsyncMock())
    monkeypatch.setattr(settings, "ENABLE_MONITORING", False)
    monkeypatch.setattr(settings, "ENABLE_SCHEDULER", False)
    monkeypatch.setattr(settings, "ENABLE_DEMO_ACCOUNT", False)
    monkeypatch.setattr(settings, "HOOKS_ENABLED", False)
    monkeypatch.setattr(settings, "WEBHOOKS_ENABLED", False)
    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", True)

    lifespan = bootstrap.build_lifespan(deps)
    async with lifespan(MagicMock()):
        assert events == ["enter"]
    assert events == ["enter", "exit"]

    # 关闭开关时不得进入（默认关闭 = 零行为变化）。
    events.clear()
    monkeypatch.setattr(settings, "MCP_SERVER_ENABLED", False)
    async with bootstrap.build_lifespan(deps)(MagicMock()):
        pass
    assert events == []
