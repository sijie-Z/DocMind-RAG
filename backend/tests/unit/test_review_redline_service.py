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
OTHER_CASE_ID_IN_ORG = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ITEM_ID = "33333333-3333-4333-8333-333333333333"
EVIDENCE_ID = "44444444-4444-4444-8444-444444444444"
SECRET_CLAUSE = "第9条 绝对保密条款不得外泄"
SECRET_AUTHOR = "对方首席律师张伟"
OTHER_ITEM_ID = "88888888-8888-4888-8888-888888888888"
OTHER_EVIDENCE_ID = "99999999-9999-4999-8999-999999999999"


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
        for case_id, org_id in (
            (CASE_ID, ORG_ID),
            (OTHER_CASE_ID_IN_ORG, ORG_ID),  # 同组织、不同案件
            (OTHER_CASE_ID, OTHER_ORG_ID),
        ):
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
        # 与 CASE_ID 同组织、但属于另一案件的 item/evidence：用于证明绑定校验
        # 必须按 case_id 而不只是按 organization_id；随机 UUID 测不出这个差别。
        session.add(
            ReviewItem(
                id=OTHER_ITEM_ID,
                case_id=OTHER_CASE_ID_IN_ORG,
                item_key="other_case_payment_term",
                ordinal=1,
                status="pending",
            )
        )
        session.add(
            ReviewEvidence(
                id=OTHER_EVIDENCE_ID,
                case_id=OTHER_CASE_ID_IN_ORG,
                item_id=OTHER_ITEM_ID,
                side="revised",
                role="main",
                selected=True,
                document_id="d3",
                doc_role="contract",
                document_sha256="e" * 64,
                chunk_id_at_capture="chunk-other-case",
                chunk_index=0,
                quote_text="另一案件的保密约定",
                quote_sha256="f" * 64,
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
        # M2：每次上传都在同事务写一条审计，仅含 id/哈希前缀，不含条款正文/作者。
        logs = (await session.execute(select(UserActivityLog))).scalars().all()
        assert len(logs) == 2
        assert {log.action for log in logs} == {"review_redline_upload"}
        assert {json.loads(log.detail)["submission_id"] for log in logs} == {
            first["id"],
            second["id"],
        }
        for log in logs:
            detail = json.loads(log.detail)
            assert set(detail) == {
                "organization_id",
                "user_id",
                "case_id",
                "submission_id",
                "sequence_no",
                "file_sha256_prefix",
                "file_size",
                "status",
            }
            assert detail["file_sha256_prefix"] == first["file_sha256"][:16]
            assert SECRET_CLAUSE not in log.detail
            assert SECRET_AUTHOR not in log.detail


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
async def test_storage_failure_keeps_referenced_row_and_never_orphans(factory, monkeypatch):
    """无删除接口：DB 先提交并引用确定对象名，存储失败也没有孤儿对象。"""

    def fail(*args, **kwargs):
        raise RuntimeError("minio offline")

    monkeypatch.setattr(svc.minio_client, "put_object", fail)
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        with pytest.raises(ServiceUnavailableError) as caught:
            await svc.upload_submission(
                session, case=case, current_user=user, filename="x.docx", data=_docx()
            )
        assert caught.value.error_code == "REVIEW_REDLINE_STORAGE_UNAVAILABLE"

    async with factory() as session:
        submissions = (await session.execute(select(ReviewRedlineSubmission))).scalars().all()
        assert len(submissions) == 1
        # 行先提交并已引用对象名：对象若后来被补传，绝非无引用孤儿。
        assert submissions[0].storage_object_name.startswith(f"redlines/{ORG_ID}/{CASE_ID}/")
        assert (await session.execute(select(func.count(ReviewRedlineRow.id)))).scalar() == 2
        assert (await session.execute(select(func.count(UserActivityLog.id)))).scalar() == 1


@pytest.mark.asyncio
async def test_integrity_error_does_not_upload_object(factory, monkeypatch, no_network):
    """M1：强制 DB 提交失败（唯一约束冲突）时，绝不产生 MinIO put_object。"""
    await _seed(factory)
    collision_id = "77777777-7777-4777-8777-777777777777"
    async with factory() as session:
        # 先占住上传将要生成的 submission id，令 INSERT 命中主键唯一约束。
        session.add(
            ReviewRedlineSubmission(
                id=collision_id,
                organization_id=ORG_ID,
                case_id=CASE_ID,
                sequence_no=1,
                filename="occupied.docx",
                file_sha256="f" * 64,
                file_size=1,
                storage_object_name="redlines/occupied.docx",
                parser_version=svc.parser_version,
                status="parsed",
                revision_count=0,
                comment_count=0,
                unparsed_count=0,
                parse_warnings=[],
                uploaded_by=USER_ID,
            )
        )
        await session.commit()

    monkeypatch.setattr(svc.uuid, "uuid4", lambda: uuid.UUID(collision_id))
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        with pytest.raises(ConflictError) as caught:
            await svc.upload_submission(
                session, case=case, current_user=user, filename="x.docx", data=_docx()
            )
        assert caught.value.error_code == "REVIEW_REDLINE_CONFLICT"
    assert not no_network["calls"]
    async with factory() as session:
        assert (await session.execute(select(func.count(UserActivityLog.id)))).scalar() == 0


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
        assert len(logs) == 2  # 1 条上传审计 + 1 条处置审计
        dispositions = [log for log in logs if log.action == "review_redline_disposition"]
        assert len(dispositions) == 1
        log = dispositions[0]
        assert log.target_type == "review_case"
        assert log.target_id == CASE_ID
        detail = json.loads(log.detail)
        assert set(detail) == {
            "organization_id",
            "user_id",
            "case_id",
            "submission_id",
            "row_id",
            "disposition",
            "lock_version",
        }
        assert SECRET_CLAUSE not in log.detail
        assert SECRET_AUTHOR not in log.detail


@pytest.mark.asyncio
async def test_stale_identity_map_session_cannot_overwrite_concurrent_update(factory):
    """H1：T1 先读行进身份映射；T2 提交后，T1 持旧版本号必须 409 而非覆盖。

    expire_on_commit=False 下 SELECT FOR UPDATE 会命中身份映射里的旧实例，
    旧实现因此绕过乐观锁；条件 UPDATE 在数据库层比对 lock_version 才能拦住。
    """
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row_id = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][
            0
        ]["id"]

    async with factory() as t1:
        case1 = await t1.get(ReviewCase, CASE_ID)
        user1 = await t1.get(User, USER_ID)
        stale_row = await svc.get_row(t1, case=case1, submission_id=summary["id"], row_id=row_id)
        assert stale_row.lock_version == 1
        await t1.commit()  # 释放读事务，但身份映射中的旧实例仍在（expire_on_commit=False）

        async with factory() as t2:
            case2 = await t2.get(ReviewCase, CASE_ID)
            user2 = await t2.get(User, USER_ID)
            winner = await svc.update_disposition(
                t2,
                case=case2,
                current_user=user2,
                submission_id=summary["id"],
                row_id=row_id,
                update=RedlineDispositionUpdate(
                    disposition="reviewed", note="T2 已核对", expected_lock_version=1
                ),
            )
            assert winner["lock_version"] == 2

        with pytest.raises(ConflictError) as caught:
            await svc.update_disposition(
                t1,
                case=case1,
                current_user=user1,
                submission_id=summary["id"],
                row_id=row_id,
                update=RedlineDispositionUpdate(
                    disposition="not_relevant", note="T1 覆盖", expected_lock_version=1
                ),
            )
        assert caught.value.error_code == "REVIEW_REDLINE_CONFLICT"

    async with factory() as session:
        saved = await session.get(ReviewRedlineRow, row_id)
        assert saved.disposition == "reviewed"
        assert saved.note == "T2 已核对"
        assert saved.lock_version == 2


@pytest.mark.asyncio
async def test_superuser_can_write_cross_org_redline(factory, no_network):
    """M7：超管与 review_case_service 行为一致，不受组织相等性限制。

    写入归属必须来自案件（ORG_ID），不能来自调用方的 primary org（OTHER_ORG_ID）。
    行/批次组织若误用调用方 org，下面的显式断言会失败。
    """
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)  # ORG_ID=11
        superuser = await session.get(User, OTHER_USER_ID)  # primary org=12
        superuser.is_superuser = True
        await session.commit()
        assert superuser.organization_id == OTHER_ORG_ID != case.organization_id
        summary = await svc.upload_submission(
            session, case=case, current_user=superuser, filename="x.docx", data=_docx()
        )
        assert summary["case_id"] == CASE_ID
        async with factory() as verify:
            saved_submission = await verify.get(ReviewRedlineSubmission, summary["id"])
            assert saved_submission.organization_id == ORG_ID
            saved_rows = (await verify.execute(select(ReviewRedlineRow))).scalars().all()
            assert saved_rows and all(
                row.organization_id == ORG_ID and row.case_id == CASE_ID for row in saved_rows
            )
            assert all(row.organization_id != superuser.organization_id for row in saved_rows)
        assert no_network["calls"][0]["kwargs"]["object_name"].startswith(
            f"redlines/{ORG_ID}/{CASE_ID}/"
        )
        row_id = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][
            0
        ]["id"]
        updated = await svc.update_disposition(
            session,
            case=case,
            current_user=superuser,
            submission_id=summary["id"],
            row_id=row_id,
            update=RedlineDispositionUpdate(disposition="reviewed", expected_lock_version=1),
        )
        assert updated["disposition"] == "reviewed"
        assert updated["lock_version"] == 2


@pytest.mark.asyncio
async def test_no_org_superuser_upload_uses_case_organization_for_submission_and_rows(factory):
    """无 primary org 的超管上传时，批次与每行的 organization_id 必须取案件组织。

    若实现误用 current_user.organization_id（None）或回退到调用方组织，
    此测试的显式断言会失败；MinIO 对象名也必须带案件组织前缀。
    """
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        caller = await session.get(User, OTHER_USER_ID)
        caller.is_superuser = True
        caller.organization_id = None  # 无 primary org，且属于另一组织的用户
        await session.commit()

        summary = await svc.upload_submission(
            session, case=case, current_user=caller, filename="tenant.docx", data=_docx()
        )
        assert summary["case_id"] == CASE_ID
        assert summary["uploaded_by"] == OTHER_USER_ID

    async with factory() as verify:
        saved_submission = await verify.get(ReviewRedlineSubmission, summary["id"])
        assert saved_submission.organization_id == case.organization_id == ORG_ID
        rows = (
            (
                await verify.execute(
                    select(ReviewRedlineRow).where(ReviewRedlineRow.submission_id == summary["id"])
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        assert all(row.organization_id == ORG_ID for row in rows)
        assert all(row.case_id == CASE_ID for row in rows)
        assert all(row.organization_id is not None for row in rows)
        assert saved_submission.storage_object_name.startswith(f"redlines/{ORG_ID}/{CASE_ID}/")
        logs = (await verify.execute(select(UserActivityLog))).scalars().all()
        assert len(logs) == 1
        assert json.loads(logs[0].detail)["organization_id"] == ORG_ID


def _column_snapshot(instance) -> dict[str, str]:
    """按列名逐项稳定序列化，用于「字段字节级未变」断言。"""
    return {
        column.name: repr(getattr(instance, column.name)) for column in instance.__table__.columns
    }


@pytest.mark.asyncio
async def test_no_mutation_of_case_item_evidence_export_or_templates(factory):
    await _seed(factory)
    before_case = await _load(factory, ReviewCase, CASE_ID)
    before_item = await _load(factory, ReviewItem, ITEM_ID)
    before_item_snapshot = _column_snapshot(before_item)
    assert before_item_snapshot["status"] == repr("pending")
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
        updated = await svc.update_disposition(
            session,
            case=case,
            current_user=user,
            submission_id=summary["id"],
            row_id=row["id"],
            # 必须真正 link item + evidence：否则本测试对「关联时误改 ReviewItem」
            # 的实现完全不敏感。
            update=RedlineDispositionUpdate(
                disposition="reviewed",
                note="人工核对过",
                expected_lock_version=1,
                item_id=ITEM_ID,
                evidence_id=EVIDENCE_ID,
            ),
        )
        assert (updated["item_id"], updated["evidence_id"]) == (ITEM_ID, EVIDENCE_ID)
        assert updated["note"] == "人工核对过" and updated["lock_version"] == 2

    after_case = await _load(factory, ReviewCase, CASE_ID)
    after_item = await _load(factory, ReviewItem, ITEM_ID)
    after_item_snapshot = _column_snapshot(after_item)
    after_evidence = await _load(factory, ReviewEvidence, EVIDENCE_ID)
    # 行本身确实发生了写入：note/lock_version 从种子状态变化，防止测试因
    # update_disposition 为空操作而假绿。ReviewItem 无 note/lock_version 列，
    # 它的等价字段是 decision_note/status/decided_by，全部纳入上面的快照。
    assert after_item_snapshot == before_item_snapshot
    assert after_item_snapshot["status"] == repr(item_status)
    assert after_item_snapshot["decided_by"] == repr(None)
    assert after_item_snapshot["decision_note"] == repr(None)
    assert after_case.lock_version == case_lock
    assert after_case.status == "in_review"
    assert after_case.title == before_case.title
    assert after_item.status == item_status
    assert after_item.decided_by is None
    assert after_evidence.selected == evidence_selected
    async with factory() as session:
        saved_row = await session.get(ReviewRedlineRow, updated["id"])
        assert saved_row.note == "人工核对过"
        assert saved_row.lock_version == 2
        assert (saved_row.item_id, saved_row.evidence_id) == (ITEM_ID, EVIDENCE_ID)
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
async def test_link_item_from_another_case_in_same_org_is_not_found_and_does_not_mutate(factory):
    """跨案件 item（同组织）不得被链接：随机 UUID 测不出 case_id 校验。"""
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        other_case = await session.get(ReviewCase, OTHER_CASE_ID_IN_ORG)
        other_item = await session.get(ReviewItem, OTHER_ITEM_ID)
        assert other_case.organization_id == case.organization_id == ORG_ID
        assert other_item.case_id == other_case.id != case.id

        with pytest.raises(NotFoundError) as caught:
            await svc.update_disposition(
                session,
                case=case,
                current_user=user,
                submission_id=summary["id"],
                row_id=row["id"],
                update=RedlineDispositionUpdate(
                    disposition="reviewed",
                    expected_lock_version=1,
                    item_id=OTHER_ITEM_ID,
                ),
            )
        assert caught.value.status_code == 404

    async with factory() as verify:
        saved = await verify.get(ReviewRedlineRow, row["id"])
        assert saved.disposition == "pending"
        assert saved.lock_version == 1
        assert saved.item_id is None and saved.evidence_id is None
        assert saved.note is None and saved.disposition_by is None and saved.disposition_at is None
        logs = (await verify.execute(select(UserActivityLog))).scalars().all()
        assert len(logs) == 1  # 仅上传审计，未写处置审计
        assert {log.action for log in logs} == {"review_redline_upload"}


@pytest.mark.asyncio
async def test_link_evidence_and_item_from_another_case_in_same_org_is_not_found(factory):
    """跨案件 evidence+item（同组织、二者互相匹配）不得被链接。

    仅校验 evidence 存在或 item 匹配而漏掉 case_id 时，本测试会失败。
    """
    await _seed(factory)
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="x.docx", data=_docx()
        )
        row = (await svc.get_ledger(session, case=case, submission_id=summary["id"]))["items"][0]
        other_evidence = await session.get(ReviewEvidence, OTHER_EVIDENCE_ID)
        assert other_evidence.case_id == OTHER_CASE_ID_IN_ORG != case.id
        assert other_evidence.item_id == OTHER_ITEM_ID  # 绑定关系本身是合法的，只是案子不对

        with pytest.raises(NotFoundError) as caught:
            await svc.update_disposition(
                session,
                case=case,
                current_user=user,
                submission_id=summary["id"],
                row_id=row["id"],
                update=RedlineDispositionUpdate(
                    disposition="reviewed",
                    expected_lock_version=1,
                    item_id=OTHER_ITEM_ID,
                    evidence_id=OTHER_EVIDENCE_ID,
                ),
            )
        assert caught.value.status_code == 404

    async with factory() as verify:
        saved = await verify.get(ReviewRedlineRow, row["id"])
        assert saved.disposition == "pending"
        assert saved.lock_version == 1
        assert saved.item_id is None and saved.evidence_id is None
        assert saved.note is None and saved.disposition_by is None and saved.disposition_at is None
        logs = (await verify.execute(select(UserActivityLog))).scalars().all()
        assert len(logs) == 1
        assert {log.action for log in logs} == {"review_redline_upload"}


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
async def test_depth_cap_warning_sets_has_truncation(factory, monkeypatch):
    """xml_depth_cap 也是截断告警，has_truncation 不得漏报。"""
    from app.models.review import ReviewCase
    from app.models.user import User

    await _seed(factory)
    monkeypatch.setattr(svc, "MAX_OOXML_XML_DEPTH", 2)
    deep = "<w:p>" + "<w:p>" * 5 + "<w:ins><w:r><w:t>深</w:t></w:r></w:ins>" + "</w:p>" * 6
    async with factory() as session:
        case = await session.get(ReviewCase, CASE_ID)
        user = await session.get(User, USER_ID)
        summary = await svc.upload_submission(
            session, case=case, current_user=user, filename="deep.docx", data=_docx(text=deep)
        )
        assert any(w.startswith("xml_depth_cap_exceeded") for w in summary["parse_warnings"])
        ledger = await svc.get_ledger(session, case=case, submission_id=summary["id"])
        assert ledger["has_truncation"] is True
        assert ledger["parse_warnings"] == summary["parse_warnings"]


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
