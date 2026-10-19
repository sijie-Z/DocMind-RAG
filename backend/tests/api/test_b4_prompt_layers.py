"""B4 提示词三层（系统 / 组织 / 个人）与激活权限测试。

先红口径：当前模型没有 ``scope`` 列、创建不接受 scope、个人模板不存在
（组织内所有模板对所有成员可见）。本文件钉住：

- 系统模板（org=NULL, is_system=True）：全组织只读；
- 组织模板：组织成员可见；激活/停用必须组织管理员（超管旁路），普通用户 403；
- 个人模板：仅创建者可见、可激活，且激活只影响自己；其他成员（含组织管理员）不可见；
- 激活动作必须写审计（activate_prompt / deactivate_prompt）；
- 创建不接受客户端伪造系统模板。
"""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.organization import Organization
from app.models.prompt import PromptTemplate
from app.models.rbac import Role, user_organization_role_association

PROMPTS = "/api/v1/prompts"


def _make_user(uid=1, org=1, role="user", superuser=False):
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        organization_id=org,
        is_superuser=superuser,
        is_active=True,
        role=role,
    )


@pytest.fixture
async def api():
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app

    pg = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with pg.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(pg, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                Organization(id=1, name="org-a", owner_id=1),
                Organization(id=2, name="org-b", owner_id=3),
            ]
        )
        await s.flush()
        # W0-1：组织管理员 = per-org OrgAdmin 角色关联（users.role 不再参与鉴权）
        org_admin_role = Role(
            name="OrgAdmin", description="组织管理员", is_system_role=False
        )
        s.add(org_admin_role)
        await s.flush()
        await s.execute(
            user_organization_role_association.insert().values(
                user_id=10, organization_id=1, role_id=org_admin_role.id
            )
        )
        s.add_all(
            [
                PromptTemplate(
                    name="sys-prompt",
                    content="system",
                    is_system=True,
                    organization_id=None,
                    scope="system",
                    creator_id=None,
                    version=1,
                ),
                PromptTemplate(
                    name="org-prompt",
                    content="org",
                    is_system=False,
                    organization_id=1,
                    scope="organization",
                    creator_id=1,
                    version=1,
                ),
                PromptTemplate(
                    name="personal-a",
                    content="mine",
                    is_system=False,
                    organization_id=1,
                    scope="personal",
                    creator_id=1,
                    version=1,
                ),
                PromptTemplate(
                    name="personal-b",
                    content="other",
                    is_system=False,
                    organization_id=1,
                    scope="personal",
                    creator_id=2,
                    version=1,
                ),
                PromptTemplate(
                    name="other-org",
                    content="org-b",
                    is_system=False,
                    organization_id=2,
                    scope="organization",
                    creator_id=3,
                    version=1,
                ),
            ]
        )
        await s.commit()

    state = SimpleNamespace(user=_make_user())

    async def _override_db():
        async with factory() as s:
            yield s

    def _override_user():
        return state.user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield SimpleNamespace(client=ac, state=state, factory=factory)
    app.dependency_overrides.clear()
    await pg.dispose()


async def _id_by_name(api, name: str) -> int:
    async with api.factory() as s:
        from sqlalchemy import select

        row = (
            await s.execute(select(PromptTemplate).where(PromptTemplate.name == name))
        ).scalars().one()
        return row.id


async def test_visibility_by_scope(api):
    # 普通成员：系统 + 本组织 + 自己的个人模板；看不到别人的个人模板
    r = await api.client.get(f"{PROMPTS}/")
    assert r.status_code == 200, r.text
    names = {p["name"] for p in r.json()}
    assert names == {"sys-prompt", "org-prompt", "personal-a"}
    scopes = {p["name"]: p["scope"] for p in r.json()}
    assert scopes["sys-prompt"] == "system"
    assert scopes["org-prompt"] == "organization"
    assert scopes["personal-a"] == "personal"

    # 组织管理员也不是别人个人模板的读者
    api.state.user = _make_user(uid=10, org=1, role="admin")
    r = await api.client.get(f"{PROMPTS}/")
    names = {p["name"] for p in r.json()}
    assert names == {"sys-prompt", "org-prompt"}

    # 超管全局可见
    api.state.user = _make_user(uid=99, org=None, superuser=True)
    r = await api.client.get(f"{PROMPTS}/")
    assert {p["name"] for p in r.json()} == {
        "sys-prompt",
        "org-prompt",
        "personal-a",
        "personal-b",
        "other-org",
    }


async def test_org_template_activation_requires_admin(api, monkeypatch):
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    from app.services.audit_service import audit_service

    monkeypatch.setattr(audit_service, "log_activity", _capture)

    org_id = await _id_by_name(api, "org-prompt")

    # 普通用户即使是创建者，激活组织模板也必须 403（先红的核心断言）
    r = await api.client.put(f"{PROMPTS}/{org_id}", json={"is_active": False})
    assert r.status_code == 403, r.text
    assert not calls

    # 组织管理员可以激活/停用，且写审计
    api.state.user = _make_user(uid=10, org=1, role="admin")
    r = await api.client.put(f"{PROMPTS}/{org_id}", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    assert calls and calls[-1]["action"] == "deactivate_prompt"
    assert calls[-1]["target_type"] == "prompt_template"
    assert calls[-1]["target_id"] == str(org_id)


async def test_personal_template_only_affects_owner(api, monkeypatch):
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    from app.services.audit_service import audit_service

    monkeypatch.setattr(audit_service, "log_activity", _capture)

    mine = await _id_by_name(api, "personal-a")
    # 本人可激活/停用自己的个人模板
    r = await api.client.put(f"{PROMPTS}/{mine}", json={"is_active": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    assert calls and calls[-1]["action"] == "deactivate_prompt"

    # 其他成员（含组织管理员）不可见也不可改：404 不泄露存在性
    api.state.user = _make_user(uid=10, org=1, role="admin")
    r = await api.client.put(f"{PROMPTS}/{mine}", json={"is_active": True})
    assert r.status_code == 404, r.text

    # 个人模板激活只影响本人：其他成员的列表不受影响
    api.state.user = _make_user(uid=2, org=1)
    r = await api.client.get(f"{PROMPTS}/")
    assert all(p["name"] != "personal-a" for p in r.json())


async def test_system_template_is_read_only(api):
    sys_id = await _id_by_name(api, "sys-prompt")
    api.state.user = _make_user(uid=10, org=1, role="admin")
    r = await api.client.put(f"{PROMPTS}/{sys_id}", json={"content": "hacked"})
    assert r.status_code == 403, r.text


async def test_create_scope_personal_and_reject_system(api):
    r = await api.client.post(
        f"{PROMPTS}/",
        json={"name": "my-private", "content": "only me", "scope": "personal"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "personal"

    # 默认创建仍是组织模板（保持既有语义）
    r = await api.client.post(
        f"{PROMPTS}/", json={"name": "team-shared", "content": "team"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "organization"

    # 客户端不得伪造系统模板
    r = await api.client.post(
        f"{PROMPTS}/",
        json={"name": "fake-system", "content": "x", "scope": "system"},
    )
    assert r.status_code == 403, r.text

    # 个人模板只对创建者可见
    api.state.user = _make_user(uid=2, org=1)
    r = await api.client.get(f"{PROMPTS}/")
    names = {p["name"] for p in r.json()}
    assert "my-private" not in names
    assert "team-shared" in names


async def test_org_create_is_draft_for_non_admin_and_admin_create_is_audited(api, monkeypatch):
    """创建即激活不能绕过管理员激活门：普通创建者的组织模板必须落草稿。"""
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    from app.services.audit_service import audit_service

    monkeypatch.setattr(audit_service, "log_activity", _capture)

    # 普通用户请求 is_active=True 创建组织模板 → 服务端强制 False（草稿）
    r = await api.client.post(
        f"{PROMPTS}/",
        json={"name": "draft-org", "content": "x", "is_active": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "organization"
    assert r.json()["is_active"] is False
    assert not [c for c in calls if c["action"] == "activate_prompt"]

    # 管理员创建即激活 → 允许，且写 activate_prompt 审计
    api.state.user = _make_user(uid=10, org=1, role="admin")
    r = await api.client.post(
        f"{PROMPTS}/",
        json={"name": "live-org", "content": "x", "is_active": True},
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True
    assert any(
        c["action"] == "activate_prompt" and "live-org" in (c["detail"] or "")
        for c in calls
    )

    # 个人模板不受限：普通用户创建即可激活（只影响自己）
    api.state.user = _make_user(uid=1, org=1)
    r = await api.client.post(
        f"{PROMPTS}/",
        json={
            "name": "my-live",
            "content": "x",
            "scope": "personal",
            "is_active": True,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True


async def test_active_org_template_content_edits_require_admin(api):
    """评审 B4-02：管理员激活后的组织模板是线上配置，创建者也不能再改。"""
    org_id = await _id_by_name(api, "org-prompt")

    # 普通用户（即使创建者）不能改内容/删除
    r = await api.client.put(f"{PROMPTS}/{org_id}", json={"content": "hacked"})
    assert r.status_code == 403, r.text
    r = await api.client.delete(f"{PROMPTS}/{org_id}")
    assert r.status_code == 403, r.text

    # 管理员可以维护
    api.state.user = _make_user(uid=10, org=1, role="admin")
    r = await api.client.put(f"{PROMPTS}/{org_id}", json={"content": "admin edit"})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "admin edit"

    # 草稿态（未激活）组织模板仍由创建者维护
    api.state.user = _make_user(uid=1, org=1)
    r = await api.client.post(
        f"{PROMPTS}/", json={"name": "draft-editable", "content": "draft"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    draft_id = r.json()["id"]
    r = await api.client.put(f"{PROMPTS}/{draft_id}", json={"content": "draft v2"})
    assert r.status_code == 200, r.text
    assert r.json()["content"] == "draft v2"
