"""公开知识库 API（批次十三）：真实 SQLite + ASGI + 假 RAG pipeline。

判别点（每个用例标题写出变异点）：
- 创建/状态/公开元数据/公开检索/轮换/撤销全链；token 只回一次、DB 只存摘要；
- 公开响应字段白名单——去掉白名单（直接投影 hit/发布行）→ 内部字段断言红；
- 检索结果只含显式白名单文档（篡改 pipeline 返回未发布/跨组织文档 → 红）；
- 撤销/过期/禁用/开关关闭/不存在一律 404 同形；
- 管理面：非管理员 403、跨组织 404；白名单校验组织/KB/INDEXED/启用；
- snippet 去 HTML（含 script/style 内容）且有界；query/top_k 上限；
- 限流 429；审计只记元数据（无 token/正文），审计失败不影响 200；
- 开关关闭时创建/公开读取 404，但状态/撤销仍可用（安全操作）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api.v1.endpoints import public_kb as pb_endpoints
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.knowledge_base import KnowledgeBase
from app.models.organization import Organization
from app.models.public_kb import DocumentPublication, KbPublication
from app.services import public_kb_service

ORG = 1
OTHER_ORG = 2
USER = 1
KB = "kb-main"
OTHER_KB = "kb-other"


def _doc(
    doc_id: str,
    *,
    org_id: int = ORG,
    kb_id: str | None = KB,
    status: DocumentStatus = DocumentStatus.INDEXED,
    enabled: bool = True,
    title: str | None = None,
) -> Document:
    return Document(
        id=doc_id,
        filename=f"{doc_id}.txt",
        file_path=f"{org_id}/{doc_id}.txt",
        file_size=1,
        file_type=DocumentType.TXT,
        status=status,
        organization_id=org_id,
        uploaded_by=USER,
        knowledge_base_id=kb_id,
        title=title,
        is_enabled=enabled,
    )


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'public-kb-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Org", owner_id=USER),
                Organization(id=OTHER_ORG, name="Other", owner_id=USER),
                KnowledgeBase(id=KB, organization_id=ORG, name="Main KB"),
                KnowledgeBase(id=OTHER_KB, organization_id=ORG, name="Other KB"),
                KnowledgeBase(id="kb-org2", organization_id=OTHER_ORG, name="Org2 KB"),
                _doc("doc-a", title="制度 A"),
                _doc("doc-b", title="制度 B"),
                _doc("doc-other-kb", kb_id=OTHER_KB, title="别的库"),
                _doc("doc-org2", org_id=OTHER_ORG, title="跨组织"),
                _doc("doc-parsed", status=DocumentStatus.PARSED, title="未就绪"),
                _doc("doc-disabled", enabled=False, title="已停用"),
            ]
        )
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def feature_enabled(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_ENABLED", True)


@pytest.fixture(autouse=True)
def reset_rate_buckets():
    pb_endpoints._RATE_BUCKETS.clear()
    yield
    pb_endpoints._RATE_BUCKETS.clear()


@pytest.fixture(autouse=True)
def stub_audit(monkeypatch):
    """默认把审计钉成 no-op：API 用例不应连真实 MySQL 写 user_activity_logs。

    审计行为本身由 test_audit_metadata_has_no_token_or_content_and_failure_is_tolerated
    显式接管（该用例会用 capture 覆盖本 stub）。
    """

    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _noop)


class _FakePipeline:
    def __init__(self, hits=None):
        self.hits = hits or []
        self.calls = []

    async def search_knowledge_base(
        self, query, organization_id, top_k=5, document_ids=None, viewer_user_id=None, **kwargs
    ):
        self.calls.append(
            {
                "query": query,
                "organization_id": organization_id,
                "top_k": top_k,
                "document_ids": document_ids,
                "viewer_user_id": viewer_user_id,
            }
        )
        return self.hits, None


@pytest.fixture
def fake_pipeline(monkeypatch):
    pipeline = _FakePipeline(
        hits=[
            {
                "document_id": "doc-a",
                "chunk_id": "chunk-a",
                "snippet": "<script>alert(1)</script><p>制度 <b>A</b> 正文</p>",
                "score": 0.93,
                "file_path": "1/secret.txt",
                "organization_id": ORG,
                "uploaded_by": USER,
            },
            {"document_id": "doc-b", "snippet": "未发布", "score": 0.9},
            {"document_id": "doc-org2", "snippet": "跨组织", "score": 0.8},
        ]
    )
    monkeypatch.setattr(public_kb_service, "get_rag_pipeline", lambda: pipeline)
    return pipeline


class _Ctx:
    def __init__(self, user, factory):
        self.user = user
        self.factory = factory

    async def __aenter__(self):
        from app.main import app

        user = self.user

        async def _user():
            return user

        async def _db():
            async with self.factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://kb")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(org=ORG, *, user_id=USER, superuser=True):
    return SimpleNamespace(
        id=user_id,
        username=f"tester{user_id}",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="user",
    )


async def _create(client, **overrides):
    payload = {
        "knowledge_base_id": KB,
        "title": "公开制度库",
        "description": "白名单检索",
        "expires_in_days": None,
        "document_ids": ["doc-a"],
    }
    payload.update(overrides)
    return await client.post("/api/v1/kb-publications", json=payload)


async def _create_ok(client, **overrides):
    resp = await _create(client, **overrides)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _expire(factory, publication_id: str, *, current: bool = True):
    async with factory() as db:
        await db.execute(
            update(KbPublication)
            .where(KbPublication.id == publication_id)
            .values(
                expires_at=(
                    datetime.now(UTC) - timedelta(minutes=1)
                    if current
                    else datetime.now(UTC) + timedelta(days=1)
                )
            )
        )
        await db.commit()


async def _set_enabled(factory, publication_id: str, enabled: bool):
    async with factory() as db:
        await db.execute(
            update(KbPublication).where(KbPublication.id == publication_id).values(enabled=enabled)
        )
        await db.commit()


async def test_create_status_public_search_revoke_roundtrip(factory, fake_pipeline):
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        assert created["url"] == f"/public/kb/{token}"
        assert created["active"] is True
        assert created["document_count"] == 1
        assert "token_hash" not in created
        assert (
            "token" not in (await ac.get(f"/api/v1/kb-publications/{created['id']}")).json()["data"]
        )

        meta = await ac.get(f"/api/v1/public/kb/{token}")
        assert meta.status_code == 200, meta.text
        assert set(meta.json()["data"]) == {
            "title",
            "description",
            "expires_at",
            "chat_enabled",
        }
        # 批次十七：聊天默认关闭；公开元数据只投影生效能力，不暴露日预算。
        assert meta.json()["data"]["chat_enabled"] is False
        assert "daily_token_budget" not in meta.json()["data"]
        assert meta.headers["cache-control"] == "no-store"
        assert meta.headers["referrer-policy"] == "no-referrer"
        assert meta.headers["x-robots-tag"] == "noindex"
        assert meta.headers["x-content-type-options"] == "nosniff"

        search = await ac.post(
            f"/api/v1/public/kb/{token}/search", json={"query": "制度", "top_k": 5}
        )
        assert search.status_code == 200, search.text
        results = search.json()["data"]["results"]
        # pipeline 返回 3 条，只有白名单 doc-a 可进入公开响应。
        assert len(results) == 1
        assert set(results[0]) == {"rank", "title", "snippet", "score"}
        assert results[0]["rank"] == 1
        assert results[0]["title"] == "制度 A"
        assert "alert(1)" not in results[0]["snippet"]
        assert "<" not in results[0]["snippet"]
        assert fake_pipeline.calls[0]["document_ids"] == ["doc-a"]

        revoked = await ac.post(f"/api/v1/kb-publications/{created['id']}/revoke")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["data"]["active"] is False

        after = await ac.get(f"/api/v1/public/kb/{token}")
        missing = await ac.get("/api/v1/public/kb/never-existed")
        assert after.status_code == missing.status_code == 404
        assert after.json()["message"] == missing.json()["message"]
        assert after.json()["error_code"] == missing.json()["error_code"]


async def test_token_plaintext_never_persisted_and_rotate_invalidates(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        async with factory() as db:
            row = (
                await db.execute(select(KbPublication).where(KbPublication.id == created["id"]))
            ).scalar_one()
            assert row.token_hash == hashlib.sha256(token.encode("utf-8")).hexdigest()
            serialized = " ".join(
                str(getattr(row, column.name)) for column in KbPublication.__table__.columns
            )
            assert token not in serialized

        rotated = await ac.post(f"/api/v1/kb-publications/{created['id']}/rotate", json={})
        assert rotated.status_code == 200, rotated.text
        new_token = rotated.json()["data"]["token"]
        assert new_token != token
        assert (await ac.get(f"/api/v1/public/kb/{token}")).status_code == 404
        assert (await ac.get(f"/api/v1/public/kb/{new_token}")).status_code == 200


async def test_public_response_has_no_internal_ids_or_paths(factory, fake_pipeline):
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        search = await ac.post(f"/api/v1/public/kb/{token}/search", json={"query": "制度"})
        body = search.text
        for forbidden in (
            "doc-a",
            "chunk-a",
            "secret.txt",
            "uploaded_by",
            "organization_id",
            "knowledge_base_id",
            "kb-main",
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        ):
            assert forbidden not in body, f"公开检索响应泄露内部字段: {forbidden}"
        meta_body = (await ac.get(f"/api/v1/public/kb/{token}")).text
        assert "organization_id" not in meta_body
        assert "knowledge_base_id" not in meta_body
        assert "doc-a" not in meta_body


async def test_revoked_expired_disabled_and_switch_off_are_uniform_404(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]

        await _expire(factory, created["id"])
        expired = await ac.get(f"/api/v1/public/kb/{token}")
        assert expired.status_code == 404

        await _expire(factory, created["id"], current=False)
        await _set_enabled(factory, created["id"], False)
        disabled = await ac.get(f"/api/v1/public/kb/{token}")
        assert disabled.status_code == 404

        await _set_enabled(factory, created["id"], True)
        await ac.post(f"/api/v1/kb-publications/{created['id']}/revoke")
        revoked = await ac.post(f"/api/v1/public/kb/{token}/search", json={"query": "制度"})
        missing = await ac.post("/api/v1/public/kb/never-existed/search", json={"query": "制度"})
        assert revoked.status_code == missing.status_code == 404
        assert revoked.json()["message"] == missing.json()["message"]
        assert revoked.json()["error_code"] == missing.json()["error_code"]

        # 开关关闭：活跃 token 的 GET 与 search 都必须 404；创建也 404。
        active = await _create_ok(ac, title="开关活跃链接")
        active_token = active["token"]
        settings.PUBLIC_KB_ENABLED = False
        off_create = await _create(ac, title="关闭后创建")
        assert off_create.status_code == 404
        off_meta = await ac.get(f"/api/v1/public/kb/{active_token}")
        assert off_meta.status_code == 404
        off_search = await ac.post(
            f"/api/v1/public/kb/{active_token}/search", json={"query": "制度"}
        )
        assert off_search.status_code == 404
        assert off_search.json()["message"] == off_meta.json()["message"]

        status = await ac.get(f"/api/v1/kb-publications/{created['id']}")
        assert status.status_code == 200
        rotated = await ac.post(f"/api/v1/kb-publications/{created['id']}/rotate", json={})
        assert rotated.status_code == 200


async def test_admin_gate_and_cross_org_404(factory, fake_pipeline):
    async with _Ctx(_user(superuser=False), factory) as ac:
        denied = await _create(ac)
        assert denied.status_code == 403

    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        cross = await ac.get(
            "/api/v1/kb-publications/candidates", params={"knowledge_base_id": "kb-org2"}
        )
        assert cross.status_code == 404

    async with _Ctx(_user(org=OTHER_ORG), factory) as ac:
        status = await ac.get(f"/api/v1/kb-publications/{created['id']}")
        assert status.status_code == 404
        assert (await ac.get(f"/api/v1/public/kb/{token}")).status_code == 200


async def test_document_whitelist_validation_and_modes(factory):
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac, document_ids=[])

        bad = await ac.post(
            f"/api/v1/kb-publications/{created['id']}/documents",
            json={"mode": "replace", "document_ids": ["doc-a", "doc-org2"]},
        )
        assert bad.status_code == 404
        docs = await ac.get(f"/api/v1/kb-publications/{created['id']}/documents")
        assert docs.json()["data"]["document_count"] == 0

        for doc_id in ("doc-other-kb", "doc-parsed", "doc-disabled"):
            resp = await ac.post(
                f"/api/v1/kb-publications/{created['id']}/documents",
                json={"mode": "add", "document_ids": [doc_id]},
            )
            assert resp.status_code == 404, doc_id

        added = await ac.post(
            f"/api/v1/kb-publications/{created['id']}/documents",
            json={"mode": "add", "document_ids": ["doc-a", "doc-b"]},
        )
        assert added.status_code == 200, added.text
        assert added.json()["data"]["document_count"] == 2

        removed = await ac.post(
            f"/api/v1/kb-publications/{created['id']}/documents",
            json={"mode": "remove", "document_ids": ["doc-a"]},
        )
        assert removed.json()["data"]["document_count"] == 1
        assert [row["id"] for row in removed.json()["data"]["documents"]] == ["doc-b"]

        replaced = await ac.post(
            f"/api/v1/kb-publications/{created['id']}/documents",
            json={"mode": "replace", "document_ids": ["doc-a"]},
        )
        assert replaced.json()["data"]["document_count"] == 1


async def test_candidates_and_list_publications_filter(factory):
    async with _Ctx(_user(), factory) as ac:
        candidates = await ac.get(
            "/api/v1/kb-publications/candidates", params={"knowledge_base_id": KB}
        )
        assert candidates.status_code == 200, candidates.text
        ids = {row["id"] for row in candidates.json()["data"]["documents"]}
        assert ids == {"doc-a", "doc-b"}
        for row in candidates.json()["data"]["documents"]:
            assert set(row) == {"id", "title"}

        missing = await ac.get(
            "/api/v1/kb-publications/candidates", params={"knowledge_base_id": "kb-missing"}
        )
        assert missing.status_code == 404

        first = await _create_ok(ac)
        second = await _create_ok(ac, knowledge_base_id=OTHER_KB, document_ids=[])
        listing = await ac.get("/api/v1/kb-publications", params={"knowledge_base_id": OTHER_KB})
        assert listing.status_code == 200, listing.text
        ids = [row["id"] for row in listing.json()["data"]]
        assert ids == [second["id"]]
        all_rows = (await ac.get("/api/v1/kb-publications")).json()["data"]
        assert {row["id"] for row in all_rows} == {first["id"], second["id"]}
        assert all("token" not in row and "token_hash" not in row for row in all_rows)


async def test_query_top_k_bounds_and_snippet_bound(factory, monkeypatch):
    long_snippet = "x" * 500
    pipeline = _FakePipeline(hits=[{"document_id": "doc-a", "snippet": long_snippet, "score": 0.5}])
    monkeypatch.setattr(public_kb_service, "get_rag_pipeline", lambda: pipeline)

    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        for query, top_k in (("", 5), ("   ", 5), ("x" * 201, 5), ("制度", 0), ("制度", 11)):
            resp = await ac.post(
                f"/api/v1/public/kb/{token}/search",
                json={"query": query, "top_k": top_k},
            )
            assert resp.status_code == 400, (query, top_k, resp.text)
        ok = await ac.post(f"/api/v1/public/kb/{token}/search", json={"query": "制度", "top_k": 10})
        assert ok.status_code == 200
        assert len(ok.json()["data"]["results"][0]["snippet"]) == 300


def test_public_kb_switch_defaults_false():
    """开关默认必须是 false（变异：默认改 True → 本用例红）。"""
    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["PUBLIC_KB_ENABLED"].default is False


async def test_public_search_rate_limit_and_safety_headers(factory, fake_pipeline, monkeypatch):
    """search 端点也受限流与公开安全头保护。"""
    monkeypatch.setattr(settings, "PUBLIC_KB_RATE_LIMIT_PER_MINUTE", 1)
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        first = await ac.post(f"/api/v1/public/kb/{token}/search", json={"query": "制度"})
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        assert first.headers["referrer-policy"] == "no-referrer"
        assert first.headers["x-robots-tag"] == "noindex"
        assert first.headers["x-content-type-options"] == "nosniff"
        second = await ac.post(f"/api/v1/public/kb/{token}/search", json={"query": "制度"})
        assert second.status_code == 429, second.text


async def test_invalid_token_scan_is_rate_limited(monkeypatch, factory):
    """无效 token 扫描按 IP 限流；删掉 invalid 桶调用 → 本用例红。"""
    from app.api.v1.endpoints import public_kb as pb_endpoints

    monkeypatch.setattr(pb_endpoints, "INVALID_BUCKET_MIN", 1)
    monkeypatch.setattr(settings, "PUBLIC_KB_RATE_LIMIT_PER_MINUTE", 1)
    async with _Ctx(_user(), factory) as ac:
        # max(1, 1*5)=5：前 5 次 404，第 6 次 429。
        for _ in range(5):
            assert (await ac.get("/api/v1/public/kb/not-real")).status_code == 404
        limited = await ac.get("/api/v1/public/kb/not-real")
        assert limited.status_code == 429, limited.text


def test_client_ip_ignores_x_forwarded_for():
    """`_client_ip` 只信 socket peer；信任 XFF 的变异必须红。"""
    from starlette.requests import Request

    from app.api.v1.endpoints import public_kb as pb_endpoints

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"1.2.3.4")],
        "client": ("10.0.0.1", 12345),
    }
    assert pb_endpoints._client_ip(Request(scope)) == "10.0.0.1"


async def test_rate_limit_429_per_token(factory, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_RATE_LIMIT_PER_MINUTE", 1)
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        assert (await ac.get(f"/api/v1/public/kb/{token}")).status_code == 200
        limited = await ac.get(f"/api/v1/public/kb/{token}")
        assert limited.status_code == 429
        # 另一条链接/无效 token 不占用该链接桶（无效按 IP 高阈值桶）。
        assert (await ac.get("/api/v1/public/kb/never-existed")).status_code == 404


async def test_audit_metadata_has_no_token_or_content_and_failure_is_tolerated(
    factory, monkeypatch
):
    calls: list[dict] = []

    async def _capture(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _capture)

    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        await ac.post(
            f"/api/v1/kb-publications/{created['id']}/rotate", json={"expires_in_days": 7}
        )
        await ac.post(
            f"/api/v1/kb-publications/{created['id']}/documents",
            json={"mode": "replace", "document_ids": ["doc-a", "doc-b"]},
        )
        await ac.post(f"/api/v1/kb-publications/{created['id']}/revoke")

    actions = [call["action"] for call in calls]
    assert actions == [
        "create_kb_publication",
        "rotate_kb_publication",
        "set_kb_publication_documents",
        "revoke_kb_publication",
    ]
    for call in calls:
        detail = call["detail"]
        assert token not in detail
        assert "doc-a" not in detail and "doc-b" not in detail
        assert "制度" not in detail  # 文档标题/正文不得进入审计
        assert call["target_type"] == "kb_publication"
        json.loads(detail)  # 合法 JSON 元数据

    async def _boom(**kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _boom)
    async with _Ctx(_user(), factory) as ac:
        ok = await _create(ac, title="审计故障仍应成功")
        assert ok.status_code == 200, ok.text


async def test_deleted_document_drops_out_of_public_search(factory, monkeypatch):
    pipeline = _FakePipeline(hits=[{"document_id": "doc-a", "snippet": "正文", "score": 1.0}])
    monkeypatch.setattr(public_kb_service, "get_rag_pipeline", lambda: pipeline)

    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        async with factory() as db:
            # 模拟文档删除链路（_DOCUMENT_CHILD_MODELS 会清白名单行；SQLite
            # 默认不强制外键，这里同时删白名单行以对齐真实行为）。
            await db.execute(
                DocumentPublication.__table__.delete().where(
                    DocumentPublication.document_id == "doc-a"
                )
            )
            await db.execute(Document.__table__.delete().where(Document.id == "doc-a"))
            await db.commit()
        resp = await ac.post(f"/api/v1/public/kb/{token}/search", json={"query": "制度"})
        assert resp.status_code == 200
        assert resp.json()["data"]["results"] == []
        assert pipeline.calls == [], "空白名单绝不能回退到 KB 全量检索"
