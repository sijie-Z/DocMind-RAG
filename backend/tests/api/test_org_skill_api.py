"""组织技能库 API（批次十）：权限 / 状态机 / 租户隔离 / 审计最小化。

判别点：
- 读需 chat:read、提议需 chat:write；API Key 审核/启用一律 403（require_jwt_user）；
- 普通成员审核 403；作者自审 403；重复审核 409；
- 跨组织列表/详情/版本/diff/审核/启用 404（不泄露存在性）；
- 提议→审核→启用→禁用→回滚→归档全链；pending 不能启用、归档后不能启用；
- 不安全 payload 400；
- 审计 detail 只记元数据，绝不包含 payload / 触发短语正文。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agent.service  # noqa: F401 — 触发全部工具注册
import app.agent.tools  # noqa: F401
import app.models  # noqa: F401
from app.agent.checkpoint import build_envelope
from app.agent.exec_context import ExecutionContext
from app.agent.planner import Plan, PlanStep
from app.api.v1.endpoints import org_skills as org_skills_ep
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.exceptions import AuthorizationError
from app.models.agent_task import AgentTask, TaskStatus
from app.models.organization import Organization
from app.models.user import User
from app.services.audit_service import audit_service

ORG = 1
OTHER_ORG = 2
AUTHOR = 1
ADMIN = 2
MEMBER = 3

TRIGGER = "机密触发词ALPHA"
PAYLOAD = {
    "schema_version": 1,
    "trigger_phrases": [TRIGGER],
    "tool_sequence": [{"tool_name": "search_knowledge_base"}],
}


def _real_snapshot():
    """生产真实形状：checkpoint v1 信封（步骤在 ctx.steps）。"""
    ctx = ExecutionContext(query="总结季度财务报告")
    ctx.complete_step(
        step_id="s1",
        description="检索",
        tool="web_search",
        status="completed",
        duration=10.0,
    )
    plan = Plan(id="p1", goal="总结季度财务报告", steps=[PlanStep(id="s1", description="检索")])
    return build_envelope(ctx=ctx, plan=plan, trigger="test")


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'org-skill-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                User(id=AUTHOR, username="author", email="a@example.com", hashed_password="x"),
                User(id=ADMIN, username="admin", email="b@example.com", hashed_password="x"),
                User(id=MEMBER, username="member", email="c@example.com", hashed_password="x"),
            ]
        )
        await db.flush()
        db.add_all(
            [
                Organization(id=ORG, name="Org", owner_id=AUTHOR),
                Organization(id=OTHER_ORG, name="Other", owner_id=MEMBER),
            ]
        )
        db.add(
            AgentTask(
                id="task-ok",
                user_id=AUTHOR,
                organization_id=ORG,
                status=TaskStatus.COMPLETED,
                query="总结季度财务报告",
                context_snapshot=_real_snapshot(),
            )
        )
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def captured_audit(monkeypatch):
    calls: list[dict] = []

    async def _log(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(audit_service, "log_activity", _log)
    return calls


@pytest.fixture
def admin_ids():
    return {ADMIN}


@pytest.fixture(autouse=True)
def patch_admin_gate(monkeypatch, admin_ids):
    async def _gate(_db, user, _organization_id):
        if user.id not in admin_ids:
            raise AuthorizationError("需要组织管理员权限")

    monkeypatch.setattr(org_skills_ep, "require_org_admin", _gate)


class _Ctx:
    def __init__(self, user, factory, *, scopes=None, api_key_id=None):
        self.user = user
        self.factory = factory
        self.scopes = scopes
        self.api_key_id = api_key_id

    async def __aenter__(self):
        from app.main import app

        user = self.user
        user.api_key_scopes = list(self.scopes) if self.scopes is not None else None
        user.api_key_id = self.api_key_id

        async def _user():
            return user

        async def _db():
            async with self.factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://org-skill")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(user_id=AUTHOR, org=ORG, *, superuser=False):
    return SimpleNamespace(
        id=user_id,
        username=f"user{user_id}",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="user",
    )


async def _propose(client, **overrides):
    body = {"name": "季度总结", "payload": PAYLOAD}
    body.update(overrides)
    resp = await client.post("/api/v1/org-skills/proposals", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _approve(client, skill_id, version_no, note=None):
    return await client.post(
        f"/api/v1/org-skills/{skill_id}/versions/{version_no}/approve",
        json={"note": note},
    )


async def _reject(client, skill_id, version_no, note=None):
    return await client.post(
        f"/api/v1/org-skills/{skill_id}/versions/{version_no}/reject",
        json={"note": note},
    )


async def test_full_flow_propose_review_enable_disable_rollback_archive(factory):
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]
        assert created["version"]["review_status"] == "pending"

        listed = await ac.get("/api/v1/org-skills")
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 1

        detail = await ac.get(f"/api/v1/org-skills/{skill_id}")
        assert detail.status_code == 200
        assert detail.json()["data"]["versions"][0]["review_status"] == "pending"

        version = await ac.get(f"/api/v1/org-skills/{skill_id}/versions/1")
        assert version.status_code == 200
        assert version.json()["data"]["payload"]["trigger_phrases"] == [TRIGGER]

        second = await _propose(
            ac,
            skill_id=skill_id,
            payload={
                "schema_version": 1,
                "trigger_phrases": [TRIGGER, "分析现金流"],
                "tool_sequence": [
                    {"tool_name": "search_knowledge_base"},
                    {"tool_name": "web_search"},
                ],
            },
        )
        assert second["version"]["version_no"] == 2

        diff = await ac.get(
            f"/api/v1/org-skills/{skill_id}/diff?base_version_no=1&target_version_no=2"
        )
        assert diff.status_code == 200
        diff_data = diff.json()["data"]
        assert diff_data["trigger_phrases"]["added"] == ["分析现金流"]
        assert diff_data["tool_sequence"]["added"] == ["web_search"]

    async with _Ctx(_user(ADMIN), factory) as ac:
        approved = await _approve(ac, skill_id, 1)
        assert approved.status_code == 200, approved.text
        assert approved.json()["data"]["review_status"] == "approved"

        rejected = await _reject(ac, skill_id, 2, note="触发词过宽")
        assert rejected.status_code == 200
        assert rejected.json()["data"]["review_status"] == "rejected"

        enabled = await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["data"]["enabled_version_no"] == 1

        disabled = await ac.post(f"/api/v1/org-skills/{skill_id}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["data"]["enabled_version_no"] is None

        await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})
        # 只有 v1 是 approved；v2 被拒，回滚无更早目标 → 409。
        rollback = await ac.post(f"/api/v1/org-skills/{skill_id}/rollback", json={})
        assert rollback.status_code == 409

        archived = await ac.post(f"/api/v1/org-skills/{skill_id}/archive")
        assert archived.status_code == 200
        assert archived.json()["data"]["status"] == "archived"
        assert archived.json()["data"]["enabled_version_no"] is None

        reenable = await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})
        assert reenable.status_code == 409, "归档后不得再启用"


async def test_author_self_review_forbidden(factory, admin_ids):
    admin_ids.add(AUTHOR)
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]
        resp = await _approve(ac, skill_id, 1)
        assert resp.status_code == 403, resp.text
        assert resp.json()["error_code"] == "ORG_SKILL_SELF_REVIEW"


async def test_member_cannot_review_or_enable(factory):
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]

    async with _Ctx(_user(MEMBER), factory) as ac:
        assert (await _approve(ac, skill_id, 1)).status_code == 403
        assert (
            await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})
        ).status_code == 403
        assert (await ac.post(f"/api/v1/org-skills/{skill_id}/archive")).status_code == 403


async def test_api_key_cannot_review_or_enable(factory):
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]
    async with _Ctx(_user(ADMIN), factory) as ac:
        assert (await _approve(ac, skill_id, 1)).status_code == 200

    async with _Ctx(
        _user(ADMIN), factory, scopes=["chat:read", "chat:write"], api_key_id="key-1"
    ) as ac:
        review = await _approve(ac, skill_id, 1)
        assert review.status_code == 403, review.text
        enable = await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})
        assert enable.status_code == 403, enable.text
        archive = await ac.post(f"/api/v1/org-skills/{skill_id}/archive")
        assert archive.status_code == 403, archive.text


async def test_cross_org_is_404(factory, admin_ids):
    admin_ids.add(AUTHOR)
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]

    # 目标组织的管理员跨组织访问：先通过本组织管理员门，再由资源查询 404。
    admin_ids.add(MEMBER)
    async with _Ctx(_user(MEMBER, org=OTHER_ORG), factory) as ac:
        assert (await ac.get(f"/api/v1/org-skills/{skill_id}")).status_code == 404
        assert (await ac.get(f"/api/v1/org-skills/{skill_id}/versions/1")).status_code == 404
        assert (
            await ac.get(
                f"/api/v1/org-skills/{skill_id}/diff?base_version_no=1&target_version_no=1"
            )
        ).status_code == 404
        assert (await _approve(ac, skill_id, 1)).status_code == 404
        assert (
            await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})
        ).status_code == 404
        listed = await ac.get("/api/v1/org-skills")
        assert listed.json()["data"]["total"] == 0


async def test_cross_org_source_task_is_404(factory):
    async with _Ctx(_user(MEMBER, org=OTHER_ORG), factory) as ac:
        resp = await ac.post(
            "/api/v1/org-skills/proposals",
            json={"name": "偷任务", "source_task_id": "task-ok"},
        )
        assert resp.status_code == 404, resp.text


async def test_source_task_proposal_uses_real_checkpoint_envelope(factory):
    """真实 checkpoint v1 信封（ctx.steps）→ pending payload；B1 的 API 级判别。"""
    async with _Ctx(_user(AUTHOR), factory) as ac:
        resp = await ac.post(
            "/api/v1/org-skills/proposals",
            json={"name": "运行沉淀", "source_task_id": "task-ok"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["version"]["review_status"] == "pending"
        assert data["version"]["source"] == "run_proposal"
        assert data["version"]["payload"]["trigger_phrases"] == ["总结季度财务报告"]
        assert data["version"]["payload"]["tool_sequence"] == [{"tool_name": "web_search"}]


async def test_duplicate_review_conflicts(factory):
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]
    async with _Ctx(_user(ADMIN), factory) as ac:
        assert (await _approve(ac, skill_id, 1)).status_code == 200
        again = await _approve(ac, skill_id, 1)
        assert again.status_code == 409, again.text


async def test_pending_cannot_be_enabled(factory):
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]
    async with _Ctx(_user(ADMIN), factory) as ac:
        resp = await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})
        assert resp.status_code == 409, resp.text


async def test_read_scope_enforced(factory):
    async with _Ctx(_user(AUTHOR), factory, scopes=["chat:write"], api_key_id="k2") as ac:
        assert (await ac.get("/api/v1/org-skills")).status_code == 403

    async with _Ctx(_user(AUTHOR), factory, scopes=["chat:read"], api_key_id="k3") as ac:
        assert (await ac.get("/api/v1/org-skills")).status_code == 200
        # 读 scope 不能提议。
        resp = await ac.post("/api/v1/org-skills/proposals", json={"name": "x", "payload": PAYLOAD})
        assert resp.status_code == 403


async def test_unsafe_payload_is_400(factory):
    async with _Ctx(_user(AUTHOR), factory) as ac:
        unsafe = dict(PAYLOAD)
        unsafe["trigger_phrases"] = ["访问 https://example.com"]
        resp = await ac.post(
            "/api/v1/org-skills/proposals", json={"name": "危险", "payload": unsafe}
        )
        assert resp.status_code == 400, resp.text
        assert resp.json()["error_code"] == "ORG_SKILL_PAYLOAD_INVALID"


async def test_audit_detail_never_contains_payload_or_trigger(factory, captured_audit):
    async with _Ctx(_user(AUTHOR), factory) as ac:
        created = await _propose(ac)
        skill_id = created["skill"]["id"]
    async with _Ctx(_user(ADMIN), factory) as ac:
        await _approve(ac, skill_id, 1)
        await ac.post(f"/api/v1/org-skills/{skill_id}/enable", json={"version_no": 1})

    assert captured_audit, "状态变更必须写审计"
    joined = " ".join(str(call.get("detail") or "") for call in captured_audit)
    assert TRIGGER not in joined, "审计不得记录触发短语正文"
    assert "tool_sequence" not in joined, "审计不得记录 payload"
    for call in captured_audit:
        detail = json.loads(call["detail"])
        assert "organization_id" in detail and "user_id" in detail
        assert "skill_id" in detail or call["target_type"] == "org_skill"
