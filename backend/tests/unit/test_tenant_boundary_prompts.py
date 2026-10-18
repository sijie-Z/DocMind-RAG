"""A2 租户边界：提示词模板双组织矩阵。

- 列表：org A 用户看到本组织模板 + 系统模板（org=NULL），看不到 org B 模板；
- 详情/版本/更新/删除：跨组织一律 404（不泄露存在性）；
- 系统模板（org=NULL）只读：任何角色都不得改写；
- 激活（is_active False→True）限管理员并审计；
- 创建时忽略客户端传入的 is_system（防止普通用户伪造全局系统模板），
  organization_id 由服务端按调用者组织写入。

真 SQLite + ``httpx.ASGITransport``，仅覆盖提示词端点。
"""
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base
from app.models.organization import Organization
from app.models.prompt import PromptTemplate
from app.models.prompt_version import PromptTemplateVersion
from app.models.user import User


def _user(uid: int, org: int | None, *, role: str = "user", superuser: bool = False) -> User:
    return User(
        id=uid,
        username=f"user{uid}",
        email=f"user{uid}@example.com",
        hashed_password="x",
        role=role,
        organization_id=org,
        is_active=True,
        is_superuser=superuser,
    )


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([
            Organization(id=1, name="OrgA", owner_id=1),
            Organization(id=2, name="OrgB", owner_id=3),
            _user(1, 1, role="admin"),
            _user(2, 1, role="user"),
            _user(3, 2, role="admin"),
        ])
        await s.flush()
        s.add_all([
            PromptTemplate(name="sys-template", content="s", is_system=True, organization_id=None, creator_id=None),
            PromptTemplate(name="p-A", content="a", is_system=False, organization_id=1, creator_id=2),
            PromptTemplate(name="p-B", content="b", is_system=False, organization_id=2, creator_id=3),
            PromptTemplate(name="p-A-inactive", content="i", is_system=False, organization_id=1, creator_id=2, is_active=False),
            # 迁移无法回填归属的历史非系统行：只允许超管可见，不得当系统模板广播。
            PromptTemplate(name="legacy-orphan", content="o", is_system=False, organization_id=None, creator_id=None),
        ])
        await s.commit()
        ids = {}
        for name in ("sys-template", "p-A", "p-B", "p-A-inactive", "legacy-orphan"):
            ids[name] = (await s.execute(select(PromptTemplate.id).where(PromptTemplate.name == name))).scalar_one()
    return engine, factory, ids


async def _call(factory, user, method: str, url: str, **kwargs):
    from app.main import app

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.request(method, url, **kwargs)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_filters_by_org_and_keeps_system_templates():
    engine, factory, ids = await _setup()
    try:
        r = await _call(factory, _user(2, 1, role="user"), "GET", "/api/v1/prompts/")
        assert r.status_code == 200, r.text
        names = {item["name"] for item in r.json()}
        assert {"p-A", "sys-template", "p-A-inactive"} <= names
        assert "p-B" not in names, "org A 用户不得看到 org B 的模板"
        assert "legacy-orphan" not in names, "非系统 NULL 孤儿行只允许超管可见"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cross_org_update_delete_versions_are_404():
    engine, factory, ids = await _setup()
    try:
        admin_a = _user(1, 1, role="admin")
        r = await _call(factory, admin_a, "PUT", f"/api/v1/prompts/{ids['p-B']}", json={"content": "hijacked"})
        assert r.status_code == 404
        r = await _call(factory, admin_a, "DELETE", f"/api/v1/prompts/{ids['p-B']}")
        assert r.status_code == 404
        r = await _call(factory, admin_a, "GET", f"/api/v1/prompts/{ids['p-B']}/versions")
        assert r.status_code == 404
        async with factory() as s:
            still = await s.get(PromptTemplate, ids["p-B"])
            assert still is not None and still.content == "b", "跨组织改写/删除不得生效"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_system_template_is_read_only():
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory, _user(1, 1, role="admin"), "PUT",
            f"/api/v1/prompts/{ids['sys-template']}", json={"content": "mutated"},
        )
        assert r.status_code == 403, "系统模板（org=NULL）必须只读"
        r = await _call(factory, _user(1, 1, role="admin"), "DELETE", f"/api/v1/prompts/{ids['sys-template']}")
        assert r.status_code == 403
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_ignores_client_is_system_and_stamps_org():
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory, _user(2, 1, role="user"), "POST", "/api/v1/prompts/",
            json={"name": "my-prompt", "content": "x", "is_system": True, "is_active": True},
        )
        assert r.status_code == 200, r.text
        async with factory() as s:
            created = (await s.execute(select(PromptTemplate).where(PromptTemplate.name == "my-prompt"))).scalar_one()
            assert created.is_system is False, "客户端不得伪造系统模板"
            assert created.organization_id == 1, "创建必须落在调用者组织"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_activation_requires_admin_and_is_audited():
    engine, factory, ids = await _setup()
    try:
        # 非管理员（即使是创建者）激活 → 403
        r = await _call(
            factory, _user(2, 1, role="user"), "PUT",
            f"/api/v1/prompts/{ids['p-A-inactive']}", json={"is_active": True},
        )
        assert r.status_code == 403, "激活操作必须限管理员"
        async with factory() as s:
            assert (await s.get(PromptTemplate, ids["p-A-inactive"])).is_active is False

        # 管理员激活 → 200 + 审计
        with patch("app.api.v1.endpoints.prompts.audit_service.log_activity", new=AsyncMock()) as audit:
            r = await _call(
                factory, _user(1, 1, role="admin"), "PUT",
                f"/api/v1/prompts/{ids['p-A-inactive']}", json={"is_active": True},
            )
        assert r.status_code == 200, r.text
        assert audit.await_count == 1, "激活必须写审计日志"
        assert "prompt" in str(audit.await_args.kwargs.get("action", "")).lower()
        async with factory() as s:
            assert (await s.get(PromptTemplate, ids["p-A-inactive"])).is_active is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_creator_can_edit_own_draft_prompt_with_unchanged_is_active():
    """前端编辑表单会回传未变化的 is_active，不得被误判为激活而 403。

    B4（评审 B4-02）：组织模板一旦被管理员激活，创建者也不得再改（草稿态才由
    创建者维护）；因此本用例使用草稿模板 p-A-inactive，验证「未变化的
    is_active=False」不会触发激活门。
    """
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory, _user(2, 1, role="user"), "PUT",
            f"/api/v1/prompts/{ids['p-A-inactive']}",
            json={"content": "updated", "is_active": False},
        )
        assert r.status_code == 200, r.text
        async with factory() as s:
            assert (await s.get(PromptTemplate, ids["p-A-inactive"])).content == "updated"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prompt_name_uniqueness_is_per_org():
    """A2 预审建议②：同名模板允许跨组织存在，同组织内仍冲突（409）。"""
    engine, factory, ids = await _setup()
    try:
        # org B 用与 org A 相同的名字创建 → 允许
        r = await _call(
            factory, _user(3, 2, role="admin"), "POST", "/api/v1/prompts/",
            json={"name": "p-A", "content": "org-b copy"},
        )
        assert r.status_code == 200, r.text
        async with factory() as s:
            org_b_copy = (await s.execute(
                select(PromptTemplate).where(
                    (PromptTemplate.name == "p-A")
                    & (PromptTemplate.organization_id == 2)
                )
            )).scalar_one()
            assert org_b_copy.content == "org-b copy"
            # org A 的原模板未被覆盖
            assert (await s.get(PromptTemplate, ids["p-A"])).content == "a"

        # 同组织内重名 → 409
        r = await _call(
            factory, _user(2, 1, role="user"), "POST", "/api/v1/prompts/",
            json={"name": "p-A", "content": "dup"},
        )
        assert r.status_code == 409
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_restore_and_version_detail_are_org_scoped():
    """A2 评审 m-6：restore / version detail 的跨组织访问必须 404。"""
    engine, factory, ids = await _setup()
    try:
        async with factory() as s:
            s.add(PromptTemplateVersion(
                prompt_id=ids["p-A"], version=1, name="p-A", content="v1", creator_id=2,
            ))
            s.add(PromptTemplateVersion(
                prompt_id=ids["p-B"], version=1, name="p-B", content="b1", creator_id=3,
            ))
            s.add(PromptTemplateVersion(
                prompt_id=ids["p-A-inactive"], version=1,
                name="p-A-inactive", content="draft-v1", creator_id=2,
            ))
            await s.commit()

        admin_a = _user(1, 1, role="admin")
        r = await _call(factory, admin_a, "POST", f"/api/v1/prompts/{ids['p-B']}/restore/1")
        assert r.status_code == 404, "跨组织恢复必须 404"
        r = await _call(factory, admin_a, "GET", f"/api/v1/prompts/{ids['p-B']}/version/1")
        assert r.status_code == 404, "跨组织版本详情必须 404"

        # 同组织创建者可恢复自己的草稿模板（B4-02：激活态仅管理员可改）
        r = await _call(
            factory, _user(2, 1, role="user"), "POST",
            f"/api/v1/prompts/{ids['p-A-inactive']}/restore/1",
        )
        assert r.status_code == 200, r.text
        assert r.json()["content"] == "draft-v1"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_seed_creates_system_templates_with_null_org():
    """seed 仍限管理员；写入的系统模板必须是 organization_id=NULL 且 is_system=True。"""
    engine, factory, ids = await _setup()
    try:
        superuser = _user(99, None, role="admin", superuser=True)
        r = await _call(factory, superuser, "POST", "/api/v1/prompts/seed")
        assert r.status_code == 200, r.text
        async with factory() as s:
            seeded = (await s.execute(
                select(PromptTemplate).where(PromptTemplate.name == "通用助手")
            )).scalar_one()
            assert seeded.is_system is True
            assert seeded.organization_id is None
    finally:
        await engine.dispose()
