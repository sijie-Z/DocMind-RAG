"""对方 DOCX 修订台账的组织删除守卫（真实 SQLite + service 直调）。

判别点：
- 台账头存在时 delete_organization_thoroughly 必须 409 且点名「对方修订台账」；
- 台账行存在时必须额外点名「对方修订台账行」；
- 两种情况组织都必须仍在库里，不允许裸删到 MySQL 1451。

从 organization_service._ATTACHED_DATA_MODELS 删除任一 redline 项，对应用例即红。
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.exceptions import ConflictError
from app.models.organization import Organization
from app.models.review import (
    REVIEW_REDLINE_TEXT_PROVENANCE,
    ReviewCase,
    ReviewRedlineRow,
    ReviewRedlineSubmission,
)
from app.models.user import User
from app.services.organization_service import organization_service

ORG_ID = 71
USER_ID = 72
CASE_ID = "71717171-7171-4171-8171-717171717171"
SUBMISSION_ID = "72727272-7272-4272-8272-727272727272"
ROW_ID = "73737373-7373-4373-8373-737373737373"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                User(
                    id=USER_ID,
                    username="redline-org",
                    email="redline-org@example.test",
                    hashed_password="x",
                ),
                Organization(id=ORG_ID, name="红线守卫组织", owner_id=USER_ID),
                ReviewCase(
                    id=CASE_ID,
                    organization_id=ORG_ID,
                    title="红线守卫案件",
                    policy_document_id="policy-placeholder",
                    base_document_id="base-placeholder",
                    revised_document_id="revised-placeholder",
                    status="in_review",
                    created_by=USER_ID,
                    lock_version=1,
                ),
            ]
        )
        await db.commit()
    yield maker
    await engine.dispose()


async def _add_submission(factory) -> None:
    async with factory() as db:
        db.add(
            ReviewRedlineSubmission(
                id=SUBMISSION_ID,
                organization_id=ORG_ID,
                case_id=CASE_ID,
                sequence_no=1,
                filename="counterparty.docx",
                file_sha256="a" * 64,
                file_size=128,
                storage_object_name="redlines/counterparty.docx",
                parser_version="1.0",
                status="parsed",
                revision_count=1,
                comment_count=0,
                unparsed_count=0,
                parse_warnings=[],
                uploaded_by=USER_ID,
            )
        )
        await db.commit()


async def _assert_org_survives(factory) -> None:
    async with factory() as db:
        assert await db.get(Organization, ORG_ID) is not None


@pytest.mark.asyncio
async def test_org_delete_blocked_by_redline_submission(factory):
    """存在台账头就必须 409 并点名「对方修订台账 1 条」。"""
    await _add_submission(factory)
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await organization_service.delete_organization_thoroughly(db, ORG_ID)
    assert "对方修订台账 1 条" in str(excinfo.value)
    await _assert_org_survives(factory)


@pytest.mark.asyncio
async def test_org_delete_blocked_by_redline_row(factory):
    """存在台账行就必须额外点名「对方修订台账行 1 条」。"""
    await _add_submission(factory)
    async with factory() as db:
        db.add(
            ReviewRedlineRow(
                id=ROW_ID,
                submission_id=SUBMISSION_ID,
                case_id=CASE_ID,
                organization_id=ORG_ID,
                ordinal=1,
                revision_type="insertion",
                after_text="60天",
                after_sha256="b" * 64,
                location={"paragraph": 1},
                text_provenance=REVIEW_REDLINE_TEXT_PROVENANCE,
                disposition="pending",
                lock_version=1,
            )
        )
        await db.commit()
    async with factory() as db:
        with pytest.raises(ConflictError) as excinfo:
            await organization_service.delete_organization_thoroughly(db, ORG_ID)
    message = str(excinfo.value)
    assert "对方修订台账 1 条" in message
    assert "对方修订台账行 1 条" in message
    await _assert_org_survives(factory)
