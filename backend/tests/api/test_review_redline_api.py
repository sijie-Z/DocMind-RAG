"""入向红线台账 API（真实 SQLite + ASGI）：上传/列表/台账/处置全链路。

判别点（每个用例标题即变异点）：
- 上传→列表→台账→处置闭环：少任一路由或响应字段错位 → 红；
- 跨组织 403/404、只读 RBAC 写入 403、错案件作用域 404：漏 ACL → 红；
- 超限 413（带 max_body_bytes）、坏 DOCX 400：顺序错误或包装成 500 → 红；
- 陈旧锁 409 REVIEW_REDLINE_CONFLICT：乐观锁失效 → 红；
- 归档案件 409、重传序号递增、原文/案件/核验项/证据不可变；
- 响应不含 risk/verdict/recommendation/legal_opinion 等判定性字段；
- 审计 detail 不含条款原文与作者姓名。
"""

from __future__ import annotations

import io
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, update

import app.models  # noqa: F401
from app.models.rbac import PermissionType
from app.models.review import (
    REVIEW_REDLINE_TEXT_PROVENANCE,
    ReviewCase,
    ReviewEvidence,
    ReviewItem,
    ReviewRedlineRow,
    ReviewRedlineSubmission,
)
from app.models.user_audit import UserActivityLog
from app.schemas.review import (
    RedlineLedgerResponse,
    RedlineRowResponse,
    RedlineSubmissionSummary,
)
from tests.api.test_review_case_api import (
    _create_case,
    _Ctx,
    _user,
)
from tests.api.test_review_case_api import (
    factory as _review_case_factory,
)

# 别名复用案件 API 夹具（真实 SQLite + 三份同组织文档）。
factory = _review_case_factory

SENTINEL_CLAUSE = "付款期限改为六十日的特别约定"
SENTINEL_AUTHOR = "对手方律师张某"
SENTINEL_COMMENT = "请确认该付款期限"


def _grant(*permissions: PermissionType):
    async def _grant_all(_db, _user_obj, organization_id=None):
        return {permission.value for permission in permissions}

    return _grant_all


def _grant_all_users(monkeypatch):
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ, PermissionType.DOCUMENT_UPDATE),
    )


def _docx_bytes(
    *,
    clause: str = SENTINEL_CLAUSE,
    author: str = SENTINEL_AUTHOR,
    comment: str = SENTINEL_COMMENT,
) -> bytes:
    """构造带 w:ins / w:del / 批注的最小合法 DOCX（纯内存，无临时文件）。"""
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:del w:id="1" w:author="{author}" w:date="2026-01-01T00:00:00Z">
        <w:r><w:delText>付款期限为30天</w:delText></w:r>
      </w:del>
      <w:ins w:id="2" w:author="{author}" w:date="2026-01-02T00:00:00Z">
        <w:r><w:t>{clause}</w:t></w:r>
      </w:ins>
      <w:commentRangeStart w:id="0"/>
      <w:r><w:t>{clause}</w:t></w:r>
      <w:commentRangeEnd w:id="0"/>
      <w:r><w:commentReference w:id="0"/></w:r>
    </w:p>
    <w:sectPr/>
  </w:body>
</w:document>""".encode()
    comments_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:comment w:id="0" w:author="{author}" w:date="2026-01-03T00:00:00Z">
    <w:p><w:r><w:t>{comment}</w:t></w:r></w:p>
  </w:comment>
</w:comments>""".encode()
    content_types = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>
</Types>"""
    root_rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""
    doc_rels = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/>
</Relationships>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("word/document.xml", document_xml)
        zf.writestr("word/comments.xml", comments_xml)
        zf.writestr("word/_rels/document.xml.rels", doc_rels)
    return buf.getvalue()


def _patch_storage(monkeypatch):
    """拦截 MinIO 写入，返回对象名；测试不依赖外部存储。"""

    def _fake_put_object(*, object_name, data, length, **kwargs):
        return object_name

    monkeypatch.setattr("app.core.minio_client.minio_client.put_object", _fake_put_object)


async def _upload(ac, case_id: str, data: bytes | None = None, *, filename="redlines.docx"):
    return await ac.post(
        f"/api/v1/reviews/{case_id}/redlines",
        files={
            "file": (
                filename,
                data if data is not None else _docx_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )


def _forbidden_keys(node):
    """递归列出所有判定性字段键（任何层级出现都不允许）。"""
    forbidden = {"risk", "verdict", "recommendation", "legal_opinion"}
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in forbidden:
                found.add(key.lower())
            found |= _forbidden_keys(value)
    elif isinstance(node, list):
        for item in node:
            found |= _forbidden_keys(item)
    return found


def _audit_texts(monkeypatch_calls, db_rows) -> list[str]:
    texts = []
    for call in monkeypatch_calls:
        detail = call.kwargs.get("detail")
        if isinstance(detail, str):
            texts.append(detail)
    texts.extend(row.detail or "" for row in db_rows)
    return texts


@pytest.mark.asyncio
async def test_upload_list_ledger_disposition_roundtrip(factory, monkeypatch):
    """闭环：上传批次→列表→台账→处置→回读；响应形状与 Pydantic 模型一致。"""
    from unittest.mock import AsyncMock

    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    audit_mock = AsyncMock()
    monkeypatch.setattr("app.services.audit_service.AuditService.log_activity", audit_mock)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        case_id = case["id"]

        uploaded = await _upload(ac, case_id)
        assert uploaded.status_code == 200, uploaded.text
        submission = uploaded.json()["data"]
        assert RedlineSubmissionSummary.model_validate(submission)
        assert submission["case_id"] == case_id
        assert submission["sequence_no"] == 1
        assert submission["file_sha256"]
        assert len(submission["file_sha256"]) == 64
        assert submission["uploaded_by"] == 1
        assert not _forbidden_keys(submission)

        listed = await ac.get(f"/api/v1/reviews/{case_id}/redlines")
        assert listed.status_code == 200, listed.text
        list_data = listed.json()["data"]
        assert set(list_data) >= {"items", "total"}
        assert list_data["total"] == 1
        assert [item["id"] for item in list_data["items"]] == [submission["id"]]
        assert not _forbidden_keys(list_data)

        ledger = await ac.get(f"/api/v1/reviews/{case_id}/redlines/{submission['id']}")
        assert ledger.status_code == 200, ledger.text
        ledger_data = ledger.json()["data"]
        # 服务端直接返回服务层字典，响应契约与服务/前端统一使用 items。
        assert set(ledger_data) >= {
            "items",
            "total",
            "page",
            "page_size",
            "counts",
            "disclaimer",
            "has_unparsed",
            "parse_warnings",
            "has_truncation",
        }
        assert ledger_data["page"] == 1
        assert ledger_data["page_size"] == 20
        assert ledger_data["total"] >= 1
        assert isinstance(ledger_data["counts"], dict)
        assert isinstance(ledger_data["disclaimer"], str)
        assert isinstance(ledger_data["has_unparsed"], bool)
        assert isinstance(ledger_data["parse_warnings"], list)
        assert isinstance(ledger_data["has_truncation"], bool)
        for row in ledger_data["items"]:
            assert RedlineRowResponse.model_validate(row)
            assert row["case_id"] == case_id
            assert row["submission_id"] == submission["id"]
            assert row["text_provenance"] == REVIEW_REDLINE_TEXT_PROVENANCE
            assert row["disposition"] == "pending"
            assert row["lock_version"] == 1
        # Schema 回环：服务输出字段必须被响应模型原样保留（不多也不少）。
        parsed = RedlineLedgerResponse.model_validate(ledger_data)
        assert parsed.parse_warnings == ledger_data["parse_warnings"]
        assert parsed.has_truncation == ledger_data["has_truncation"]
        model_data = parsed.model_dump(mode="json")
        assert model_data["parse_warnings"] == ledger_data["parse_warnings"]
        assert model_data["has_truncation"] == ledger_data["has_truncation"]
        assert "rows" not in model_data
        assert not _forbidden_keys(ledger_data)

        row = ledger_data["items"][0]
        put = await ac.put(
            f"/api/v1/reviews/{case_id}/redlines/{submission['id']}/rows/{row['id']}/disposition",
            json={
                "disposition": "reviewed",
                "note": "已人工确认",
                "expected_lock_version": row["lock_version"],
            },
        )
        assert put.status_code == 200, put.text
        updated = put.json()["data"]
        assert updated["disposition"] == "reviewed"
        assert updated["note"] == "已人工确认"
        assert updated["lock_version"] == row["lock_version"] + 1
        assert not _forbidden_keys(updated)

        reread = await ac.get(
            f"/api/v1/reviews/{case_id}/redlines/{submission['id']}",
            params={"disposition": "reviewed"},
        )
        assert reread.status_code == 200, reread.text
        reread_data = reread.json()["data"]
        assert reread_data["total"] == 1
        assert reread_data["items"][0]["id"] == row["id"]
        assert reread_data["items"][0]["disposition"] == "reviewed"


@pytest.mark.asyncio
async def test_cross_org_and_wrong_case_scope_are_denied(factory, monkeypatch):
    """跨组织 403/404、错案件作用域 404：漏掉 get_case_for_user → 红。"""
    from tests.api.test_review_case_api import OTHER_ORG

    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        other_case = await _create_case(ac)
        assert other_case["id"] != case["id"]
        uploaded = await _upload(ac, case["id"])
        assert uploaded.status_code == 200, uploaded.text
        submission_id = uploaded.json()["data"]["id"]
        ledger = await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission_id}")
        row_id = ledger.json()["data"]["items"][0]["id"]

    async with _Ctx(_user(org=OTHER_ORG), factory) as ac:
        for method, path, kwargs in (
            ("get", f"/api/v1/reviews/{case['id']}/redlines", {}),
            ("get", f"/api/v1/reviews/{case['id']}/redlines/{submission_id}", {}),
            (
                "put",
                f"/api/v1/reviews/{case['id']}/redlines/{submission_id}/rows/{row_id}/disposition",
                {"json": {"disposition": "reviewed", "expected_lock_version": 1}},
            ),
        ):
            response = await getattr(ac, method)(path, **kwargs)
            assert response.status_code in (403, 404), (method, path, response.text)
        foreign_upload = await _upload(ac, case["id"])
        assert foreign_upload.status_code in (403, 404), foreign_upload.text

    # 同组织但案件不匹配：批次 id 不能借到别的案件下读取/处置。
    async with _Ctx(_user(superuser=True), factory) as ac:
        wrong_ledger = await ac.get(f"/api/v1/reviews/{other_case['id']}/redlines/{submission_id}")
        assert wrong_ledger.status_code == 404, wrong_ledger.text
        wrong_put = await ac.put(
            f"/api/v1/reviews/{other_case['id']}/redlines/{submission_id}"
            f"/rows/{row_id}/disposition",
            json={"disposition": "reviewed", "expected_lock_version": 1},
        )
        assert wrong_put.status_code == 404, wrong_put.text


@pytest.mark.asyncio
async def test_readonly_rbac_denies_writes(factory, monkeypatch):
    """RBAC：只有 document:read 的读写端点分离；写 403、读 200。"""
    monkeypatch.setattr(
        "app.core.security.permission_service.get_user_permissions",
        _grant(PermissionType.DOCUMENT_READ),
    )
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        uploaded = await _upload(ac, case["id"])
        assert uploaded.status_code == 200, uploaded.text
        submission_id = uploaded.json()["data"]["id"]
        row_id = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission_id}")).json()[
            "data"
        ]["items"][0]["id"]

    async with _Ctx(_user(), factory) as ac:
        denied_upload = await _upload(ac, case["id"])
        assert denied_upload.status_code == 403, denied_upload.text
        denied_put = await ac.put(
            f"/api/v1/reviews/{case['id']}/redlines/{submission_id}/rows/{row_id}/disposition",
            json={"disposition": "reviewed", "expected_lock_version": 1},
        )
        assert denied_put.status_code == 403, denied_put.text
        allowed_read = await ac.get(f"/api/v1/reviews/{case['id']}/redlines")
        assert allowed_read.status_code == 200, allowed_read.text


@pytest.mark.asyncio
async def test_oversize_upload_is_413_with_limits(factory, monkeypatch):
    """超限先于读正文：413 + detail.max_body_bytes/received_bytes。"""
    from app.services import review_redline_service as redline_service

    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    monkeypatch.setattr(redline_service, "MAX_REDLINE_UPLOAD_BYTES", 512)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        response = await _upload(ac, case["id"], b"x" * 1024)
        assert response.status_code == 413, response.text
        body = response.json()
        assert body["error_code"] == "PAYLOAD_TOO_LARGE"
        # 小文件的 multipart 整包在整包预算内，由实测文件大小闸门拦截。
        assert body["detail"]["max_body_bytes"] == 512
        assert body["detail"]["received_bytes"] == 1024


@pytest.mark.asyncio
async def test_content_length_guard_rejects_before_reading_body(factory, monkeypatch):
    """第一道闸门：声明超过整包预算的 Content-Length 立即 413，不等正文读完。"""
    from app.api.v1.endpoints import reviews as endpoint
    from app.services import review_redline_service as redline_service

    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    monkeypatch.setattr(redline_service, "MAX_REDLINE_UPLOAD_BYTES", 512)
    body_budget = endpoint._redline_body_budget_bytes()
    assert body_budget == 512 + 1024 * 1024
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        declared = body_budget + 1
        response = await ac.post(
            f"/api/v1/reviews/{case['id']}/redlines",
            files={"file": ("redlines.docx", _docx_bytes(), "application/octet-stream")},
            headers={"Content-Length": str(declared)},
        )
        assert response.status_code == 413, response.text
        body = response.json()
        assert body["error_code"] == "PAYLOAD_TOO_LARGE"
        assert body["detail"] == {
            "max_body_bytes": body_budget,
            "received_bytes": declared,
        }


@pytest.mark.asyncio
async def test_multipart_overhead_does_not_trigger_file_size_gate(factory, monkeypatch):
    """判别点：文件略小于 20MB，但 multipart 整包超过旧阈值时仍应放行。

    这里用合成小字节 + 声明一个大于旧阈值的 Content-Length 模拟真实整包开销；
    第一道闸门应对 multipart 开销留余量，文件是否超限只看实测字节数。
    """
    from app.services import review_redline_service as redline_service

    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    # 保留真实 20MB 文件大小上限，避免制造大文件；只把整包声明值抬到旧阈值之上。
    assert redline_service.MAX_REDLINE_UPLOAD_BYTES == 20 * 1024 * 1024
    actual_docx = _docx_bytes()
    assert len(actual_docx) < redline_service.MAX_REDLINE_UPLOAD_BYTES
    old_threshold = redline_service.MAX_REDLINE_UPLOAD_BYTES
    declared = old_threshold + 4096
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        response = await ac.post(
            f"/api/v1/reviews/{case['id']}/redlines",
            files={"file": ("redlines.docx", actual_docx, "application/octet-stream")},
            headers={"Content-Length": str(declared)},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["file_size"] == len(actual_docx)


@pytest.mark.asyncio
async def test_ledger_schema_roundtrip_surfaces_parse_warnings_and_truncation(factory, monkeypatch):
    """判别点：服务快照中的解析警告/截断标记必须原样进入响应模型与 JSON。

    直接给批次写入警告列表，走真实 API 读取台账，验证 schema 与服务输出同形
    （不是只测 Pydantic 默认值）。
    """
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    warnings = [
        "row_cap_exceeded:parsed=2001;kept=2000",
        "text_cap_exceeded:ordinal=9;field=after_text",
        "overlap:ins:1:del:2",
    ]
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        submission = (await _upload(ac, case["id"])).json()["data"]
        async with factory() as db:
            stored = await db.get(ReviewRedlineSubmission, submission["id"])
            stored.parse_warnings = warnings
            await db.commit()

        ledger = await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}")
        assert ledger.status_code == 200, ledger.text
        data = ledger.json()["data"]
        assert data["parse_warnings"] == warnings
        assert data["has_truncation"] is True
        parsed = RedlineLedgerResponse.model_validate(data)
        assert parsed.parse_warnings == warnings
        assert parsed.has_truncation is True
        assert parsed.model_dump(mode="json")["parse_warnings"] == warnings


@pytest.mark.asyncio
async def test_empty_string_filters_are_treated_as_absent(factory, monkeypatch):
    """空字符串筛选参数（?revision_type=&disposition=）不能变成 422。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        submission = (await _upload(ac, case["id"])).json()["data"]
        response = await ac.get(
            f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}",
            params={"revision_type": "", "disposition": "", "page": 1, "page_size": 20},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["total"] >= 1


@pytest.mark.asyncio
async def test_corrupt_upload_is_400_and_leaves_no_submission(factory, monkeypatch):
    """坏 ZIP 400：解析失败不得留下半截批次/行。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        bad = await _upload(ac, case["id"], b"not-a-zip")
        assert bad.status_code == 400, bad.text
        listed = await ac.get(f"/api/v1/reviews/{case['id']}/redlines")
        assert listed.status_code == 200, listed.text
        assert listed.json()["data"]["total"] == 0


@pytest.mark.asyncio
async def test_stale_lock_version_is_409_conflict(factory, monkeypatch):
    """陈旧 expected_lock_version 409 REVIEW_REDLINE_CONFLICT，不改行。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        submission = (await _upload(ac, case["id"])).json()["data"]
        ledger = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}")).json()[
            "data"
        ]
        row = ledger["items"][0]
        stale = await ac.put(
            f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}"
            f"/rows/{row['id']}/disposition",
            json={"disposition": "reviewed", "expected_lock_version": 999},
        )
        assert stale.status_code == 409, stale.text
        assert stale.json()["error_code"] == "REVIEW_REDLINE_CONFLICT"

        reread = await ac.get(
            f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}",
            params={"disposition": "pending"},
        )
        assert reread.json()["data"]["total"] >= 1
        assert (
            next(item for item in reread.json()["data"]["items"] if item["id"] == row["id"])[
                "lock_version"
            ]
            == 1
        )


@pytest.mark.asyncio
async def test_stale_lock_after_concurrent_write_is_409_with_versions(factory, monkeypatch):
    """真实并发场景：另一人先提交后，旧版本重试 409 且给出当前版本号。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        submission = (await _upload(ac, case["id"])).json()["data"]
        ledger = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}")).json()[
            "data"
        ]
        row = ledger["items"][0]
        url = (
            f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}/rows/{row['id']}/disposition"
        )
        # 第一人用 v1 提交成功，行版本变为 v2。
        first = await ac.put(
            url, json={"disposition": "reviewed", "expected_lock_version": row["lock_version"]}
        )
        assert first.status_code == 200, first.text
        assert first.json()["data"]["lock_version"] == row["lock_version"] + 1
        # 第二人仍持 v1 重试：必须 409，且 detail 指明当前版本，便于前端刷新。
        stale = await ac.put(
            url, json={"disposition": "not_relevant", "expected_lock_version": row["lock_version"]}
        )
        assert stale.status_code == 409, stale.text
        body = stale.json()
        assert body["error_code"] == "REVIEW_REDLINE_CONFLICT"
        assert body["detail"]["expected_lock_version"] == row["lock_version"]
        assert body["detail"]["current_lock_version"] == row["lock_version"] + 1
        reread = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}")).json()[
            "data"
        ]["items"][0]
        assert reread["disposition"] == "reviewed"
        assert reread["lock_version"] == row["lock_version"] + 1


@pytest.mark.asyncio
async def test_evidence_id_without_item_id_is_422(factory, monkeypatch):
    """schema 守卫：evidence_id 必须绑定 item_id。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        submission = (await _upload(ac, case["id"])).json()["data"]
        row = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}")).json()[
            "data"
        ]["items"][0]
        invalid = await ac.put(
            f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}"
            f"/rows/{row['id']}/disposition",
            json={
                "disposition": "reviewed",
                "expected_lock_version": 1,
                "evidence_id": str(uuid.uuid4()),
            },
        )
        assert invalid.status_code == 422, invalid.text


@pytest.mark.asyncio
async def test_archived_case_upload_is_409(factory, monkeypatch):
    """归档案件只读：归档后不能上传新批次。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        async with factory() as db:
            await db.execute(
                update(ReviewCase)
                .where(ReviewCase.id == case["id"])
                .values(
                    archived_at=datetime.now(UTC),
                    archived_by=1,
                    status="blocked",
                )
            )
            await db.commit()
        archived = await _upload(ac, case["id"])
        assert archived.status_code == 409, archived.text


@pytest.mark.asyncio
async def test_archived_case_disposition_is_409(factory, monkeypatch):
    """归档案件只读：归档后不能处置台账行。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        submission = (await _upload(ac, case["id"])).json()["data"]
        row = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}")).json()[
            "data"
        ]["items"][0]
        async with factory() as db:
            await db.execute(
                update(ReviewCase)
                .where(ReviewCase.id == case["id"])
                .values(
                    archived_at=datetime.now(UTC),
                    archived_by=1,
                    status="blocked",
                )
            )
            await db.commit()
        denied = await ac.put(
            f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}"
            f"/rows/{row['id']}/disposition",
            json={"disposition": "reviewed", "expected_lock_version": row["lock_version"]},
        )
        assert denied.status_code == 409, denied.text
        assert denied.json()["error_code"] == "REVIEW_CASE_ARCHIVED"
        # 归档不应改动台账行状态/版本。
        reread = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission['id']}")).json()[
            "data"
        ]["items"][0]
        assert reread["disposition"] == "pending"
        assert reread["lock_version"] == row["lock_version"]


@pytest.mark.asyncio
async def test_reupload_increments_sequence_and_preserves_history(factory, monkeypatch):
    """重传=新增不可变批次：sequence_no 递增，旧批次仍可读。"""
    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        first = (await _upload(ac, case["id"])).json()["data"]
        second = (await _upload(ac, case["id"])).json()["data"]
        assert second["sequence_no"] == first["sequence_no"] + 1
        assert second["id"] != first["id"]
        listed = (await ac.get(f"/api/v1/reviews/{case['id']}/redlines")).json()["data"]
        assert listed["total"] == 2
        for submission_id in (first["id"], second["id"]):
            ledger = await ac.get(f"/api/v1/reviews/{case['id']}/redlines/{submission_id}")
            assert ledger.status_code == 200, ledger.text
            assert ledger.json()["data"]["total"] >= 1


@pytest.mark.asyncio
async def test_upload_and_disposition_do_not_mutate_case_or_evidence(factory, monkeypatch):
    """不可变性：台账写入不碰案件/核验项/证据，原文与哈希处置后不变。"""
    from unittest.mock import AsyncMock

    _grant_all_users(monkeypatch)
    _patch_storage(monkeypatch)
    audit_mock = AsyncMock()
    monkeypatch.setattr("app.services.audit_service.AuditService.log_activity", audit_mock)
    async with _Ctx(_user(superuser=True), factory) as ac:
        case = await _create_case(ac)
        case_id = case["id"]
        async with factory() as db:
            before_case = (
                await db.execute(select(ReviewCase).where(ReviewCase.id == case_id))
            ).scalar_one()
            case_snapshot = (
                before_case.status,
                before_case.title,
                before_case.lock_version,
                before_case.rule_snapshot_id,
                before_case.archived_at,
            )
            items_before = [
                (row.id, row.status, row.decision_note, row.updated_at)
                for row in (
                    await db.execute(select(ReviewItem).where(ReviewItem.case_id == case_id))
                ).scalars()
            ]
            evidence_before = [
                (row.id, row.item_id, row.selected, row.quote_text)
                for row in (
                    await db.execute(
                        select(ReviewEvidence).where(ReviewEvidence.case_id == case_id)
                    )
                ).scalars()
            ]

        submission = (await _upload(ac, case_id)).json()["data"]
        row = (await ac.get(f"/api/v1/reviews/{case_id}/redlines/{submission['id']}")).json()[
            "data"
        ]["items"][0]
        source_snapshot = {
            key: row[key]
            for key in (
                "before_text",
                "after_text",
                "before_sha256",
                "after_sha256",
                "text_provenance",
                "item_id",
                "evidence_id",
            )
        }
        put = await ac.put(
            f"/api/v1/reviews/{case_id}/redlines/{submission['id']}/rows/{row['id']}/disposition",
            json={"disposition": "needs_clarification", "expected_lock_version": 1},
        )
        assert put.status_code == 200, put.text

        async with factory() as db:
            after_case = (
                await db.execute(select(ReviewCase).where(ReviewCase.id == case_id))
            ).scalar_one()
            assert (
                after_case.status,
                after_case.title,
                after_case.lock_version,
                after_case.rule_snapshot_id,
                after_case.archived_at,
            ) == case_snapshot
            assert [
                (r.id, r.status, r.decision_note, r.updated_at)
                for r in (
                    await db.execute(select(ReviewItem).where(ReviewItem.case_id == case_id))
                ).scalars()
            ] == items_before
            assert [
                (r.id, r.item_id, r.selected, r.quote_text)
                for r in (
                    await db.execute(
                        select(ReviewEvidence).where(ReviewEvidence.case_id == case_id)
                    )
                ).scalars()
            ] == evidence_before
            stored_row = (
                await db.execute(select(ReviewRedlineRow).where(ReviewRedlineRow.id == row["id"]))
            ).scalar_one()
            for key, value in source_snapshot.items():
                assert getattr(stored_row, key) == value

        texts = _audit_texts(
            audit_mock.await_args_list,
            (await _audit_rows(factory, case_id)),
        )
        assert texts, "写操作应留审计记录"
        for text in texts:
            assert SENTINEL_CLAUSE not in text
            assert SENTINEL_AUTHOR not in text


async def _audit_rows(factory, case_id: str):
    async with factory() as db:
        return list(
            (
                await db.execute(
                    select(UserActivityLog).where(UserActivityLog.target_id == case_id)
                )
            ).scalars()
        )
