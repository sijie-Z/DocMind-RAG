"""工件只读分享 API（真实 SQLite + ASGI + 假 MinIO）。

判别点（每个用例标题写出变异点）：
- 创建/状态/公开元数据/公开正文/撤销全链；token 只回一次、DB 只存摘要；
- 公开响应字段白名单——去掉白名单（直接投影行）→ 内部字段断言红；
- 轮换：旧 token 立即 404、新 token 200（覆盖 token_hash → 旧链接失效）；
- 过期/撤销/不存在一律 404（去掉失效检查 → 红）；
- 版本绑定：工件产生 v2 后，旧分享仍返回 v1（跟随链头 → 红）；
- owner-only：同组织他人与跨组织创建/状态/撤销全 404；
- 开关关闭：创建与公开读取 404；撤销仍可用（安全操作）；
- 公开正文安全头逐项断言；限流超限 429。
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.artifact import AgentArtifact
from app.models.artifact_share import AgentArtifactShare
from app.models.organization import Organization
from app.services import artifact_service

ORG = 1
OTHER_ORG = 2
USER = 1
OTHER_USER = 2

_PUBLIC_KEYS = {
    "title",
    "kind",
    "filename",
    "mime_type",
    "version_no",
    "created_at",
    "content_url",
    "expires_at",
}


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifact-share-api.db'}")
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


@pytest.fixture(autouse=True)
def share_enabled(monkeypatch):
    monkeypatch.setattr(settings, "ARTIFACT_PUBLIC_SHARE_ENABLED", True)


@pytest.fixture(autouse=True)
def reset_rate_buckets():
    from app.api.v1.endpoints import public_artifacts

    public_artifacts._RATE_BUCKETS.clear()
    yield
    public_artifacts._RATE_BUCKETS.clear()


@pytest.fixture(autouse=True)
def grant_permissions(monkeypatch):
    async def _grant_all(_db, _user_obj, organization_id=None):
        from app.models.rbac import PermissionType

        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant_all)


class _Ctx:
    def __init__(self, user, factory, *, scopes=None):
        self.user = user
        self.factory = factory
        self.scopes = scopes

    async def __aenter__(self):
        from app.main import app

        user = self.user
        user.api_key_scopes = list(self.scopes) if self.scopes is not None else None

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


async def _create(client, **overrides):
    payload = {"kind": "markdown", "title": "报告", "content": "# 正文"}
    payload.update(overrides)
    resp = await client.post("/api/v1/artifacts", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _share(client, artifact_id, **body):
    return await client.post(f"/api/v1/artifacts/{artifact_id}/share", json=body)


async def test_create_status_public_revoke_roundtrip(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, content="# 分享正文", conversation_id="conv-share")
        resp = await _share(ac, created["id"])
        assert resp.status_code == 200, resp.text
        share_data = resp.json()["data"]
        token = share_data["token"]
        assert share_data["shared"] is True
        assert share_data["url"] == f"/share/artifact/{token}"
        assert share_data["expires_at"] is None

        status = await ac.get(f"/api/v1/artifacts/{created['id']}/share")
        assert status.status_code == 200, status.text
        status_data = status.json()["data"]
        assert status_data["shared"] is True
        assert "token" not in status_data, "状态端点不得回显 token"

        meta = await ac.get(f"/api/v1/public/artifacts/{token}")
        assert meta.status_code == 200, meta.text
        data = meta.json()["data"]
        assert set(data) == _PUBLIC_KEYS, f"公开元数据字段漂移: {set(data)}"
        assert data["title"] == "报告"
        assert data["kind"] == "markdown"
        assert data["version_no"] == 1
        assert data["content_url"] == f"/api/v1/public/artifacts/{token}/content"
        assert created["id"] not in meta.text

        content = await ac.get(f"/api/v1/public/artifacts/{token}/content")
        assert content.status_code == 200, content.text
        assert content.content.decode("utf-8") == "# 分享正文"

        revoked = await ac.delete(f"/api/v1/artifacts/{created['id']}/share")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["data"]["shared"] is False
        assert revoked.json()["data"]["revoked_at"]

        after = await ac.get(f"/api/v1/public/artifacts/{token}")
        assert after.status_code == 404, after.text
        content_after = await ac.get(f"/api/v1/public/artifacts/{token}/content")
        assert content_after.status_code == 404, content_after.text

    async with factory() as db:
        row = (
            await db.execute(
                select(AgentArtifactShare).where(AgentArtifactShare.artifact_id == created["id"])
            )
        ).scalar_one()
        # token 明文不落库：DB 只有 SHA-256 摘要。
        assert row.token_hash != token
        assert row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert row.revoked_at is not None


async def test_public_metadata_has_no_internal_fields(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, title="内部字段检查", conversation_id="conv-secret-xyz")
    async with _Ctx(_user(), factory) as ac:
        token = (await _share(ac, created["id"])).json()["data"]["token"]
        meta = await ac.get(f"/api/v1/public/artifacts/{token}")
        assert meta.status_code == 200, meta.text
        body = meta.json()["data"]
        for internal in (
            "id",
            "organization_id",
            "user_id",
            "conversation_id",
            "task_id",
            "agent_id",
            "content_sha256",
            "source_tool",
            "source_call_id",
            "replaces_artifact_id",
            "versions",
            "content",
        ):
            assert internal not in body, f"公开元数据泄露内部字段 {internal}"
        assert created["id"] not in meta.text
        assert "conv-secret-xyz" not in meta.text


async def test_rotate_invalidates_old_token(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, content="轮换正文")
        first = (await _share(ac, created["id"])).json()["data"]["token"]
        second_resp = await _share(ac, created["id"])
        assert second_resp.status_code == 200, second_resp.text
        second = second_resp.json()["data"]["token"]
        assert first != second

        assert (await ac.get(f"/api/v1/public/artifacts/{first}")).status_code == 404
        assert (await ac.get(f"/api/v1/public/artifacts/{second}")).status_code == 200
        # 撤销后重新创建 = 重新激活（轮换重置撤销状态），新 token 可用。
        await ac.delete(f"/api/v1/artifacts/{created['id']}/share")
        third = (await _share(ac, created["id"])).json()["data"]["token"]
        assert (await ac.get(f"/api/v1/public/artifacts/{third}")).status_code == 200


async def test_expiry_validation_and_expired_is_404(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, content="过期正文")
        for bad in (0, -1, 91):
            resp = await _share(ac, created["id"], expires_in_days=bad)
            assert resp.status_code == 400, (bad, resp.text)
            assert resp.json()["error_code"] == "VALIDATION_ERROR"

        resp = await _share(ac, created["id"], expires_in_days=90)
        assert resp.status_code == 200, resp.text
        token = resp.json()["data"]["token"]
        assert resp.json()["data"]["expires_at"] is not None

    async with factory() as db:
        row = (
            await db.execute(
                select(AgentArtifactShare).where(AgentArtifactShare.artifact_id == created["id"])
            )
        ).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        await db.commit()

    async with _Ctx(_user(), factory) as ac:
        assert (await ac.get(f"/api/v1/public/artifacts/{token}")).status_code == 404
        status = (await ac.get(f"/api/v1/artifacts/{created['id']}/share")).json()["data"]
        assert status["shared"] is False
        # 过期后再创建（轮换）恢复可用。
        fresh = (await _share(ac, created["id"], expires_in_days=1)).json()["data"]["token"]
        assert (await ac.get(f"/api/v1/public/artifacts/{fresh}")).status_code == 200


async def test_share_binds_specific_version_not_chain_head(factory):
    async with _Ctx(_user(), factory) as ac:
        v1 = await _create(ac, title="周报", content="v1 内容", conversation_id="conv-ver")
        token = (await _share(ac, v1["id"])).json()["data"]["token"]
        v2 = await _create(ac, title="周报", content="v2 内容", conversation_id="conv-ver")
        assert v2["version_no"] == 2

        meta = (await ac.get(f"/api/v1/public/artifacts/{token}")).json()["data"]
        assert meta["version_no"] == 1, "分享必须绑定创建时的那一版，不跟随链头"
        content = await ac.get(f"/api/v1/public/artifacts/{token}/content")
        assert content.content.decode("utf-8") == "v1 内容"


async def test_owner_only_same_org_and_cross_org_are_404(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, title="机密工件", content="绝密正文")
        token = (await _share(ac, created["id"])).json()["data"]["token"]

    for other in (_user(user_id=OTHER_USER), _user(org=OTHER_ORG, user_id=OTHER_USER)):
        async with _Ctx(other, factory) as ac:
            for method, path in (
                ("post", f"/api/v1/artifacts/{created['id']}/share"),
                ("get", f"/api/v1/artifacts/{created['id']}/share"),
                ("delete", f"/api/v1/artifacts/{created['id']}/share"),
            ):
                resp = await getattr(ac, method)(path, **({"json": {}} if method == "post" else {}))
                assert resp.status_code == 404, (method, resp.text)
                assert "机密工件" not in resp.text
                assert "绝密正文" not in resp.text
        # 他人 404 不得影响真实所有者的链接。
        async with _Ctx(_user(), factory) as ac:
            assert (await ac.get(f"/api/v1/public/artifacts/{token}")).status_code == 200


async def test_disabled_switch_create_and_public_are_404(monkeypatch, factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, content="开关正文")
        token = (await _share(ac, created["id"])).json()["data"]["token"]

        monkeypatch.setattr(settings, "ARTIFACT_PUBLIC_SHARE_ENABLED", False)
        assert (await _share(ac, created["id"])).status_code == 404
        assert (await ac.get(f"/api/v1/public/artifacts/{token}")).status_code == 404
        assert (await ac.get(f"/api/v1/public/artifacts/{token}/content")).status_code == 404
        # 撤销/状态始终可用：撤链接是安全操作，不受功能开关影响。
        status = await ac.get(f"/api/v1/artifacts/{created['id']}/share")
        assert status.status_code == 200, status.text
        assert (await ac.delete(f"/api/v1/artifacts/{created['id']}/share")).status_code == 200


async def test_missing_scope_is_403(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, title="权限工件")
    async with _Ctx(_user(), factory, scopes=["rpc"]) as ac:
        resp = await _share(ac, created["id"])
        assert resp.status_code == 403, resp.text
        assert (await ac.get(f"/api/v1/artifacts/{created['id']}/share")).status_code == 403
        assert (await ac.delete(f"/api/v1/artifacts/{created['id']}/share")).status_code == 403


async def test_unknown_and_oversized_tokens_are_404(factory):
    async with _Ctx(_user(), factory) as ac:
        assert (await ac.get("/api/v1/public/artifacts/not-a-real-token")).status_code == 404
        assert (await ac.get(f"/api/v1/public/artifacts/{'x' * 200}")).status_code == 404
        assert (
            await ac.get("/api/v1/public/artifacts/not-a-real-token/content")
        ).status_code == 404


async def test_public_content_security_headers(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, kind="html", title="页面", content="<script>alert(1)</script>")
        token = (await _share(ac, created["id"])).json()["data"]["token"]

    async with factory() as db:
        row = await db.get(AgentArtifact, created["id"])
        row.filename = None  # 触发无文件名兜底：兜底串不得含内部 id
        await db.commit()

    async with _Ctx(_user(), factory) as ac:
        content = await ac.get(f"/api/v1/public/artifacts/{token}/content")
        assert content.status_code == 200, content.text
        assert content.content.decode("utf-8") == "<script>alert(1)</script>"
        assert content.headers["x-content-type-options"] == "nosniff"
        assert content.headers["content-security-policy"] == (
            "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        )
        assert content.headers["referrer-policy"] == "no-referrer"
        assert content.headers["x-robots-tag"] == "noindex"
        assert content.headers["cache-control"] == "no-store"
        disposition = content.headers["content-disposition"]
        assert disposition.startswith("inline;")
        assert "filename*=UTF-8''" in disposition
        assert created["id"] not in disposition


async def test_rate_limit_returns_429(monkeypatch, factory):
    monkeypatch.setattr(settings, "ARTIFACT_PUBLIC_RATE_LIMIT_PER_MINUTE", 1)
    async with _Ctx(_user(), factory) as ac:
        created = await _create(ac, content="限流正文")
        token = (await _share(ac, created["id"])).json()["data"]["token"]

        first = await ac.get(f"/api/v1/public/artifacts/{token}")
        assert first.status_code == 200, first.text
        second = await ac.get(f"/api/v1/public/artifacts/{token}")
        assert second.status_code == 429, second.text
        assert second.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
