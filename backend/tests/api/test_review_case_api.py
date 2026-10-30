"""核验案件 API（真实 SQLite + ASGI）：建案/列表/详情/证据选择。"""

import uuid
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.review import ReviewDocumentMeta
from app.models.user_audit import UserActivityLog

ORG = 1
OTHER_ORG = 2
POLICY = "doc-api-policy"
BASE = "doc-api-base"
REVISED = "doc-api-revised"
LEGACY = "doc-api-legacy"
NOMETA = "doc-api-nometa"
FOREIGN = "doc-api-foreign"


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-case-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=0))
        db.add(Organization(id=OTHER_ORG, name="Other", owner_id=0))
        docs = []
        for doc_id, org_id in (
            (POLICY, ORG),
            (BASE, ORG),
            (REVISED, ORG),
            (LEGACY, ORG),
            (NOMETA, ORG),
            (FOREIGN, OTHER_ORG),
        ):
            docs.append(
                Document(
                    id=doc_id,
                    filename=f"{doc_id}.pdf",
                    file_path=f"{org_id}/{doc_id}.pdf",
                    file_size=100,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=org_id,
                    uploaded_by=1,
                )
            )
        db.add_all(docs)
        db.add_all(
            [
                ReviewDocumentMeta(
                    document_id=POLICY,
                    organization_id=ORG,
                    doc_role="policy",
                    content_sha256="p" * 64,
                    registered_by=1,
                ),
                ReviewDocumentMeta(
                    document_id=BASE,
                    organization_id=ORG,
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="V1",
                    version_seq=1,
                    content_sha256="b" * 64,
                    registered_by=1,
                ),
                ReviewDocumentMeta(
                    document_id=REVISED,
                    organization_id=ORG,
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="V2",
                    version_seq=2,
                    content_sha256="r" * 64,
                    registered_by=1,
                ),
                ReviewDocumentMeta(
                    document_id=LEGACY,
                    organization_id=ORG,
                    doc_role="contract",
                    family_key="supplier-x",
                    version_label="V3",
                    version_seq=3,
                    content_sha256="l" * 64,
                    registered_by=1,
                ),
            ]
        )

        def chunk(doc_id, idx, original, *, page=1, meta=None):
            return DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                chunk_index=idx,
                chunk_text=f"【文档背景：摘要】{original}",
                original_text=original,
                chunk_length=len(original),
                page_number=page,
                meta_data=meta or {"text_provenance": "parser_original"},
            )

        db.add_all(
            [
                chunk(POLICY, 0, "第3条 采购付款应遵循本制度，另有约定的从其约定。"),
                chunk(BASE, 0, "第一条 付款期限为30天，自收到发票之日起算。"),
                chunk(BASE, 1, "第二条 验收合格后支付价款。"),
                chunk(REVISED, 0, "第一条 付款期限为60天，自收到发票之日起算。"),
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=LEGACY,
                    chunk_index=0,
                    chunk_text="【文档背景：摘要】旧版付款条款",
                    original_text=None,
                    chunk_length=8,
                    page_number=1,
                    meta_data={"text_provenance": "legacy_unverified"},
                ),
            ]
        )
        await db.commit()
    yield maker
    await engine.dispose()


class _Ctx:
    def __init__(self, user, factory):
        self.user = user
        self.factory = factory

    async def __aenter__(self):
        from app.main import app

        async def _user():
            return self.user

        async def _db():
            async with self.factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://review")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(org=ORG, *, superuser=False):
    return SimpleNamespace(
        id=1,
        username="tester",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="admin" if superuser else "user",
    )


def _case_payload(*, revised=REVISED, title="采购合同 V1→V2 核验"):
    return {
        "title": title,
        "policy_document_id": POLICY,
        "base_document_id": BASE,
        "revised_document_id": revised,
    }


async def _create_case(client):
    r = await client.post("/api/v1/reviews", json=_case_payload())
    assert r.status_code == 200, r.text
    return r.json()["data"]


@pytest.mark.asyncio
async def test_create_list_detail_and_select_evidence_flow(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        assert case["status"] == "ready", case
        assert case["live_source_stale"] is False
        assert [i["item_key"] for i in case["items"]] == [
            "payment_term",
            "start_condition",
            "acceptance_invoice",
            "exception",
        ]
        assert case["documents"]["base"]["version_label"] == "V1"
        assert case["documents"]["revised"]["version_label"] == "V2"
        assert case["documents"]["base"]["readiness"]["status"] == "reviewable"

        listed = await ac.get("/api/v1/reviews")
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"]["total"] == 1

        detail = await ac.get(f"/api/v1/reviews/{case['id']}")
        assert detail.status_code == 200, detail.text
        detail_data = detail.json()["data"]
        assert detail_data["id"] == case["id"]

        payment = next(i for i in detail_data["items"] if i["item_key"] == "payment_term")
        base_evidence = payment["evidence"]["base"]
        assert base_evidence, "建案时应在 base 侧抓到候选证据"
        candidate = base_evidence[0]
        assert candidate["text_provenance"] == "parser_original"
        assert "【文档背景" not in candidate["quote"]

        selected = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={
                "side": "base",
                "chunk_id": candidate["chunk_id_at_capture"],
                "role": "main",
            },
        )
        assert selected.status_code == 200, selected.text
        row = selected.json()["data"]
        assert row["selected"] is True
        assert row["role"] == "main"
        assert row["quote"] == candidate["quote"]
        assert row["quote_sha256"] == candidate["quote_sha256"]


@pytest.mark.asyncio
async def test_legacy_revised_blocks_case_and_evidence_is_409(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        r = await ac.post("/api/v1/reviews", json=_case_payload(revised=LEGACY))
        assert r.status_code == 200, r.text
        case = r.json()["data"]
        assert case["status"] == "blocked"
        assert "revised:no_original_text" in case["blocking_reasons"]

        payment = case["items"][0]
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={"side": "base", "chunk_id": str(uuid.uuid4())},
        )
        assert put.status_code == 409, put.text
        assert put.json()["error_code"] == "REVIEW_CASE_NOT_READY"


@pytest.mark.asyncio
async def test_legacy_chunk_selection_is_409_source_unavailable(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        payment = next(i for i in case["items"] if i["item_key"] == "payment_term")

        async with factory() as db:
            other_doc_chunk_id = (
                await db.execute(
                    select(DocumentChunk.id).where(DocumentChunk.document_id == LEGACY)
                )
            ).scalar_one()
        # 分块不属于该侧文档：404，而不是把别的文档正文当作证据。
        cross = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={"side": "revised", "chunk_id": other_doc_chunk_id},
        )
        assert cross.status_code == 404, cross.text

        # 建案后文档被写入一个 legacy 分块（缺 parser 标记）：选择时必须 409，
        # 不得回退 chunk_text 快照成「原文证据」。
        async with factory() as db:
            legacy_chunk = DocumentChunk(
                id=str(uuid.uuid4()),
                document_id=BASE,
                chunk_index=9,
                chunk_text="旧文本 付款期限30天",
                original_text=None,
                chunk_length=10,
                page_number=1,
                meta_data={"text_provenance": "legacy_unverified"},
            )
            db.add(legacy_chunk)
            await db.commit()
            legacy_chunk_id = legacy_chunk.id
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{payment['id']}/evidence",
            json={"side": "base", "chunk_id": legacy_chunk_id},
        )
        assert put.status_code == 409, put.text
        assert put.json()["error_code"] == "SOURCE_ORIGINAL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_cross_org_access_is_forbidden(factory, monkeypatch):
    async def _grant_all(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value, PermissionType.DOCUMENT_UPDATE.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant_all)

    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)

    async with _Ctx(_user(org=2), factory) as ac:
        detail = await ac.get(f"/api/v1/reviews/{case['id']}")
        assert detail.status_code == 403, detail.text

        post = await ac.post("/api/v1/reviews", json=_case_payload())
        assert post.status_code == 403, post.text

        item = case["items"][0]
        chunk_id = item["evidence"]["base"][0]["chunk_id_at_capture"]
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{item['id']}/evidence",
            json={"side": "base", "chunk_id": chunk_id},
        )
        assert put.status_code == 403, put.text

        # 列表按调用者组织过滤：别的组织看不到本案件，返回空而不是泄露。
        listed = await ac.get("/api/v1/reviews")
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_review_endpoints_require_document_permissions(factory):
    async with _Ctx(_user(org=ORG), factory) as ac:
        listed = await ac.get("/api/v1/reviews")
        post = await ac.post("/api/v1/reviews", json=_case_payload())

    assert listed.status_code == 403, listed.text
    assert post.status_code == 403, post.text


@pytest.mark.asyncio
async def test_read_only_role_cannot_create_or_select_but_can_list(factory, monkeypatch):
    async def _read_only(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _read_only)

    async with _Ctx(_user(org=ORG), factory) as ac:
        listed = await ac.get("/api/v1/reviews")
        post = await ac.post("/api/v1/reviews", json=_case_payload())

    assert listed.status_code == 200, listed.text
    assert post.status_code == 403, post.text


@pytest.mark.asyncio
async def test_unknown_case_and_item_are_404(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        missing = await ac.get(f"/api/v1/reviews/{uuid.uuid4()}")
        assert missing.status_code == 404, missing.text

        case = await _create_case(ac)
        item = case["items"][0]
        chunk_id = item["evidence"]["base"][0]["chunk_id_at_capture"]
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{uuid.uuid4()}/evidence",
            json={"side": "base", "chunk_id": chunk_id},
        )
        assert put.status_code == 404, put.text


@pytest.mark.asyncio
async def test_analyze_case_endpoint_updates_items_and_is_idempotent(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        r = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        items = {i["item_key"]: i for i in data["items"]}
        payment = items["payment_term"]
        assert payment["change_type"] == "modified"
        assert payment["facts"]["base"]["value"] == 30
        assert payment["facts"]["revised"]["value"] == 60
        assert payment["facts"]["extractor_version"] == 1

        r2 = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r2.status_code == 200, r2.text
        second = {i["item_key"]: (i["change_type"], i["facts"]) for i in r2.json()["data"]["items"]}
        first = {i["item_key"]: (i["change_type"], i["facts"]) for i in data["items"]}
        assert second == first, "同一输入重复分析必须一致"


@pytest.mark.asyncio
async def test_analyze_blocked_case_409_cross_org_403_and_unknown_404(factory, monkeypatch):
    async with _Ctx(_user(superuser=True), factory) as ac:
        blocked = (await ac.post("/api/v1/reviews", json=_case_payload(revised=LEGACY))).json()[
            "data"
        ]
        r = await ac.post(f"/api/v1/reviews/{blocked['id']}/analyze")
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "REVIEW_CASE_NOT_READY"
        case = await _create_case(ac)

    async def _grant_all(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value, PermissionType.DOCUMENT_UPDATE.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant_all)

    async with _Ctx(_user(org=2), factory) as ac:
        cross = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert cross.status_code == 403, cross.text
        missing = await ac.post(f"/api/v1/reviews/{uuid.uuid4()}/analyze")
        assert missing.status_code == 404, missing.text


@pytest.mark.asyncio
async def test_analyze_requires_write_permission(factory, monkeypatch):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)

    async def _read_only(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _read_only)

    async with _Ctx(_user(org=ORG), factory) as ac:
        r = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_analyze_legacy_after_creation_maps_409(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        async with factory() as db:
            await db.execute(
                DocumentChunk.__table__.delete().where(DocumentChunk.document_id == BASE)
            )
            db.add(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=BASE,
                    chunk_index=0,
                    chunk_text="旧文 付款期限30天",
                    original_text=None,
                    chunk_length=10,
                    page_number=1,
                    meta_data={"text_provenance": "legacy_unverified"},
                )
            )
            await db.commit()
        r = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "SOURCE_ORIGINAL_UNAVAILABLE"


@pytest.mark.asyncio
async def test_decision_export_and_download_flow(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        analyzed = await ac.post(f"/api/v1/reviews/{case['id']}/analyze")
        assert analyzed.status_code == 200, analyzed.text
        blocked = await ac.post(f"/api/v1/reviews/{case['id']}/export")
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error_code"] == "REVIEW_EXPORT_BLOCKED"
        assert blocked.json()["detail"]["items"][0]["reasons"] == ["undecided"]

        for item in case["items"]:
            decided = await ac.put(
                f"/api/v1/reviews/{case['id']}/items/{item['id']}/decision",
                json={"status": "verified", "note": "已核对"},
            )
            assert decided.status_code == 200, decided.text

        exported = await ac.post(f"/api/v1/reviews/{case['id']}/export")
        assert exported.status_code == 200, exported.text
        export = exported.json()["data"]
        assert export["item_count"] == 4

        listed = await ac.get(f"/api/v1/reviews/{case['id']}/exports")
        assert listed.status_code == 200
        assert listed.json()["data"]["items"][0]["id"] == export["id"]

        download = await ac.get(
            f"/api/v1/reviews/{case['id']}/exports/{export['id']}/download",
            params={"format": "json"},
        )
        assert download.status_code == 200, download.text
        assert download.headers["content-type"].startswith("application/json")
        assert download.headers["content-disposition"].startswith("attachment; filename=")
        body = download.json()
        assert body["schema_version"] == 1
        assert body["documents"]["base"]["version_label"] == "V1"
        assert "chunk_text" not in download.text

        markdown = await ac.get(
            f"/api/v1/reviews/{case['id']}/exports/{export['id']}/download",
            params={"format": "markdown"},
        )
        assert markdown.status_code == 200
        assert "text/markdown" in markdown.headers["content-type"]
        assert "# 核验单" in markdown.text
        assert "本核验单仅覆盖本次选定的条款与文档版本" in markdown.text

        detail = await ac.get(f"/api/v1/reviews/{case['id']}")
        assert detail.json()["data"]["status"] == "completed"

        # completed 后再改决策：状态回 in_review，旧导出保留
        reopened = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{case['items'][0]['id']}/decision",
            json={"status": "needs_human", "note": "复核后调整"},
        )
        assert reopened.status_code == 200, reopened.text
        detail2 = await ac.get(f"/api/v1/reviews/{case['id']}")
        assert detail2.json()["data"]["status"] == "in_review"

    async with factory() as db:
        logs = (
            (
                await db.execute(
                    select(UserActivityLog).where(UserActivityLog.target_id == case["id"])
                )
            )
            .scalars()
            .all()
        )
    actions = {log.action for log in logs}
    assert "review_export" in actions
    assert "review_export_download" in actions


@pytest.mark.asyncio
async def test_decision_export_permission_and_scope(factory, monkeypatch):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        item = case["items"][0]

    async def _grant_all(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value, PermissionType.DOCUMENT_UPDATE.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _grant_all)
    async with _Ctx(_user(org=2), factory) as ac:
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{item['id']}/decision",
            json={"status": "verified"},
        )
        post = await ac.post(f"/api/v1/reviews/{case['id']}/export")
        download = await ac.get(
            f"/api/v1/reviews/{case['id']}/exports/{uuid.uuid4()}/download",
            params={"format": "json"},
        )
    assert put.status_code == 403, put.text
    assert post.status_code == 403, post.text
    assert download.status_code == 403, download.text

    async def _read_only(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _read_only)
    async with _Ctx(_user(org=ORG), factory) as ac:
        ro_put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{item['id']}/decision",
            json={"status": "verified"},
        )
        ro_post = await ac.post(f"/api/v1/reviews/{case['id']}/export")
        ro_list = await ac.get(f"/api/v1/reviews/{case['id']}/exports")
    assert ro_put.status_code == 403, ro_put.text
    assert ro_post.status_code == 403, ro_post.text
    assert ro_list.status_code == 200, ro_list.text


@pytest.mark.asyncio
async def test_decision_on_blocked_case_is_409_and_unknown_export_404(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        blocked = (await ac.post("/api/v1/reviews", json=_case_payload(revised=LEGACY))).json()[
            "data"
        ]
        item = blocked["items"][0]
        r = await ac.put(
            f"/api/v1/reviews/{blocked['id']}/items/{item['id']}/decision",
            json={"status": "verified"},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "REVIEW_CASE_NOT_READY"
        blocked_export = await ac.post(f"/api/v1/reviews/{blocked['id']}/export")
        assert blocked_export.status_code == 409, blocked_export.text
        assert blocked_export.json()["error_code"] == "REVIEW_CASE_NOT_READY"

        case = await _create_case(ac)
        assert (await ac.post(f"/api/v1/reviews/{case['id']}/analyze")).status_code == 200
        for it in case["items"]:
            await ac.put(
                f"/api/v1/reviews/{case['id']}/items/{it['id']}/decision",
                json={"status": "not_applicable"},
            )
        export = (await ac.post(f"/api/v1/reviews/{case['id']}/export")).json()["data"]
        missing = await ac.get(
            f"/api/v1/reviews/{case['id']}/exports/{uuid.uuid4()}/download",
            params={"format": "json"},
        )
        assert missing.status_code == 404, missing.text
        good = await ac.get(
            f"/api/v1/reviews/{case['id']}/exports/{export['id']}/download",
            params={"format": "json"},
        )
        assert good.status_code == 200


@pytest.mark.asyncio
async def test_read_only_can_download_exports_but_not_decide(factory, monkeypatch):
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        assert (await ac.post(f"/api/v1/reviews/{case['id']}/analyze")).status_code == 200
        for it in case["items"]:
            decided = await ac.put(
                f"/api/v1/reviews/{case['id']}/items/{it['id']}/decision",
                json={"status": "verified"},
            )
            assert decided.status_code == 200, decided.text
        export = (await ac.post(f"/api/v1/reviews/{case['id']}/export")).json()["data"]

    async def _read_only(_db, _user_obj, organization_id=None):
        return {PermissionType.DOCUMENT_READ.value}

    monkeypatch.setattr("app.core.security.permission_service.get_user_permissions", _read_only)
    async with _Ctx(_user(org=ORG), factory) as ac:
        listed = await ac.get(f"/api/v1/reviews/{case['id']}/exports")
        assert listed.status_code == 200, listed.text
        download = await ac.get(
            f"/api/v1/reviews/{case['id']}/exports/{export['id']}/download",
            params={"format": "json"},
        )
        assert download.status_code == 200, download.text
        put = await ac.put(
            f"/api/v1/reviews/{case['id']}/items/{case['items'][0]['id']}/decision",
            json={"status": "verified"},
        )
        post = await ac.post(f"/api/v1/reviews/{case['id']}/export")
    assert put.status_code == 403, put.text
    assert post.status_code == 403, post.text


# ── 建案候选文档（GET /reviews/candidates） ──────────────────────────────────


@pytest.mark.asyncio
async def test_candidates_lists_org_documents_with_optional_identity(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        r = await ac.get("/api/v1/reviews/candidates")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        ids = [item["document_id"] for item in data["items"]]
        assert set(ids) == {POLICY, BASE, REVISED, LEGACY, NOMETA}
        assert FOREIGN not in ids, "候选文档必须限定在本组织"
        by_id = {item["document_id"]: item for item in data["items"]}
        assert by_id[POLICY]["meta"]["doc_role"] == "policy"
        assert by_id[BASE]["meta"]["family_key"] == "supplier-x"
        assert by_id[BASE]["meta"]["version_label"] == "V1"
        assert by_id[NOMETA]["meta"] is None
        assert by_id[NOMETA]["filename"] == f"{NOMETA}.pdf"
        assert ids[-1] == NOMETA, "已登记身份的文档应排在未登记的前面"


@pytest.mark.asyncio
async def test_candidates_keyword_filter_and_route_not_shadowed_by_case_id(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        r = await ac.get("/api/v1/reviews/candidates", params={"q": "nometa"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert [item["document_id"] for item in data["items"]] == [NOMETA]
        assert data["total"] == 1
        # 枚举小写 value 与分页回显口径
        assert data["items"][0]["status"] == "indexed"
        assert data["items"][0]["file_type"] == "pdf"
        assert (data["page"], data["page_size"]) == (1, 50)


@pytest.mark.asyncio
async def test_candidates_q_matches_title_and_escapes_like_wildcards(factory):
    async with _Ctx(_user(superuser=True), factory) as ac:
        # title 命中（filename 不含该关键字）
        async with factory() as db:
            doc = await db.get(Document, BASE)
            doc.title = "年度采购框架协议"
            await db.commit()
        by_title = await ac.get("/api/v1/reviews/candidates", params={"q": "框架协议"})
        assert by_title.status_code == 200, by_title.text
        assert [i["document_id"] for i in by_title.json()["data"]["items"]] == [BASE]

        # LIKE 通配符必须按字面处理：q="100%" 不得变成「匹配一切」
        async with factory() as db:
            doc = await db.get(Document, REVISED)
            doc.filename = "100%付款条款.pdf"
            await db.commit()
        wildcard = await ac.get("/api/v1/reviews/candidates", params={"q": "100%"})
        assert wildcard.status_code == 200, wildcard.text
        assert [i["document_id"] for i in wildcard.json()["data"]["items"]] == [REVISED]
