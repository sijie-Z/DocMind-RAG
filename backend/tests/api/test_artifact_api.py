"""工件 API（真实 SQLite + ASGI + 假 MinIO）：创建/列表/详情/正文/ACL/跨组织/版本链。

判别点（每个用例标题写出变异点）：
- 创建 → 列表 → 详情 → 正文全链；正文 MIME 与字节逐字一致（伪造 MIME → 红）；
- 跨组织一律 404 且不回显标题/内容（去掉 org 条件 → 红）；
- 同组织不同用户 404（用户级隔离）；超管平台口径可读；
- 同标题新内容 → v2，详情返回完整版本链（删版本链 → 红）；
- 普通用户缺 chat:read scope 403（去掉 require_scope → 红）；
- 缺组织 403（fail-closed）；
- 正文只读 ready（状态非 ready → 404）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.artifact import AgentArtifact
from app.models.organization import Organization
from app.services import artifact_service

ORG = 1
OTHER_ORG = 2
USER = 1
OTHER_USER = 2


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifact-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=USER))
        db.add(Organization(id=OTHER_ORG, name="Other", owner_id=OTHER_USER))
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    store: dict[str, bytes] = {}

    def _put(object_name, data, length, content_type="application/octet-stream", **kwargs):
        store[object_name] = data.read()
        return object_name

    class _Response:
        def __init__(self, payload: bytes):
            self._payload = payload

        def read(self):
            return self._payload

        def close(self):
            return None

        def release_conn(self):
            return None

    def _get(object_name):
        if object_name not in store:
            raise KeyError(object_name)
        return _Response(store[object_name])

    monkeypatch.setattr(artifact_service.minio_client, "put_object", _put)
    monkeypatch.setattr(artifact_service.minio_client, "get_object", _get)
    return store


class _Ctx:
    def __init__(self, user, factory, *, scopes=None):
        self.user = user
        self.factory = factory
        self.scopes = scopes

    async def __aenter__(self):
        from app.main import app

        user = self.user
        if self.scopes is not None:
            user.api_key_scopes = list(self.scopes)
        else:
            user.api_key_scopes = None

        async def _user():
            return user

        async def _db():
            async with self.factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://artifact")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(org=ORG, *, user_id=USER, superuser=False):
    return SimpleNamespace(
        id=user_id,
        username=f"tester{user_id}",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="user",
    )


@pytest.fixture(autouse=True)
def grant_permissions(monkeypatch):
    """工件端点要求 document:read；本测试只关心租户/用户隔离，权限统一放行。"""

    async def _grant_all(_db, _user_obj, organization_id=None):
        from app.models.rbac import PermissionType

        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant_all)


async def _create(client, **overrides):
    payload = {"kind": "markdown", "title": "报告", "content": "# 正文"}
    payload.update(overrides)
    resp = await client.post("/api/v1/artifacts", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_create_list_detail_content_roundtrip(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, content="# 第一版", conversation_id="conv-1")
        assert created["created"] is True
        assert created["kind"] == "markdown"
        assert created["version_no"] == 1
        assert created["content_url"] == f"/api/v1/artifacts/{created['id']}/content"

        listed = await ac.get("/api/v1/artifacts", params={"conversation_id": "conv-1"})
        assert listed.status_code == 200, listed.text
        data = listed.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["id"] == created["id"]

        detail = await ac.get(f"/api/v1/artifacts/{created['id']}")
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert detail_data["title"] == "报告"
        assert [v["version_no"] for v in detail_data["versions"]] == [1]

        content = await ac.get(f"/api/v1/artifacts/{created['id']}/content")
        assert content.status_code == 200, content.text
        assert content.content.decode("utf-8") == "# 第一版"
        assert content.headers["content-type"].startswith("text/markdown")
        assert content.headers["x-content-type-options"] == "nosniff"
        assert "attachment" not in content.headers["content-disposition"]
        assert "filename*=UTF-8''" in content.headers["content-disposition"]


async def test_version_chain_via_api(factory):
    async with _Ctx(_user(), factory) as ac:
        v1 = await _create(ac, title="周报", content="v1 内容", conversation_id="conv-2")
        v2 = await _create(ac, title="周报", content="v2 内容", conversation_id="conv-2")
        assert v2["version_no"] == 2
        assert v2["replaces_artifact_id"] == v1["id"]
        assert v2["id"] != v1["id"]

        detail = await ac.get(f"/api/v1/artifacts/{v2['id']}")
        versions = detail.json()["data"]["versions"]
        assert [v["version_no"] for v in versions] == [1, 2]
        assert versions[0]["content_sha256"] != versions[1]["content_sha256"]


async def test_list_filters_by_task_and_conversation(factory):
    async with _Ctx(_user(), factory) as ac:
        await _create(ac, title="A", conversation_id="conv-a")
        await _create(ac, title="B")
        listed = await ac.get("/api/v1/artifacts", params={"conversation_id": "conv-a"})
        items = listed.json()["data"]["items"]
        assert [item["title"] for item in items] == ["A"]
        listed_task = await ac.get("/api/v1/artifacts", params={"task_id": "task-none"})
        assert listed_task.json()["data"]["total"] == 0


async def test_cross_org_is_404_and_does_not_leak(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, title="机密工件", content="绝密正文")
    async with _Ctx(_user(org=OTHER_ORG, user_id=OTHER_USER), factory) as ac:
        for path in (
            f"/api/v1/artifacts/{created['id']}",
            f"/api/v1/artifacts/{created['id']}/content",
        ):
            resp = await ac.get(path)
            assert resp.status_code == 404, resp.text
            assert "机密工件" not in resp.text
            assert "绝密正文" not in resp.text
        listed = await ac.get("/api/v1/artifacts")
        assert listed.json()["data"]["total"] == 0


async def test_same_org_other_user_is_404(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, title="个人工件")
    async with _Ctx(_user(user_id=OTHER_USER), factory) as ac:
        resp = await ac.get(f"/api/v1/artifacts/{created['id']}")
        assert resp.status_code == 404, resp.text
        listed = await ac.get("/api/v1/artifacts")
        assert listed.json()["data"]["total"] == 0


async def test_superuser_reads_across_users(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, title="跨用户")
    async with _Ctx(_user(superuser=True), factory) as ac:
        resp = await ac.get(f"/api/v1/artifacts/{created['id']}")
        assert resp.status_code == 200, resp.text


async def test_missing_scope_is_403(factory):
    async with _Ctx(_user(), factory, scopes=["rpc"]) as ac:
        resp = await ac.get("/api/v1/artifacts")
        assert resp.status_code == 403, resp.text
        created_payload = {"kind": "text", "title": "t", "content": "c"}
        post = await ac.post("/api/v1/artifacts", json=created_payload)
        assert post.status_code == 403, post.text


async def test_missing_organization_is_403(factory):
    async with _Ctx(_user(org=None), factory) as ac:
        resp = await ac.get("/api/v1/artifacts")
        assert resp.status_code == 403, resp.text


async def test_only_ready_artifacts_are_readable(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, title="待清理")
    async with factory() as db:
        row = await db.get(AgentArtifact, created["id"])
        row.status = "pending"
        await db.commit()
    async with _Ctx(_user(), factory) as ac:
        resp = await ac.get(f"/api/v1/artifacts/{created['id']}/content")
        assert resp.status_code == 404, resp.text


async def test_create_rejects_invalid_kind_and_oversize(factory):
    async with _Ctx(_user(), factory) as ac:
        bad_kind = await ac.post(
            "/api/v1/artifacts", json={"kind": "exe", "title": "t", "content": "c"}
        )
        assert bad_kind.status_code == 400, bad_kind.text
        assert bad_kind.json()["error_code"] == "VALIDATION_ERROR"
        oversize = await ac.post(
            "/api/v1/artifacts",
            json={"kind": "text", "title": "t", "content": "a" * (512 * 1024 + 1)},
        )
        assert oversize.status_code == 400, oversize.text


async def test_unknown_artifact_is_404(factory):
    async with _Ctx(_user(), factory) as ac:
        resp = await ac.get("/api/v1/artifacts/does-not-exist")
        assert resp.status_code == 404, resp.text
