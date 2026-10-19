"""B4 组织设置 API 测试（sqlite 全链 + dependency_overrides；不连真实服务）。

覆盖：
- GET 生效值/来源/是否需重启（无行时全部回退平台默认）；
- PUT 权限：普通成员 403，组织管理员可写，跨组织 403，超管旁路；
- 写入持久化 + 审计（action/target/detail）+ 审计读取端点；
- 校验边界（top_k 1..50、quota >= 0）；
- 检索 top_k 真实消费：组织设置 > 显式请求 > 既有默认（settings.TOP_K_RESULTS）；
- chat 检索的 top_k 解析助手：有组织设置用设置值，否则回退调用方既有默认。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.config import settings
from app.core.database import Base
from app.models.organization import Organization
from app.models.organization_settings import OrganizationSettings
from app.models.rbac import (
    PermissionType,
    Role,
    user_organization_role_association,
)
from app.models.user_audit import UserActivityLog

ORGS = "/api/v1/organizations"
SEARCH = "/api/v1/knowledge/search"


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
                Organization(id=2, name="org-b", owner_id=2),
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
            user_organization_role_association.insert(),
            # 只授 org 1：跨组织 403 才能证明 per-org 管理员边界，
            # 而不是被“同一个人也是 org 2 管理员”混淆（W0-1 评审 M2/M5）。
            [{"user_id": 10, "organization_id": 1, "role_id": org_admin_role.id}],
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


def _admin_state(api, org=1):
    api.state.user = _make_user(uid=10, org=org, role="admin")


async def _put(api, payload: dict, org=1):
    return await api.client.put(f"{ORGS}/{org}/settings", json=payload)


# ── GET：生效值 / 来源 / 是否需重启 ─────────────────────────────────────


async def test_get_defaults_for_member(api):
    r = await api.client.get(f"{ORGS}/1/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["organization_id"] == 1
    assert set(body["settings"]) == {
        "default_provider",
        "default_model",
        "retrieval_top_k",
        "allow_privacy_mode",
        "monthly_token_quota",
        "tool_whitelist",
    }
    for entry in body["settings"].values():
        assert entry["source"] == "default"
        assert isinstance(entry["requires_restart"], bool)
    assert body["settings"]["retrieval_top_k"]["value"] == settings.TOP_K_RESULTS
    assert body["settings"]["allow_privacy_mode"]["value"] is True
    assert body["settings"]["monthly_token_quota"]["value"] is None
    assert body["settings"]["tool_whitelist"]["value"] is None
    assert body["usage"]["month_tokens"] == 0


# ── PUT：权限 / 持久化 / 审计 ───────────────────────────────────────────


async def test_put_requires_org_admin(api):
    r = await _put(api, {"retrieval_top_k": 3})
    assert r.status_code == 403, r.text


async def test_put_by_admin_persists_and_audits(api, monkeypatch):
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    from app.services.audit_service import audit_service

    monkeypatch.setattr(audit_service, "log_activity", _capture)

    _admin_state(api)
    r = await _put(
        api,
        {
            "default_provider": "deepseek",
            "default_model": "deepseek-v4-flash",
            "retrieval_top_k": 3,
            "allow_privacy_mode": False,
            "monthly_token_quota": 100000,
            "tool_whitelist": ["search_knowledge_base", "web_search"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["settings"]["retrieval_top_k"] == {
        "value": 3,
        "source": "organization",
        "requires_restart": False,
    }
    assert body["settings"]["allow_privacy_mode"]["value"] is False
    assert body["settings"]["allow_privacy_mode"]["source"] == "organization"
    assert body["settings"]["tool_whitelist"]["value"] == [
        "search_knowledge_base",
        "web_search",
    ]

    async with api.factory() as s:
        row = (
            await s.execute(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == 1
                )
            )
        ).scalars().one()
        assert row.retrieval_top_k == 3
        assert row.allow_privacy_mode is False
        assert row.monthly_token_quota == 100000

    assert calls, "写入组织设置必须写审计"
    audit = calls[-1]
    assert audit["action"] == "update_organization_settings"
    assert audit["target_type"] == "organization_settings"
    assert audit["target_id"] == "1"
    assert "retrieval_top_k" in (audit["detail"] or "")

    # 再读：来源切到 organization
    r2 = await api.client.get(f"{ORGS}/1/settings")
    assert r2.status_code == 200
    assert r2.json()["settings"]["retrieval_top_k"]["source"] == "organization"


async def test_cross_org_is_forbidden_and_superuser_bypasses(api):
    # 组织 1 的 admin 不能读写组织 2
    _admin_state(api, org=1)
    assert (await api.client.get(f"{ORGS}/2/settings")).status_code == 403
    assert (await _put(api, {"retrieval_top_k": 3}, org=2)).status_code == 403

    # 普通成员同样不能读其他组织
    api.state.user = _make_user(uid=2, org=1)
    assert (await api.client.get(f"{ORGS}/2/settings")).status_code == 403

    # 超管旁路
    api.state.user = _make_user(uid=99, org=None, superuser=True)
    assert (await api.client.get(f"{ORGS}/2/settings")).status_code == 200
    assert (await _put(api, {"retrieval_top_k": 4}, org=2)).status_code == 200


async def test_update_validates_bounds(api):
    _admin_state(api)
    assert (await _put(api, {"retrieval_top_k": 0})).status_code == 422
    assert (await _put(api, {"retrieval_top_k": 51})).status_code == 422
    assert (await _put(api, {"monthly_token_quota": -1})).status_code == 422
    assert (await _put(api, {"tool_whitelist": [123]})).status_code == 422


# ── 审计读取 ────────────────────────────────────────────────────────────


async def test_get_settings_audit_entries(api):
    async with api.factory() as s:
        s.add_all(
            [
                UserActivityLog(
                    user_id=10,
                    action="update_organization_settings",
                    target_type="organization_settings",
                    target_id="1",
                    detail='{"retrieval_top_k": {"old": null, "new": 3}}',
                ),
                UserActivityLog(
                    user_id=20,
                    action="update_organization_settings",
                    target_type="organization_settings",
                    target_id="2",
                    detail='{"retrieval_top_k": {"old": null, "new": 4}}',
                ),
            ]
        )
        await s.commit()

    # 普通成员不可读审计
    r = await api.client.get(f"{ORGS}/1/settings/audit")
    assert r.status_code == 403

    _admin_state(api)
    r = await api.client.get(f"{ORGS}/1/settings/audit")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["action"] == "update_organization_settings"
    assert items[0]["target_id"] == "1"


# ── 检索 top_k 真实消费 ─────────────────────────────────────────────────


def _patched_permissions():
    return patch(
        "app.services.permission_service.permission_service.get_user_permissions",
        new=AsyncMock(return_value={p.value for p in PermissionType}),
    )


def _fake_search_result() -> list[dict]:
    return [
        {
            "id": "c1",
            "text": "chunk",
            "score": 0.9,
            "source": {"filename": "a.pdf"},
        }
    ]


async def test_search_top_k_uses_org_setting(api):
    _admin_state(api)
    assert (await _put(api, {"retrieval_top_k": 3})).status_code == 200

    api.state.user = _make_user(uid=2, org=1)
    with (
        _patched_permissions(),
        patch(
            "app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge",
            new=AsyncMock(return_value=_fake_search_result()),
        ) as mocked,
    ):
        r = await api.client.post(
            SEARCH, json={"query": "报销", "organization_id": 1}
        )
    assert r.status_code == 200, r.text
    assert mocked.await_args.kwargs["top_k"] == 3


async def test_search_top_k_falls_back_to_existing_default(api):
    api.state.user = _make_user(uid=2, org=1)
    with (
        _patched_permissions(),
        patch(
            "app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge",
            new=AsyncMock(return_value=_fake_search_result()),
        ) as mocked,
    ):
        r = await api.client.post(
            SEARCH, json={"query": "报销", "organization_id": 1}
        )
    assert r.status_code == 200, r.text
    # 未设置组织值时不得改变既有默认语义：仍为 settings.TOP_K_RESULTS（10）
    assert mocked.await_args.kwargs["top_k"] == settings.TOP_K_RESULTS


async def test_search_explicit_top_k_wins_over_org_setting(api):
    _admin_state(api)
    assert (await _put(api, {"retrieval_top_k": 3})).status_code == 200

    api.state.user = _make_user(uid=2, org=1)
    with (
        _patched_permissions(),
        patch(
            "app.api.v1.endpoints.knowledge.knowledge_service.search_knowledge",
            new=AsyncMock(return_value=_fake_search_result()),
        ) as mocked,
    ):
        r = await api.client.post(
            SEARCH, json={"query": "报销", "organization_id": 1, "top_k": 7}
        )
    assert r.status_code == 200, r.text
    assert mocked.await_args.kwargs["top_k"] == 7


async def test_chat_retrieval_top_k_helper(api):
    from app.services.organization_settings_service import organization_settings_service

    async with api.factory() as s:
        s.add(
            OrganizationSettings(
                organization_id=1,
                retrieval_top_k=3,
                tool_whitelist='["search_knowledge_base"]',
            )
        )
        await s.commit()

    async with api.factory() as s:
        # 组织有设置：使用组织值，覆盖调用方既有默认（chat 文档 8 / 通用 5）
        assert (
            await organization_settings_service.resolve_retrieval_top_k(s, 1, fallback=8)
            == 3
        )
        # 组织无设置：回退调用方既有默认，不得改变默认语义
        assert (
            await organization_settings_service.resolve_retrieval_top_k(s, 2, fallback=5)
            == 5
        )
        # 无组织上下文：回退既有默认
        assert (
            await organization_settings_service.resolve_retrieval_top_k(
                s, None, fallback=5
            )
            == 5
        )

        # 隐私策略：未设置 = 允许（保持既有默认）；显式关闭 = False
        assert (
            await organization_settings_service.allow_privacy_mode(s, 1) is True
        )
        row = (
            await s.execute(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == 1
                )
            )
        ).scalars().one()
        row.allow_privacy_mode = False
        await s.commit()
        assert (
            await organization_settings_service.allow_privacy_mode(s, 1) is False
        )


async def test_org_delete_cleans_settings(api):
    """组织设置是配置附属：删除组织时必须清理，不得因新外键 1451 阻塞删除。"""
    from app.services.organization_service import organization_service

    async with api.factory() as s:
        s.add(OrganizationSettings(organization_id=1, retrieval_top_k=3))
        await s.commit()

    async with api.factory() as s:
        assert await organization_service.delete_organization_thoroughly(s, 1) is True
        await s.commit()

    async with api.factory() as s:
        remaining = (await s.execute(select(OrganizationSettings))).scalars().all()
        assert remaining == []


async def test_put_explicit_null_on_missing_row_creates_no_row(api):
    """评审 B4-06：显式 null = 清除覆盖；行不存在时是无操作，不得凭空建行。"""
    _admin_state(api)
    r = await _put(api, {"retrieval_top_k": None})
    assert r.status_code == 200, r.text
    async with api.factory() as s:
        rows = (await s.execute(select(OrganizationSettings))).scalars().all()
        assert rows == []
    assert r.json()["settings"]["retrieval_top_k"]["source"] == "default"


async def test_put_explicit_null_clears_existing_override(api):
    _admin_state(api)
    assert (await _put(api, {"retrieval_top_k": 3})).status_code == 200
    r = await _put(api, {"retrieval_top_k": None})
    assert r.status_code == 200, r.text
    entry = r.json()["settings"]["retrieval_top_k"]
    assert entry["source"] == "default"
    assert entry["value"] == settings.TOP_K_RESULTS
    async with api.factory() as s:
        row = (
            await s.execute(
                select(OrganizationSettings).where(
                    OrganizationSettings.organization_id == 1
                )
            )
        ).scalars().one()
        assert row.retrieval_top_k is None
