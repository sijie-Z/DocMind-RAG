"""入向红线台账数据库服务单测（SQLite 内存/临时库，MinIO 用 monkeypatch 隔离）。"""

from __future__ import annotations

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  — 注册所有模型
from app.core.database import Base
from app.exceptions import ConflictError, NotFoundError, ServiceUnavailableError
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.organization import Organization, user_organization
from app.models.review import (
    REVIEW_REDLINE_TEXT_PROVENANCE,
    ReviewCase,
    ReviewEvidence,
    ReviewExport,
    ReviewItem,
    ReviewRedlineRow,
    ReviewRedlineSubmission,
)
from app.models.user import User
from app.models.user_audit import UserActivityLog
from app.schemas.review import RedlineDispositionUpdate
from app.services import review_redline_service as svc

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = f'xmlns:w="{W}"'
ORG_ID = 11
OTHER_ORG_ID = 12
USER_ID = 21
OTHER_USER_ID = 22
CASE_ID = "11111111-1111-4111-8111-111111111111"
OTHER_CASE_ID = "22222222-2222-4222-8222-222222222222"
ITEM_ID = "33333333-3333-4333-8333-333333333333"
EVIDENCE_ID = "44444444-4444-4444-8444-444444444444"
SECRET_CLAUSE = "第9条 绝对保密条款不得外泄"
SECRET_AUTHOR = "对方首席律师张伟"


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'redline-ledger.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """任何测试都不得访问 MinIO；记录调用供断言。"""
    calls: list[dict] = []

    def fake_put(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})

    monkeypatch.setattr(svc.minio_client, "put_object", fake_put)
    return {"calls": calls}


def _docx(*, author: str = "对方律师", text: str = "60天") -> bytes:
    document = (
        f'<w:p><w:r><w:t xml:space="preserve">{SECRET_CLAUSE}</w:t></w:r>'
        f'<w:del w:id="1" w:author="{author}" w:date="2026-01-01T00:00:00Z">'
        "<w:r><w:delText>30天</w:delText></w:r></w:del>"
        f'<w:ins w:id="2" w:author="{author}" w:date="2026-01-01T01:00:00Z">'
        f"<w:r><w:t>{text}</w:t></w:r></w:ins></w:p>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            f'<?xml version="1.0"?><w:document {NS}><w:body>{document}</w:body></w:document>',
        )
    return buffer.getvalue()


async def _seed(factory, *, archived: bool = False) -> None:
    async with factory() as session:
        user = User(
            id=USER_ID,
            username="redline-user",
            email="redline-user@example.test",
            hashed_password="x",
        )
        other_user = User(
            id=OTHER_USER_ID,
            username="redline-other",
            email="redline-other@example.test",
            hashed_password="x",
        )
        session.add_all([user, other_user])
        await session.flush()
        session.add_all(
            [
                Organization(id=ORG_ID, name="红线组织", owner_id=USER_ID),
                Organization(id=OTHER_ORG_ID, name="其他组织", owner_id=OTHER_USER_ID),
            ]
        )
        await session.flush()
        user.organization_id = ORG_ID
        other_user.organization_id = OTHER_ORG_ID
        await session.execute(
            user_organization.insert().values(user_id=USER_ID, organization_id=ORG_ID)
        )
        await session.execute(
            user_organization.insert().values(user_id=OTHER_USER_ID, organization_id=OTHER_ORG_ID)
        )
        for doc_id in ("d1", "d2", "d3"):
            session.add(
                Document(
                    id=doc_id,
                    filename=f"{doc_id}.docx",
                    file_path=f"{ORG_ID}/{doc_id}.docx",
                    file_size=100,
                    file_type=DocumentType.WORD,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG_ID,
                    uploaded_by=USER_ID,
                )
            )
        for case_id, org_id in ((CASE_ID, ORG_ID), (OTHER_CASE_ID, OTHER_ORG_ID)):
            session.add(
                ReviewCase(
                    id=case_id,
                    organization_id=org_id,
                    title="回传红线",
                    policy_document_id="d1",
                    base_document_id="d2",
                    revised_document_id="d3",
                    status="in_review",
                    checklist_version="v1",
                    created_by=USER_ID,
                    lock_version=1,
                    archived_at=datetime.now(UTC) if archived and case_id == CASE_ID else None,
                )
            )
        session.add(
            ReviewItem(
                id=ITEM_ID,
                case_id=CASE_ID,
                item_key="payment_term",
                ordinal=1,
                status="pending",
            )
        )
        session.add(
            ReviewEvidence(
                id=EVIDENCE_ID,
                case_id=CASE_ID,
                item_id=ITEM_ID,
                side="revised",
                role="main",
                selected=True,
                document_id="d3",
                doc_role="contract",
                document_sha256="a" * 64,
                chunk_id_at_capture="chunk-1",
                chunk_index=0,
                quote_text=SECRET_CLAUSE,
                quote_sha256="b" * 64,
                text_provenance="parser_original",
            )
        )
        session.add(
            ReviewExport(
                id=str(uuid.uuid4()),
                case_id=CASE_ID,
                organization_id=ORG_ID,
                schema_version=1,
                payload={"item_count": 1},
                payload_sha256="c" * 64,
                item_count=1,
                needs_human_count=0,
                unidentified_count=0,
                created_by=USER_ID,
            )
        )
        await session.commit()


async def _load(factory, model, ident):
    async with factory() as session:
        return await session.get(model, ident)


@pytest.mark.asyncio
async def test_upload_sequence_increment_and_minio_object(factory, no_network):
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        payload = _docx()
        first = await svc.upload_submission(
            session, case=case, current_user=user, filename="对方回传.docx", data=payload
        )
        second = await svc.upload_submission(
            session, case=case, current_user=user, filename="第二版.docx", data=payload
        )
        assert first["sequence_no"] == 1
        assert second["sequence_no"] == 2
        assert first["file_sha256"] == second["file_sha256"]
        # 普通插入/删除没有无法归类的结构 → parsed；unparsed 行才会变 partial。
        assert first["status"] == second["status"] == "parsed"
        assert first["revision_count"] == 2
        assert first["parser_version"] == svc.parser_version
        assert first["created_at"].endswith("+00:00") or "T" in first["created_at"]
        assert set(first) == {
            "id",
            "case_id",
            "sequence_no",
            "filename",
            "file_sha256",
            "file_size",
            "parser_version",
            "status",
            "revision_count",
            "comment_count",
            "unparsed_count",
            "parse_warnings",
            "uploaded_by",
            "created_at",
        }
        assert no_network["calls"] and len(no_network["calls"]) == 2
        kwargs = no_network["calls"][0]["kwargs"]
        assert kwargs["object_name"] == f"redlines/{ORG_ID}/{CASE_ID}/{first['id']}.docx"
        assert kwargs["length"] == len(payload)
        assert kwargs["content_type"] == (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert isinstance(kwargs["data"], io.BytesIO)
        assert kwargs["data"].getvalue() == payload

    async with factory() as session:
        rows = (await session.execute(select(ReviewRedlineRow))).scalars().all()
        assert len(rows) == 4
        assert all(row.text_provenance == REVIEW_REDLINE_TEXT_PROVENANCE for row in rows)
        assert all(row.disposition == "pending" and row.lock_version == 1 for row in rows)
        assert rows[0].pair_key == rows[1].pair_key
        assert (await session.execute(select(func.count(UserActivityLog.id)))).scalar() == 0


@pytest.mark.asyncio
async def test_upload_rejects_oversize_without_writing(factory, no_network):
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        with pytest.raises(Exception) as caught:
            await svc.upload_submission(
                session,
                case=case,
                current_user=user,
                filename="big.docx",
                data=b"x" * (svc.MAX_REDLINE_UPLOAD_BYTES + 1),
            )
        assert caught.value.status_code == 400
    assert not no_network["calls"]


@pytest.mark.asyncio
async def test_storage_failure_rolls_back_database(factory, monkeypatch):
    await _seed(factory)

    def fail(*args, **kwargs):
        raise RuntimeError("minio offline")

    monkeypatch.setattr(svc.minio_client, "put_object", fail)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        with pytest.raises(ServiceUnavailableError):
            await svc.upload_submission(
                session, case=case, current_user=user, filename="x.docx", data=_docx()
            )
    async with factory() as session:
        assert (await session.execute(select(func.count(ReviewRedlineSubmission.id)))).scalar() == 0
        assert (await session.execute(select(func.count(ReviewRedlineRow.id)))).scalar() == 0


@pytest.mark.asyncio
async def test_ledger_shape_counts_filters_and_list(factory):
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        listing = await svc.list_submissions(session, case=case)
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == summary["id"]
        ledger = await svc.get_ledger(
            session, case=case, submission_id=summary["id"], page=1, page_size=1
        )
        assert set(ledger) == {
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
        assert ledger["parse_warnings"] == summary["parse_warnings"]
        assert isinstance(ledger["has_truncation"], bool)
        assert ledger["disclaimer"] == "只记录文本变化事实，不构成法律意见"
        assert ledger["has_unparsed"] is False
        assert ledger["counts"] == {"revision": 2, "comment": 0, "unparsed": 0}
        assert ledger["total"] == 2
        assert len(ledger["items"]) == 1
        filtered = await svc.get_ledger(
            session, case=case, submission_id=summary["id"], revision_type="insertion"
        )
        assert filtered["total"] == 1
        assert all(row["revision_type"] == "insertion" for row in filtered["items"])
        # counts 与 total/items 同口径：过滤 insertion 后只剩 1 条 revision。
        assert filtered["counts"] == {"revision": 1, "comment": 0, "unparsed": 0}


@pytest.mark.asyncio
async def test_disposition_optimistic_lock_and_audit_privacy(factory):
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        ledger = await svc.get_ledger(session, case=case, submission_id=summary["id"])
        row_id = ledger["items"][0]["id"]

        update = RedlineDispositionUpdate(
            disposition="reviewed",
            note="人工核对过",
            expected_lock_version=1,
            item_id=ITEM_ID,
            evidence_id=EVIDENCE_ID,
        )
        done = await svc.update_disposition(
            session,
            case=case,
            current_user=user,
            submission_id=summary["id"],
            row_id=row_id,
            update=update,
        )
        assert done["disposition"] == "reviewed"
        assert done["lock_version"] == 2
        assert done["item_id"] == ITEM_ID
        assert done["evidence_id"] == EVIDENCE_ID
        assert done["text_provenance"] == REVIEW_REDLINE_TEXT_PROVENANCE
        assert done["before_text"] is not None
        assert done["disposition_at"] is not None

        stale = RedlineDispositionUpdate(disposition="not_relevant", expected_lock_version=1)
        with pytest.raises(ConflictError) as caught:
            await svc.update_disposition(
                session,
                case=case,
                current_user=user,
                submission_id=summary["id"],
                row_id=row_id,
                update=stale,
            )
        assert caught.value.error_code == "REVIEW_REDLINE_CONFLICT"
        assert caught.value.detail == {"expected_lock_version": 1, "current_lock_version": 2}

    async with factory() as session:
        logs = (await session.execute(select(UserActivityLog))).scalars().all()
        assert len(logs) == 1
        assert logs[0].action == "review_redline_disposition"
        assert logs[0].target_type == "review_case"
        assert logs[0].target_id == CASE_ID
        detail = json.loads(logs[0].detail)
        assert set(detail) == {
            "organization_id",
            "user_id",
            "case_id",
            "submission_id",
            "row_id",
            "disposition",
            "lock_version",
        }
        assert SECRET_CLAUSE not in logs[0].detail
        assert SECRET_AUTHOR not in logs[0].detail


@pytest.mark.asyncio
async def test_no_mutation_of_case_item_evidence_export_or_templates(factory):
    await _seed(factory)
    before_case = await _load(factory, ReviewCase, CASE_ID)
    before_item = await _load(factory, ReviewItem, ITEM_ID)
    before_evidence = await _load(factory, ReviewEvidence, EVIDENCE_ID)
    case_lock = before_case.lock_version
    item_status = before_item.status
    evidence_selected = before_evidence.selected

    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        await svc.update_disposition(
            session,
            case=case,
            current_user=user,
            submission_id=summary["id"],
            row_id=row["id"],
            update=RedlineDispositionUpdate(disposition="reviewed", expected_lock_version=1),
        )

    after_case = await _load(factory, ReviewCase, CASE_ID)
    after_item = await _load(factory, ReviewItem, ITEM_ID)
    after_evidence = await _load(factory, ReviewEvidence, EVIDENCE_ID)
    assert after_case.lock_version == case_lock
    assert after_case.status == "in_review"
    assert after_case.title == before_case.title
    assert after_item.status == item_status
    assert after_item.decided_by is None
    assert after_evidence.selected == evidence_selected
    async with factory() as session:
        assert (await session.execute(select(func.count(ReviewExport.id)))).scalar() == 1


@pytest.mark.asyncio
async def test_archived_case_blocks_writes(factory):
    await _seed(factory, archived=True)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        with pytest.raises(ConflictError) as caught:
            await svc.upload_submission(
                session, case=case, current_user=user, filename="x.docx", data=_docx()
            )
        assert caught.value.error_code == "REVIEW_CASE_ARCHIVED"

    # 非归档状态下上传一行，再将案件归档，验证 disposition 同样被冻结。
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        await session.commit()
        case.archived_at = None
        await session.commit()
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        case.archived_at = datetime.now(UTC)
        await session.commit()
        with pytest.raises(ConflictError) as caught:
            await svc.update_disposition(
                session,
                case=case,
                current_user=user,
                submission_id=summary["id"],
                row_id=row["id"],
                update=RedlineDispositionUpdate(disposition="reviewed", expected_lock_version=1),
            )
        assert caught.value.error_code == "REVIEW_CASE_ARCHIVED"


@pytest.mark.asyncio
async def test_cross_org_and_cross_case_ids_are_not_found(factory):
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        other_user = await session.get(User, OTHER_USER_ID)
        other_case = await session.get(ReviewCase, OTHER_CASE_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        # 组织不匹配时服务层再挡一道（端点已鉴权也不会误放行）
        with pytest.raises(NotFoundError):
            await svc.upload_submission(
                session, case=case, current_user=other_user, filename="x.docx", data=_docx()
            )
        with pytest.raises(NotFoundError):
            await svc.get_submission(session, case=other_case, submission_id=summary["id"])
        with pytest.raises(NotFoundError):
            await svc.get_ledger(session, case=other_case, submission_id=summary["id"])
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        with pytest.raises(NotFoundError):
            await svc.get_row(
                session, case=other_case, submission_id=summary["id"], row_id=row["id"]
            )
        with pytest.raises(NotFoundError):
            await svc.get_submission(session, case=case, submission_id=str(uuid.uuid4()))
        with pytest.raises(NotFoundError):
            await svc.update_disposition(
                session,
                case=case,
                current_user=user,
                submission_id=summary["id"],
                row_id=str(uuid.uuid4()),
                update=RedlineDispositionUpdate(disposition="reviewed", expected_lock_version=1),
            )


@pytest.mark.asyncio
async def test_evidence_binding_requires_same_case_item(factory):
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        with pytest.raises(Exception) as caught:
            await svc.update_disposition(
                session,
                case=case,
                current_user=user,
                submission_id=summary["id"],
                row_id=row["id"],
                update=RedlineDispositionUpdate(
                    disposition="reviewed",
                    expected_lock_version=1,
                    item_id=str(uuid.uuid4()),
                    evidence_id=EVIDENCE_ID,
                ),
            )
        assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_disposition_links_follow_submitted_update_and_null_clears(factory):
    await _seed(factory)
    second_item_id = "55555555-5555-4555-8555-555555555555"
    second_evidence_id = "66666666-6666-4666-8666-666666666666"
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        session.add(
            ReviewItem(
                id=second_item_id,
                case_id=CASE_ID,
                item_key="exception",
                ordinal=2,
                status="pending",
            )
        )
        await session.flush()
        session.add(
            ReviewEvidence(
                id=second_evidence_id,
                case_id=CASE_ID,
                item_id=second_item_id,
                side="revised",
                role="main",
                selected=True,
                document_id="d3",
                doc_role="contract",
                document_sha256="a" * 64,
                chunk_id_at_capture="chunk-2",
                chunk_index=1,
                quote_text="第二证据",
                quote_sha256="d" * 64,
                text_provenance="parser_original",
            )
        )
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        row_id = row["id"]

        bound = await svc.update_disposition(
            session,
            case=case,
            current_user=user,
            submission_id=summary["id"],
            row_id=row_id,
            update=RedlineDispositionUpdate(
                disposition="reviewed",
                expected_lock_version=1,
                item_id=ITEM_ID,
                evidence_id=EVIDENCE_ID,
            ),
        )
        assert (bound["item_id"], bound["evidence_id"]) == (ITEM_ID, EVIDENCE_ID)

        # 切到新 item + 新 evidence：验证按「本次提交」的 item 绑定，旧行 item 不作数。
        switched = await svc.update_disposition(
            session,
            case=case,
            current_user=user,
            submission_id=summary["id"],
            row_id=row_id,
            update=RedlineDispositionUpdate(
                disposition="reviewed",
                expected_lock_version=bound["lock_version"],
                item_id=second_item_id,
                evidence_id=second_evidence_id,
            ),
        )
        assert (switched["item_id"], switched["evidence_id"]) == (
            second_item_id,
            second_evidence_id,
        )

        cleared = await svc.update_disposition(
            session,
            case=case,
            current_user=user,
            submission_id=summary["id"],
            row_id=row_id,
            update=RedlineDispositionUpdate(
                disposition="reviewed", expected_lock_version=switched["lock_version"]
            ),
        )
        assert cleared["item_id"] is None and cleared["evidence_id"] is None

        # 不存在的 evidence UUID 必须 404，不能留下悬空 id。
        with pytest.raises(NotFoundError):
            await svc.update_disposition(
                session,
                case=case,
                current_user=user,
                submission_id=summary["id"],
                row_id=row_id,
                update=RedlineDispositionUpdate(
                    disposition="reviewed",
                    expected_lock_version=cleared["lock_version"],
                    item_id=ITEM_ID,
                    evidence_id=str(uuid.uuid4()),
                ),
            )


@pytest.mark.asyncio
async def test_disposition_uses_case_row_lock(factory, monkeypatch):
    await _seed(factory)
    calls: list[str] = []
    original = svc._lock_case

    async def tracking(session, case):
        calls.append(case.id)
        return await original(session, case)

    monkeypatch.setattr(svc, "_lock_case", tracking)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        await svc.update_disposition(
            session,
            case=case,
            current_user=user,
            submission_id=summary["id"],
            row_id=row["id"],
            update=RedlineDispositionUpdate(disposition="reviewed", expected_lock_version=1),
        )
    assert CASE_ID in calls  # upload + disposition 都加锁；归档检查在锁内


@pytest.mark.asyncio
async def test_ledger_exposes_parse_warnings_truncation_and_filtered_counts(factory, monkeypatch):
    await _seed(factory)
    # 把行数上限降到 1 触发 row_cap → partial + truncation 标记。
    monkeypatch.setattr(svc, "MAX_REDLINE_ROWS", 1)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        assert any("row_cap_exceeded" in w for w in summary["parse_warnings"])
        ledger = await svc.get_ledger(session, case=case, submission_id=summary["id"])
        assert ledger["parse_warnings"] == summary["parse_warnings"]
        assert ledger["has_truncation"] is True
        assert ledger["counts"] == {"revision": 1, "comment": 0, "unparsed": 0}

        # 过滤后 counts/has_unparsed 与 total 同口径。
        filtered = await svc.get_ledger(
            session,
            case=case,
            submission_id=summary["id"],
            revision_type="insertion",
        )
        assert filtered["counts"] == {"revision": 0, "comment": 0, "unparsed": 0}
        assert filtered["total"] == 0
        assert filtered["has_unparsed"] is False
        assert filtered["has_truncation"] is True  # 解析期事实，与过滤无关
