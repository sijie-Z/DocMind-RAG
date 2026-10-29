"""MySQL REPEATABLE READ 下案件行锁的可读性（Batch 4 并发回归）。

背景：请求内先前的普通 SELECT 会建立 read view，直接 `SELECT ... FOR UPDATE`
后接普通 SELECT 仍可能读到旧快照。`_lock_case` 先结束只读事务再加锁，本用例
用两个真实会话钉住「加锁后能读到锁内最新已提交值」这一不变量。

依赖真实 MySQL（CI Integration job 设置 RUN_INTEGRATION=1）；SQLite 忽略
FOR UPDATE，无法验证该语义。

注意：本用例**自建引擎**而不是复用全局 `AsyncSessionLocal`——pytest-asyncio
为每个用例新建事件循环，全局连接池里指向旧循环的连接会抛
「Future attached to a different loop」。
"""

import os
import uuid

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base
from app.models.organization import Organization
from app.models.review import ReviewCase
from app.models.user import User
from app.services.review_export_service import _lock_case

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1）",
)


@pytest.mark.asyncio
async def test_lock_case_sees_latest_commit_under_repeatable_read():
    if "mysql" not in settings.DATABASE_URL:
        pytest.skip("需要 MySQL（SQLite 忽略 FOR UPDATE）")

    engine = create_async_engine(settings.DATABASE_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:10]
    case_id = str(uuid.uuid4())
    user_id = org_id = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with maker() as db:
            user = User(
                username=f"lock_probe_{suffix}",
                email=f"lock_probe_{suffix}@example.com",
                hashed_password="not-a-real-hash",
                role="admin",
                is_active=True,
            )
            db.add(user)
            await db.flush()
            org = Organization(name=f"lock-probe-{suffix}", owner_id=user.id)
            db.add(org)
            await db.flush()
            db.add(
                ReviewCase(
                    id=case_id,
                    organization_id=org.id,
                    title="lock probe",
                    policy_document_id="p",
                    base_document_id="b",
                    revised_document_id="r",
                    checklist_version="v1",
                    status="in_review",
                    created_by=user.id,
                    lock_version=1,
                )
            )
            await db.commit()
            user_id, org_id = user.id, org.id

        async with maker() as reader:
            loaded = await reader.get(ReviewCase, case_id)  # 先建立 read view
            assert loaded is not None and loaded.status == "in_review"

            async with maker() as writer:
                row = await writer.get(ReviewCase, case_id)
                row.status = "completed"
                row.lock_version = 2
                await writer.commit()

            locked = await _lock_case(reader, loaded)
            assert locked.status == "completed", "加锁后必须看到最新已提交状态"
            assert locked.lock_version == 2
            await reader.commit()
    finally:
        async with maker() as db:
            await db.execute(delete(ReviewCase).where(ReviewCase.id == case_id))
            if org_id is not None:
                await db.execute(delete(Organization).where(Organization.id == org_id))
            if user_id is not None:
                await db.execute(delete(User).where(User.id == user_id))
            await db.commit()
        await engine.dispose()
