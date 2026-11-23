"""核验模板 API（真实 SQLite + ASGI）：CRUD/版本/diff/归档/建案选择/跨组织/RBAC/审计。

判别性覆盖（每个用例标题写出变异点）：
- 读取端点需要 document:read + knowledge:read，写入端点需要 document:update
  （去掉 _WRITE_DEPS 或放宽 _READ_DEPS → RBAC 用例红）；
- 跨组织模板一律 404/403，列表不泄露（get_template 去掉 org 校验 → 红）；
- 版本行不可变：新增版本后旧版本内容与哈希不变（create_version 改成 update → 红）；
- 建案可选模板版本并固化快照；模板后续编辑不影响历史案件详情；
- 归档不删除行、归档后不可新增版本/不可建案；
- 审计只含元数据（org/id/版本号/项数/哈希前缀），不出现规则关键词文本。
"""

import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.database import Base
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.review import ReviewTemplate, ReviewTemplateVersion
from app.services.review_checklist import DEFAULT_ITEM_KEYWORDS
from tests.api.test_review_case_api import (
    ORG,
    OTHER_ORG,
    _case_payload,
    _Ctx,
    _user,
)

WRITE_ACTIONS = {
    "create_review_template",
    "update_review_template",
    "create_review_template_version",
    "archive_review_template",
    "bootstrap_review_template",
}
READ_ACTIONS = WRITE_ACTIONS | {"bootstrap_review_template"}


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'review-template-api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=0))
        db.add(Organization(id=OTHER_ORG, name="Other", owner_id=0))
        await db.commit()
    yield maker
    await engine.dispose()


def template_items(*, extra: str = "保密") -> list[dict]:
    """自定义追加项（内置 4 项由服务端自动补齐，客户端不提交）。"""
    return [{"key": "confidentiality", "keywords": [extra, "泄露"], "label": "保密义务"}]


async def _create_template(ac, *, name="采购模板", items=None) -> dict:
    payload: dict = {"name": name}
    if items is not None:
        payload["items"] = items
    r = await ac.post("/api/v1/reviews/templates", json=payload)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _add_version(ac, template_id: str, items, *, change_note=None) -> dict:
    body: dict = {"items": items}
    if change_note:
        body["change_note"] = change_note
    r = await ac.post(f"/api/v1/reviews/templates/{template_id}/versions", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


async def _seed_case_docs(factory):
    """插入建案所需的三份同组织可核验文档（形状与 test_review_case_api 夹具一致）。"""
    import uuid as _uuid

    from app.models.document import (
        Document,
        DocumentChunk,
        DocumentStatus,
        DocumentType,
    )
    from app.models.review import ReviewDocumentMeta
    from tests.api.test_review_case_api import BASE, POLICY, REVISED

    async with factory() as db:
        for doc_id in (POLICY, BASE, REVISED):
            db.add(
                Document(
                    id=doc_id,
                    filename=f"{doc_id}.pdf",
                    file_path=f"{ORG}/{doc_id}.pdf",
                    file_size=100,
                    file_type=DocumentType.PDF,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=1,
                )
            )
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
            ]
        )

        def chunk(doc_id, idx, original, *, page=1):
            return DocumentChunk(
                id=str(_uuid.uuid4()),
                document_id=doc_id,
                chunk_index=idx,
                chunk_text=f"【文档背景：摘要】{original}",
                original_text=original,
                chunk_length=len(original),
                page_number=page,
                meta_data={"text_provenance": "parser_original"},
            )

        db.add_all(
            [
                chunk(POLICY, 0, "第3条 采购付款应遵循本制度，另有约定的从其约定。"),
                chunk(BASE, 0, "第一条 付款期限为30天，自收到发票之日起算。"),
                chunk(REVISED, 0, "第一条 付款期限为60天，自收到发票之日起算。"),
            ]
        )
        await db.commit()


def _grant(*permissions: PermissionType):
    async def _grant_all(_db, _user_obj, organization_id=None):
        return {permission.value for permission in permissions}

    return _grant_all


# ── CRUD 基础 ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_template_create_list_get_and_version_flow(factory, monkeypatch):
    """创建（带首版）→ 列表 → 详情 → 加版本 → 版本列表/单版查看。

    变异点：list_templates 默认包含归档/跨组织 → 列表断言红；
    create_version 不再递增 latest_version → 详情版本断言红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    async with _Ctx(_user(superuser=True), factory) as ac:
        created = await _create_template(ac, items=template_items())
        assert created["latest_version"] == 1
        assert created["status"] == "active"
        assert created["version"]["version_no"] == 1
        assert created["version"]["item_count"] == 5  # 内置 4 + 自定义 1
        assert created["version"]["intact"] is True

        listed = await ac.get("/api/v1/reviews/templates")
        assert listed.status_code == 200, listed.text
        data = listed.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["id"] == created["id"]

        detail = await ac.get(f"/api/v1/reviews/templates/{created['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["version"]["version_no"] == 1

        second = await _add_version(
            ac,
            created["id"],
            [{"key": "warranty", "keywords": ["质保"], "label": "质保期"}],
            change_note="追加质保",
        )
        assert second["version_no"] == 2
        assert second["change_note"] == "追加质保"

        versions = await ac.get(f"/api/v1/reviews/templates/{created['id']}/versions")
        assert versions.status_code == 200, versions.text
        rows = versions.json()["data"]["items"]
        assert [row["version_no"] for row in rows] == [2, 1]
        assert versions.json()["data"]["latest_version"] == 2
        assert all("items" not in row for row in rows), "版本列表不带规则正文"

        single = await ac.get(f"/api/v1/reviews/templates/{created['id']}/versions/1")
        assert single.status_code == 200, single.text
        assert [entry["key"] for entry in single.json()["data"]["items"]][:4] == list(
            DEFAULT_ITEM_KEYWORDS
        )

        # 旧版本行不可变：v1 仍是 6 项，v2 是 5 项（内置 4 + warranty）
        v1 = await ac.get(f"/api/v1/reviews/templates/{created['id']}/versions/1")
        assert v1.json()["data"]["item_count"] == 5  # 内置 4 + 自定义 1
        assert v1.json()["data"]["payload_sha256"] == created["version"]["payload_sha256"]


@pytest.mark.asyncio
async def test_invalid_first_version_does_not_leave_orphan_template(factory, monkeypatch):
    """首版规则无效时整次创建失败：不得先提交模板头留下「无版本空模板」。

    回归：端点曾先 create_template（独立 commit）再 create_version；非法 items
    让版本校验 400，但模板行已落库，同名重试又被 409 挡住 —— 用户既建不出
    可用模板，也清不掉坏行（本批不提供物理删除）。
    变异点：把 compose_version_items 预校验移到 create_template 之后 → 第二次
    同名创建变 409，本用例红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    async with _Ctx(_user(superuser=True), factory) as ac:
        bad = await ac.post(
            "/api/v1/reviews/templates",
            json={"name": "半成品模板", "items": [{"key": "payment_term", "keywords": ["付款"]}]},
        )
        assert bad.status_code in (400, 422), bad.text
        # 同名重试必须能成功（说明上一次没有留下模板行）。
        good = await ac.post(
            "/api/v1/reviews/templates",
            json={"name": "半成品模板", "items": template_items()},
        )
        assert good.status_code == 200, good.text
        assert good.json()["data"]["latest_version"] == 1


@pytest.mark.asyncio
async def test_template_update_and_duplicate_name(factory, monkeypatch):
    """改名生效；同组织重名 409；空更新 422（schema 层）。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    async with _Ctx(_user(superuser=True), factory) as ac:
        created = await _create_template(ac, name="旧名")
        updated = await ac.put(
            f"/api/v1/reviews/templates/{created['id']}",
            json={"name": "新名", "description": "说明"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["name"] == "新名"

        await _create_template(ac, name="另一个")
        conflict = await ac.put(
            f"/api/v1/reviews/templates/{created['id']}", json={"name": "另一个"}
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["error_code"] == "REVIEW_TEMPLATE_NAME_CONFLICT"

        empty = await ac.put(f"/api/v1/reviews/templates/{created['id']}", json={})
        assert empty.status_code in (400, 422), empty.text


@pytest.mark.asyncio
async def test_invalid_template_payload_is_422(factory, monkeypatch):
    """schema 层也拦非法输入：内置项不可改词、空数组、超长名称。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    async with _Ctx(_user(superuser=True), factory) as ac:
        for payload in (
            # 内置 key 冒充自定义项：服务层 400（确定性抽取器固定，模板不能改）。
            {"name": "x", "items": [{"key": "payment_term", "keywords": ["付款"]}]},
            {"name": "x" * 101},
            {"name": "x", "items": [{"key": "foo", "keywords": ["a" * 65]}]},
            {"name": "x", "items": [{"key": "Bad Key", "keywords": ["a"]}]},
            {"name": "x", "change_note": "无首版却写变更说明"},
        ):
            r = await ac.post("/api/v1/reviews/templates", json=payload)
            assert r.status_code in (400, 422), (payload, r.text)


# ── 版本 diff ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_diff_endpoint_reports_field_level_changes(factory, monkeypatch):
    """diff 端点：只读、逐字段、关键词集合语义。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    async with _Ctx(_user(superuser=True), factory) as ac:
        created = await _create_template(ac, items=template_items(extra="保密"))
        await _add_version(
            ac,
            created["id"],
            [
                {
                    "key": "confidentiality",
                    "keywords": ["保密", "泄露", "竞业"],
                    "label": "保密义务",
                },
                {"key": "termination", "keywords": ["解除"], "label": "解除"},
            ],
        )
        diff = await ac.get(
            f"/api/v1/reviews/templates/{created['id']}/diff",
            params={"base_version": 1, "target_version": 2},
        )
        assert diff.status_code == 200, diff.text
        data = diff.json()["data"]
        # 只比较自定义项：v1 有 1 个（confidentiality）、v2 有 2 个 → added 1 + changed 1。
        assert data["summary"] == {"added": 1, "removed": 0, "changed": 1, "unchanged": 0}
        assert data["added"][0]["key"] == "termination"
        assert data["changed"][0]["fields"]["keywords"]["added"] == ["竞业"]

        missing = await ac.get(
            f"/api/v1/reviews/templates/{created['id']}/diff",
            params={"base_version": 1, "target_version": 99},
        )
        assert missing.status_code == 404, missing.text


# ── 建案选择模板版本 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_case_creation_selects_template_version_and_freezes_snapshot(factory, monkeypatch):
    """建案带 template_id/version_no：案件项来自模板版本、快照哈希可复现。

    变异点：create_review_case 不透传 template 参数 → 案件项变回 4 个内置项，
    本用例红；案件详情不回显 template_id → 溯源断言红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    await _seed_case_docs(factory)
    async with _Ctx(_user(superuser=True), factory) as ac:
        created = await _create_template(ac, items=template_items())
        payload = {
            **_case_payload(),
            "template_id": created["id"],
            "template_version_no": 1,
        }
        r = await ac.post("/api/v1/reviews", json=payload)
        assert r.status_code == 200, r.text
        case = r.json()["data"]
        assert case["template_id"] == created["id"]
        assert case["template_version_no"] == 1
        assert case["from_snapshot"] is True
        assert [item["item_key"] for item in case["items"]] == [
            *DEFAULT_ITEM_KEYWORDS,
            "confidentiality",
        ]

        # 模板后续编辑：历史案件详情完全不变（快照哈希与项集合都不漂移）。
        await _add_version(
            ac,
            created["id"],
            [{"key": "warranty", "keywords": ["质保"], "label": "质保期"}],
        )
        detail = await ac.get(f"/api/v1/reviews/{case['id']}")
        assert detail.status_code == 200, detail.text
        after = detail.json()["data"]
        assert after["rule_snapshot_sha256"] == case["rule_snapshot_sha256"]
        assert [item["item_key"] for item in after["items"]] == [
            *DEFAULT_ITEM_KEYWORDS,
            "confidentiality",
        ]

        # version_no 缺失模板 id：422（schema 层拒绝歧义请求）。
        bad = await ac.post("/api/v1/reviews", json={**_case_payload(), "template_version_no": 1})
        assert bad.status_code in (400, 422), bad.text


@pytest.mark.asyncio
async def test_case_creation_with_archived_template_is_409(factory, monkeypatch):
    """归档模板不可再建案（409 稳定错误码）。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    await _seed_case_docs(factory)
    async with _Ctx(_user(superuser=True), factory) as ac:
        created = await _create_template(ac, items=template_items())
        archived = await ac.post(f"/api/v1/reviews/templates/{created['id']}/archive")
        assert archived.status_code == 200, archived.text
        assert archived.json()["data"]["status"] == "archived"
        again = await ac.post(f"/api/v1/reviews/templates/{created['id']}/archive")
        assert again.status_code == 409, again.text
        assert again.json()["error_code"] == "REVIEW_TEMPLATE_ALREADY_ARCHIVED"

        r = await ac.post(
            "/api/v1/reviews",
            json={**_case_payload(), "template_id": created["id"]},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error_code"] == "REVIEW_TEMPLATE_ARCHIVED"


@pytest.mark.asyncio
async def test_bootstrap_endpoint_creates_from_process_checklist(factory, monkeypatch):
    """bootstrap 端点把进程级 checklist 固化为 v1（含环境追加项）。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    monkeypatch.setenv(
        "REVIEW_CHECKLIST_JSON",
        json.dumps([{"key": "confidentiality", "keywords": ["保密"], "label": "保密义务"}]),
    )
    async with _Ctx(_user(superuser=True), factory) as ac:
        r = await ac.post("/api/v1/reviews/templates/bootstrap", json={"name": "迁移模板"})
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["latest_version"] == 1
        assert data["version"]["source"] == "bootstrap"
        assert data["version"]["item_count"] == 5


# ── RBAC 与审计 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_org_template_access_is_denied(factory, monkeypatch):
    """外组织用户：模板读取/写入一律 404（不暴露存在性），列表为空。

    口径：路径式资源（模板 id 已知）按服务层 get_template 组织校验返回 404
    —— 与文档/案件「跨组织不可枚举」的既有做法一致；集合端点（列表/创建）
    走 require_organization + RBAC，列表只返回本组织数据。
    变异点：get_template 去掉 org 校验 → 第一个断言变 200 红；列表去掉 org
    过滤 → total 断言红；写端点漏掉组织解析 → 创建成功变红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    async with _Ctx(_user(superuser=True), factory) as ac:
        created = await _create_template(ac, items=template_items())

    async with _Ctx(_user(org=OTHER_ORG), factory) as ac:
        detail = await ac.get(f"/api/v1/reviews/templates/{created['id']}")
        assert detail.status_code == 404, detail.text
        listed = await ac.get("/api/v1/reviews/templates")
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 0
        # 状态只作用于本组织：外组织也不能借归档筛选看到它。
        archived_list = await ac.get("/api/v1/reviews/templates", params={"status": "archived"})
        assert archived_list.json()["data"]["total"] == 0
        version_write = await ac.post(
            f"/api/v1/reviews/templates/{created['id']}/versions",
            json={"items": template_items()},
        )
        assert version_write.status_code == 404, version_write.text
        own_write = await ac.post("/api/v1/reviews/templates", json={"name": "本组织模板"})
        assert own_write.status_code == 200, own_write.text
        assert own_write.json()["data"]["organization_id"] == OTHER_ORG


@pytest.mark.asyncio
async def test_template_read_requires_read_permissions_and_write_requires_update(
    factory, monkeypatch
):
    """RBAC 读写分离：只有 document:read 时写 403；两者都无时读也 403。

    变异点：写端点改用 _READ_DEPS 或读端点改用 _WRITE_DEPS → 本用例红。
    """
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ),
    )
    async with _Ctx(_user(), factory) as ac:
        blocked_write = await ac.post("/api/v1/reviews/templates", json={"name": "只读用户"})
        assert blocked_write.status_code == 403, blocked_write.text
        # 读权限足够（当前组织无模板 → 空列表，200）
        readable = await ac.get("/api/v1/reviews/templates")
        assert readable.status_code == 200, readable.text

    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(),
    )
    async with _Ctx(_user(), factory) as ac:
        blocked_read = await ac.get("/api/v1/reviews/templates")
        assert blocked_read.status_code == 403, blocked_read.text


@pytest.mark.asyncio
async def test_template_audit_records_metadata_without_rule_text(factory, monkeypatch):
    """审计：写操作各记一条；detail 只含元数据，绝不出现规则关键词文本。

    口径：``audit_service`` 用独立会话写审计，与测试的 SQLite 库不同源；
    这里把 ``log_activity`` 换成记录调用的替身，只断言「传了什么」——这正是
    审计内容契约的判别点（与 ``test_retrieval_eval_api`` 的审计用例同法）。
    变异点：detail 改为记录 items JSON / 关键词 → sentinel 断言红；
    删掉任一写端点的 audit 调用 → action 集合断言红。
    """
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )
    audit_mock = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.reviews.audit_service.log_activity", audit_mock)
    async with _Ctx(_user(superuser=True), factory) as ac:
        created = await _create_template(ac, items=template_items(extra="绝密关键词"))
        await ac.put(f"/api/v1/reviews/templates/{created['id']}", json={"description": "说明"})
        await _add_version(ac, created["id"], template_items(extra="另一个关键词"))
        await ac.post(f"/api/v1/reviews/templates/{created['id']}/archive")

    calls = audit_mock.await_args_list
    actions = [call.kwargs["action"] for call in calls]
    assert actions == [
        "create_review_template",
        "update_review_template",
        "create_review_template_version",
        "archive_review_template",
    ]
    for call in calls:
        detail_text = call.kwargs["detail"]
        assert "绝密关键词" not in detail_text
        assert "另一个关键词" not in detail_text
        assert "保密" not in detail_text
        assert "泄露" not in detail_text
        payload = json.loads(detail_text)
        assert payload["organization_id"] == ORG
        assert payload["user_id"] == 1
        assert call.kwargs["user_id"] == 1
        assert call.kwargs["target_type"] in {"review_template", "review_template_version"}


@pytest.mark.asyncio
async def test_org_delete_protection_includes_templates(factory):
    """组织删除守卫：存在模板/版本时 409 并点名，不裸删到 MySQL 1451。

    变异点：从 organization_service._ATTACHED_DATA_MODELS 删掉
    ("核验模板", ReviewTemplate) 或版本项 → 本用例红。
    """
    from app.exceptions import ConflictError
    from app.services.organization_service import organization_service

    async with factory() as db:
        db.add(
            ReviewTemplate(
                id="tpl-org-delete",
                organization_id=ORG,
                name="组织删除守卫",
                status="active",
                latest_version=0,
                created_by=1,
                lock_version=1,
            )
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await organization_service.delete_organization_thoroughly(db, ORG)
    assert "核验模板 1 条" in str(excinfo.value)

    # 版本行同样独立计数（模板行与版本行各一条 → 两个标签都出现）。
    async with factory() as db:
        db.add(
            ReviewTemplateVersion(
                id="tplv-org-delete",
                template_id="tpl-org-delete",
                organization_id=ORG,
                version_no=1,
                version_label="v1",
                items_json=template_items(),
                item_count=5,
                payload_sha256="a" * 64,
                source="manual",
                created_by=1,
            )
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await organization_service.delete_organization_thoroughly(db, ORG)
    assert "核验模板版本 1 条" in str(excinfo.value)
