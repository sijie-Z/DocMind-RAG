"""A2 ⑤：组织删除的级联检查（禁止裸删 → MySQL 1451）。

- 组织（含子组织）下存在文档 / 会话 / 任务等工作数据时，删除返回 409 并提示转移；
- 干净组织可正常删除，引用它的成员用户被安全摘除（organization_id → NULL），
  不触发外键 1451；
- 批量删除不得把 409 吞成 "删除失败" 的 200。

真 SQLite（``Base.metadata.create_all``），不依赖外部服务。
"""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import ConflictError
from app.models import Base
from app.models.agent_task import AgentTask
from app.models.chat import ChatSession
from app.models.document import Document, DocumentType
from app.models.organization import Organization
from app.models.user import User
from app.services.organization_service import organization_service


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([
            Organization(id=10, name="BusyOrg", owner_id=20),
            Organization(id=11, name="EmptyOrg", owner_id=20),
            Organization(id=12, name="ChildOrg", owner_id=20, parent_id=10, level=2),
            User(id=20, username="owner", email="o@example.com", hashed_password="x",
                 role="admin", organization_id=10, is_active=True),
        ])
        await s.commit()
    return engine, factory


def _doc(doc_id: str, org_id: int) -> Document:
    return Document(
        id=doc_id, filename=f"{doc_id}.txt", file_path=f"/tmp/{doc_id}.txt",
        file_size=1, file_type=DocumentType.TXT, organization_id=org_id, uploaded_by=20,
    )


def _superuser() -> SimpleNamespace:
    return SimpleNamespace(
        id=999, username="root", organization_id=None, role="admin",
        is_superuser=True, is_active=True,
    )


@pytest.mark.asyncio
async def test_delete_org_with_documents_returns_409_with_transfer_hint():
    engine, factory = await _setup()
    try:
        async with factory() as s:
            s.add(_doc("doc-1", 10))
            await s.commit()

        from app.main import app

        async def _override_db():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user] = lambda: _superuser()
        try:
            transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                r = await ac.delete("/api/v1/organizations/10")
        finally:
            app.dependency_overrides.clear()

        assert r.status_code == 409, f"非空组织删除必须 409（不落到外键 1451）: {r.text[:400]}"
        assert "转移" in r.text, "409 文案必须提示先转移数据"
        async with factory() as s:
            assert await s.get(Organization, 10) is not None, "409 时组织不得被删除"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_org_with_child_org_documents_blocks():
    engine, factory = await _setup()
    try:
        async with factory() as s:
            s.add(_doc("doc-child", 12))  # 子组织里的文档同样阻断父组织删除
            await s.commit()
        async with factory() as s:
            with pytest.raises(ConflictError):
                await organization_service.delete_organization_thoroughly(s, 10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_org_with_chat_sessions_blocks():
    engine, factory = await _setup()
    try:
        async with factory() as s:
            s.add(ChatSession(id="sess-1", user_id=20, organization_id=10, title="t"))
            await s.commit()
        async with factory() as s:
            with pytest.raises(ConflictError):
                await organization_service.delete_organization_thoroughly(s, 10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_org_with_agent_tasks_blocks():
    engine, factory = await _setup()
    try:
        async with factory() as s:
            s.add(AgentTask(id="task-1", user_id=20, organization_id=10, query="q"))
            await s.commit()
        async with factory() as s:
            with pytest.raises(ConflictError):
                await organization_service.delete_organization_thoroughly(s, 10)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_clean_org_deletes_and_detaches_members():
    engine, factory = await _setup()
    try:
        # 让用户成为空组织 11 的成员（users.organization_id 指向 11）
        async with factory() as s:
            user = await s.get(User, 20)
            user.organization_id = 11
            await s.commit()

        async with factory() as s:
            ok = await organization_service.delete_organization_thoroughly(s, 11)
            await s.commit()
            assert ok is True
            assert await s.get(Organization, 11) is None
            user = await s.get(User, 20)
            assert user.organization_id is None, "删除组织后成员用户必须被摘除，而不是留下悬空外键"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_batch_delete_propagates_conflict_instead_of_swallowing():
    engine, factory = await _setup()
    try:
        async with factory() as s:
            s.add(_doc("doc-batch", 10))
            await s.commit()
        async with factory() as s:
            with pytest.raises(ConflictError):
                await organization_service.batch_delete_organizations(s, [10])
            assert await s.get(Organization, 10) is not None
    finally:
        await engine.dispose()
