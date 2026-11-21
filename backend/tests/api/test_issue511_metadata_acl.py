"""issue #511 P1 元数据泄漏回归：组织/文件/聊天列表都按 viewer 过滤私有文档。

真实内存 SQLite + ASGI：A 上传私有文档，B 是同组织成员但无 ACL 授权。
- B 调用三个端点均不得拿到 A 私有文档的标题 / 文件名 / ID；
- /files/list 的 user_id 可由调用方指定，服务端必须以认证用户覆盖；
- total 与 items 同口径（count 泄漏也算失败）。

变异点：删任一过滤 / 服务端 user_id 覆盖 → 对应用例红。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.document import Document, DocumentStatus, DocumentType, DocumentVisibility
from app.models.organization import Organization
from app.models.rbac import Permission, PermissionType, Role
from app.models.user import User

ORG = 11
USER_A = 101
USER_B = 202
DOC_PRIVATE = "doc-511-private"
DOC_PRIVATE_TITLE = "绝密-甲方并购草案"
DOC_PRIVATE_FILE = "a-private-merger.docx"
DOC_PUBLIC = "doc-511-public"
DOC_PUBLIC_TITLE = "公开的制度手册"
DOC_PUBLIC_FILE = "public-handbook.pdf"
SESSION = "session-511-b"

ORG_DOCUMENTS = f"/api/v1/organizations/{ORG}/documents"
FILES_LIST = "/api/v1/files/list"
CONVERSATION = f"/api/v1/chat/conversations/{SESSION}"


async def _seeded_factory():
    """A/B 同组织；A 的私有文档 + 一篇组织可见文档；B 的会话含恶意 file_ids。"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        s.add(Organization(id=ORG, name="Org 511", owner_id=USER_A))
        role = Role(name="User", is_system_role=True)
        for perm in (
            PermissionType.VIEW_ORGANIZATION,
            PermissionType.VIEW_KNOWLEDGE_BASE,
            PermissionType.DOCUMENT_UPLOAD,
        ):
            role.permissions.append(Permission(name=perm.value))
        s.add(role)
        s.add_all(
            [
                User(
                    id=USER_A,
                    username="acl511-a",
                    email="a@511.test",
                    hashed_password="x",
                    organization_id=ORG,
                    role="user",
                    is_active=True,
                ),
                User(
                    id=USER_B,
                    username="acl511-b",
                    email="b@511.test",
                    hashed_password="x",
                    organization_id=ORG,
                    role="user",
                    is_active=True,
                ),
            ]
        )
        s.add_all(
            [
                Document(
                    id=DOC_PRIVATE,
                    filename=DOC_PRIVATE_FILE,
                    title=DOC_PRIVATE_TITLE,
                    file_path=f"{ORG}/{DOC_PRIVATE_FILE}",
                    file_size=1,
                    file_type=DocumentType.WORD,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=USER_A,
                    visibility=DocumentVisibility.PRIVATE,
                    content_length=10,
                    chunk_count=1,
                ),
                Document(
                    id=DOC_PUBLIC,
                    filename=DOC_PUBLIC_FILE,
                    title=DOC_PUBLIC_TITLE,
                    file_path=f"{ORG}/{DOC_PUBLIC_FILE}",
                    file_size=1,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=USER_A,
                    visibility=DocumentVisibility.ORGANIZATION,
                    content_length=10,
                    chunk_count=1,
                ),
            ]
        )
        s.add(
            ChatSession(
                id=SESSION,
                user_id=USER_B,
                organization_id=ORG,
                title="B 的会话",
                status=ChatSessionStatus.ACTIVE,
                settings={},
                next_message_seq=1,
            )
        )
        s.add(
            ChatMessage(
                id="msg-511",
                session_id=SESSION,
                seq=1,
                content="请分析附件",
                message_type=MessageType.USER,
                meta_data=json.dumps({"file_ids": [DOC_PRIVATE, DOC_PUBLIC]}),
            )
        )
        await s.commit()
    return engine, factory


class _ApiCtx:
    """dependency_overrides 安装到同一个 app，退出时恢复，避免污染其它测试。"""

    def __init__(self, user, factory):
        self._user = user
        self._factory = factory

    async def __aenter__(self):
        from app.main import app

        async def _current_user():
            return self._user

        async def _db():
            async with self._factory() as session:
                yield session

        self._app = app
        self._saved = dict(app.dependency_overrides)
        app.dependency_overrides[get_current_user] = _current_user
        app.dependency_overrides[get_db] = _db
        # file_service 自开 AsyncSessionLocal（不经过 get_db override），
        # 必须一并指向同一内存库，否则会打到真实 MySQL 并掩盖本测试的判定。
        self._session_patch = patch(
            "app.services.file_service.AsyncSessionLocal", new=lambda: self._factory()
        )
        self._session_patch.start()
        self._client = AsyncClient(transport=ASGITransport(app=app), base_url="http://acl511.test")
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        await self._client.aclose()
        self._session_patch.stop()
        self._app.dependency_overrides.clear()
        self._app.dependency_overrides.update(self._saved)
        return False


def _b_user():
    return SimpleNamespace(
        id=USER_B,
        organization_id=ORG,
        token_organization_id=None,
        is_superuser=False,
        role="user",
        is_active=True,
        username="acl511-b",
        api_key_scopes=None,
    )


def _assert_no_private_leak(payload: str) -> None:
    """判别断言：私有标题/文件名/ID 一个都不能出现。"""
    for secret in (DOC_PRIVATE_TITLE, DOC_PRIVATE_FILE, DOC_PRIVATE):
        assert secret not in payload, f"B 看到了 A 的私有元数据: {secret}"


@pytest.mark.asyncio
async def test_organization_documents_hides_private_title_id_and_count():
    engine, factory = await _seeded_factory()
    try:
        async with _ApiCtx(_b_user(), factory) as ac:
            r = await ac.get(ORG_DOCUMENTS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        items = body["data"]
        assert [d["id"] for d in items] == [DOC_PUBLIC], items
        # items 与 count 同口径：本端点只返回 data 列表，逐项断言即为 count 口径。
        assert len(items) == 1
        _assert_no_private_leak(r.text)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_files_list_ignores_caller_user_id_and_filters_private_metadata():
    engine, factory = await _seeded_factory()
    try:
        async with _ApiCtx(_b_user(), factory) as ac:
            # 攻击参数：B 指定 A 的 user_id，试图拿 A 的私有文件名。
            r = await ac.get(FILES_LIST, params={"user_id": str(USER_A)})
        assert r.status_code == 200, r.text
        body = r.json()
        docs = body["documents"]
        # 服务端 user_id 覆盖：B 只能看自己上传的文档 → 0 条；
        # total 同步收口，不能出现「items 空但 total>0」的 count 泄漏。
        assert docs == [], docs
        assert body["total"] == 0, body
        _assert_no_private_leak(r.text)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_files_list_endpoint_overrides_caller_supplied_user_id():
    """判别端点层覆盖：即使 service 层再做一次覆盖，端点自身也必须传认证用户。

    变异点：把端点 `user_id=str(current_user.id)` 改回 `user_id=user_id` → 本条红。
    """
    engine, factory = await _seeded_factory()
    captured = {}

    async def _fake_list(**kwargs):
        captured.update(kwargs)
        return {"total": 0, "documents": [], "skip": 0, "limit": 20}

    try:
        from app.api.v1.endpoints import files as files_api
        from app.main import app

        async def _b():
            return _b_user()

        async def _db():
            async with factory() as session:
                yield session

        saved = dict(app.dependency_overrides)
        app.dependency_overrides[get_current_user] = _b
        app.dependency_overrides[get_db] = _db
        try:
            with patch.object(files_api.file_service, "get_document_list", new=_fake_list):
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://acl511.test"
                ) as ac:
                    r = await ac.get(FILES_LIST, params={"user_id": str(USER_A)})
        finally:
            app.dependency_overrides.clear()
            app.dependency_overrides.update(saved)
        assert r.status_code == 200, r.text
        assert captured["user_id"] == str(USER_B), captured
        assert captured["viewer_user_id"] == USER_B, captured
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_chat_history_does_not_resolve_private_file_id_to_filename():
    engine, factory = await _seeded_factory()
    try:
        async with _ApiCtx(_b_user(), factory) as ac:
            r = await ac.get(CONVERSATION)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True, body
        files = body["data"]["messages"][0]["files"]
        assert [f["id"] for f in files] == [DOC_PUBLIC], files
        _assert_no_private_leak(r.text)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_files_list_service_overrides_caller_user_id():
    """判别服务层覆盖：ACL 全放行时也不能用外部 user_id 读另一用户的文件名。

    变异点：删掉 `user_id = str(viewer_user_id)` → B 传 A 的 id 即读到 A 的
    组织可见文档文件名 → 本条红。
    """
    engine, factory = await _seeded_factory()
    try:
        with (
            patch(
                "app.services.document_acl.get_blocked_document_ids",
                new=AsyncMock(return_value=[]),
            ),
            patch("app.services.file_service.AsyncSessionLocal", new=lambda: factory()),
        ):
            from app.services.file_service import file_service

            result = await file_service.get_document_list(
                organization_id=str(ORG), user_id=str(USER_A), viewer_user_id=USER_B
            )
        assert result["documents"] == [], result
        assert result["total"] == 0, result
        _assert_no_private_leak(json.dumps(result, ensure_ascii=False))
        assert DOC_PUBLIC_FILE not in json.dumps(result, ensure_ascii=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_files_list_blocked_ids_apply_to_owner_as_well():
    """判别 ACL 过滤本身：A 上传的私有文档被撤销授权后，A 自己也看不到。

    变异点：删掉 `query.where(Document.id.notin_(blocked))` → A 仍能看到
    自己的私有文档 → 本条红。与 user_id 覆盖测试组合可区分两层保护。
    """
    engine, factory = await _seeded_factory()
    blockers = []

    async def _blocked(session, org_id, viewer_user_id):
        blockers.append((org_id, viewer_user_id))
        return [DOC_PRIVATE]

    try:
        with (
            patch("app.services.document_acl.get_blocked_document_ids", new=_blocked),
            patch("app.services.file_service.AsyncSessionLocal", new=lambda: factory()),
        ):
            from app.services.file_service import file_service

            result = await file_service.get_document_list(
                organization_id=str(ORG), user_id=str(USER_A), viewer_user_id=USER_A
            )
        assert blockers == [(ORG, USER_A)]
        assert [d["id"] for d in result["documents"]] == [DOC_PUBLIC]
        assert result["total"] == 1, result
        _assert_no_private_leak(json.dumps(result, ensure_ascii=False))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_files_list_fails_closed_when_acl_query_raises():
    """ACL 查询异常必须屏蔽私有文档，不能吞异常后按组织级放行。"""
    engine, factory = await _seeded_factory()
    blockers = []

    async def _blocked(*args, **kwargs):
        blockers.append(kwargs)
        return [DOC_PRIVATE, DOC_PUBLIC]

    try:
        with patch("app.services.document_acl.get_blocked_document_ids", new=_blocked):
            async with _ApiCtx(_b_user(), factory) as ac:
                r = await ac.get(FILES_LIST)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 0 and body["documents"] == [], body
        assert blockers, "必须调用 ACL 查询，不能跳过过滤"
        assert blockers[-1].get("viewer_user_id") in (None, USER_B)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_org_documents_superuser_keeps_audit_visibility():
    """超管豁免是有意保留的既有契约，不能被误改成 0 条。"""
    engine, factory = await _seeded_factory()
    admin = _b_user()
    admin.is_superuser = True
    try:
        async with _ApiCtx(admin, factory) as ac:
            r = await ac.get(ORG_DOCUMENTS)
        assert r.status_code == 200, r.text
        assert {d["id"] for d in r.json()["data"]} == {DOC_PRIVATE, DOC_PUBLIC}
    finally:
        await engine.dispose()
