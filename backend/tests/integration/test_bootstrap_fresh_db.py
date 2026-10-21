"""空库自举（issue #233）—— 真实 MySQL 上钉住「全新环境首启可用」。

用独立命名的临时数据库跑真实 create_all + `ensure_demo_user`，断言用户/组织
真的落库且互相关联。用后即删，绝不碰测试库。

修复前的行为（本用例必红）：先建 `owner_id=1` 的组织 → 空库外键 IntegrityError
（1452）→ 异常被吞 → users/organizations 两表为 0。
"""

import os
import socket
import uuid
from urllib.parse import urlparse

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1，并确保可达）",
)


def _mysql_reachable() -> bool:
    raw = settings.DATABASE_URL.split("://", 1)[-1]  # 去掉 mysql+driver 前缀再解析
    u = urlparse("//" + raw)
    try:
        with socket.create_connection((u.hostname, u.port or 3306), timeout=2):
            return True
    except OSError:
        return False


if not _mysql_reachable():
    pytestmark = [
        pytestmark,
        pytest.mark.skip(reason="MySQL 不可达"),
    ]


@pytest.mark.asyncio
async def test_fresh_database_bootstrap_succeeds(monkeypatch):
    import app.core.ensure_demo_user as edu
    import app.models  # noqa: F401  # 注册全部模型
    from app.core.database import Base
    from app.models.organization import Organization
    from app.models.user import User

    base_url = settings.DATABASE_URL
    admin_url = base_url  # 借现有库的连接执行 CREATE/DROP DATABASE
    tmp_db = f"docmind_bootstrap_{uuid.uuid4().hex[:8]}"
    tmp_url = base_url.rsplit("/", 1)[0] + "/" + tmp_db

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    engine = None
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"CREATE DATABASE {tmp_db} CHARACTER SET utf8mb4"))

        engine = create_async_engine(tmp_url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # 把种子函数指向临时库
        Session = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(edu, "AsyncSessionLocal", Session)

        await edu.ensure_demo_user()

        async with Session() as s:
            user = (
                await s.execute(select(User).where(User.username == "guest"))
            ).scalar_one_or_none()
            org = (
                await s.execute(select(Organization).where(Organization.name == "Default"))
            ).scalar_one_or_none()

        assert user is not None, "空库自举应创建演示账号（issue #233）"
        assert org is not None, "空库自举应创建默认组织（issue #233）"
        assert user.organization_id == org.id, "用户应归属到默认组织"
        assert org.owner_id == user.id, "组织的 owner 应是已存在的用户（而非硬编码 1）"
    finally:
        if engine is not None:
            await engine.dispose()
        async with admin_engine.connect() as conn:
            await conn.execute(text(f"DROP DATABASE IF EXISTS {tmp_db}"))
        await admin_engine.dispose()
