"""B1：我的组织列表 + 组织切换端点（先红 —— 端点尚不存在）。

判别点：
- GET /users/me/organizations 只返回调用者成员关系（user_organization）内的组织，
  没有成员关系的组织绝不出现；主组织即使缺成员行也可见（兼容历史用户）；
- POST /users/me/organizations/{id}/switch 成功后，用户 DB 主组织字段更新，
  知识库列表与会话列表随之处在同一组织上下文（按新组织过滤）；
- 非成员切换 → 403，且状态原样不动（多租户 fail-closed 不回退）；
- 超管保留全局切换能力（既有 god-mode 语义）。

真 SQLite + 真实模型；不依赖 MySQL/Redis/ES。
"""
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.exceptions import AuthorizationError, NotFoundError
from app.models import Base
from app.models.chat import ChatSession, ChatSessionStatus
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.organization import Organization, user_organization
from app.models.user import User

_FAKE_REQUEST = SimpleNamespace(headers={}, client=None)


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([
            User(id=7, username="multi", email="m7@example.com", hashed_password="x",
                 role="user", organization_id=1, is_active=True),
            User(id=9, username="legacy", email="m9@example.com", hashed_password="x",
                 role="user", organization_id=2, is_active=True),
            User(id=10, username="root", email="m10@example.com", hashed_password="x",
                 role="user", organization_id=None, is_active=True, is_superuser=True),
        ])
        s.add_all([
            Organization(id=1, name="OrgA", owner_id=7),
            Organization(id=2, name="OrgB", owner_id=7),
            Organization(id=3, name="OrgC", owner_id=7),
        ])
        await s.flush()
        await s.execute(user_organization.insert().values(user_id=7, organization_id=1))
        await s.execute(user_organization.insert().values(user_id=7, organization_id=2))
        s.add_all([
            Document(id="doc-a", filename="a.txt", file_path="/a", file_size=1,
                     file_type=DocumentType.TXT, status=DocumentStatus.INDEXED,
                     organization_id=1, uploaded_by=7),
            Document(id="doc-b", filename="b.txt", file_path="/b", file_size=1,
                     file_type=DocumentType.TXT, status=DocumentStatus.INDEXED,
                     organization_id=2, uploaded_by=7),
            ChatSession(id="sess-a", user_id=7, organization_id=1,
                        status=ChatSessionStatus.ACTIVE, title="A"),
            ChatSession(id="sess-b", user_id=7, organization_id=2,
                        status=ChatSessionStatus.ACTIVE, title="B"),
        ])
        await s.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_my_organizations_lists_only_memberships():
    from app.api.v1.endpoints.users import get_my_organizations

    engine, factory = await _setup()
    try:
        async with factory() as s:
            user = await s.get(User, 7)
            payload = await get_my_organizations(current_user=user, db=s)
        data = payload["data"]
        assert {o["id"] for o in data["organizations"]} == {1, 2}
        assert data["current_organization_id"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_primary_org_visible_without_membership_row():
    """历史用户没有 user_organization 行时，主组织仍必须出现在列表里。"""
    from app.api.v1.endpoints.users import get_my_organizations

    engine, factory = await _setup()
    try:
        async with factory() as s:
            user = await s.get(User, 9)
            payload = await get_my_organizations(current_user=user, db=s)
        assert {o["id"] for o in payload["data"]["organizations"]} == {2}
        assert payload["data"]["current_organization_id"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_switch_updates_context_and_list_filters_follow_it():
    from app.api.v1.endpoints.chat import get_conversations
    from app.api.v1.endpoints.knowledge import list_knowledge_bases
    from app.api.v1.endpoints.users import switch_my_organization

    engine, factory = await _setup()
    try:
        async with factory() as s:
            user = await s.get(User, 7)
            payload = await switch_my_organization(
                2, request=_FAKE_REQUEST, current_user=user, db=s
            )
            assert payload["data"]["organization_id"] == 2
            assert user.organization_id == 2

        async with factory() as s:
            user = await s.get(User, 7)
            assert user.organization_id == 2
            docs = await list_knowledge_bases(current_user=user, db=s)
            assert {d["id"] for d in docs["data"]["data"]} == {"doc-b"}
            conversations = await get_conversations(
                search=None, current_user=user, db=s
            )
            assert {c["id"] for c in conversations["data"]["data"]} == {"sess-b"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_switch_to_non_member_is_rejected_and_state_unchanged():
    from app.api.v1.endpoints.users import switch_my_organization

    engine, factory = await _setup()
    try:
        async with factory() as s:
            user = await s.get(User, 7)
            with pytest.raises(AuthorizationError) as ei:
                await switch_my_organization(
                    3, request=_FAKE_REQUEST, current_user=user, db=s
                )
            assert ei.value.status_code == 403
        async with factory() as s:
            assert (await s.get(User, 7)).organization_id == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_switch_to_missing_organization_is_404():
    from app.api.v1.endpoints.users import switch_my_organization

    engine, factory = await _setup()
    try:
        async with factory() as s:
            user = await s.get(User, 7)
            with pytest.raises(NotFoundError):
                await switch_my_organization(
                    999, request=_FAKE_REQUEST, current_user=user, db=s
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_superuser_can_list_and_switch_to_any_organization():
    from app.api.v1.endpoints.users import get_my_organizations, switch_my_organization

    engine, factory = await _setup()
    try:
        async with factory() as s:
            root = await s.get(User, 10)
            payload = await get_my_organizations(current_user=root, db=s)
            assert {o["id"] for o in payload["data"]["organizations"]} == {1, 2, 3}

            switched = await switch_my_organization(
                3, request=_FAKE_REQUEST, current_user=root, db=s
            )
            assert switched["data"]["organization_id"] == 3
            assert root.organization_id == 3
    finally:
        await engine.dispose()
