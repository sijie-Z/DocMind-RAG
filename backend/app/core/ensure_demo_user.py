# -*- coding: utf-8 -*-
"""应用启动时确保演示账号和默认组织存在。"""
import os
import logging
from app.core.database import AsyncSessionLocal
from app.services.auth_service import auth_service
from app.models.user import User
from app.models.organization import Organization
from sqlalchemy import select

logger = logging.getLogger(__name__)

DEMO_USERNAME = "guest"
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "123456")
DEMO_EMAIL = "guest@example.com"

if DEMO_PASSWORD == "123456":
    logger.warning("DEMO_PASSWORD is using default value '123456'. Set DEMO_PASSWORD environment variable for production.")


async def _ensure_default_org(db) -> int:
    """确保默认组织存在，返回其 ID"""
    result = await db.execute(select(Organization).where(Organization.name == "Default"))
    org = result.scalar_one_or_none()
    if org:
        return org.id

    org = Organization(
        name="Default",
        description="默认组织",
        color="#64748b",
        is_private=False,
        parent_id=None,
        level=0,
        sort_order=0,
        owner_id=1,
    )
    db.add(org)
    await db.flush()
    logger.info(f"默认组织已创建: Default (id={org.id})")
    return org.id


async def ensure_demo_user():
    """若不存在则创建 guest/123456，存在则确保密码为 123456 且已激活。"""
    try:
        async with AsyncSessionLocal() as db:
            org_id = await _ensure_default_org(db)

            result = await db.execute(select(User).where(User.username == DEMO_USERNAME))
            user = result.scalar_one_or_none()
            if user:
                hashed = auth_service.hash_password(DEMO_PASSWORD)
                setattr(user, "hashed_password", hashed)
                setattr(user, "is_active", True)
                setattr(user, "role", "user")
                if not user.organization_id:
                    setattr(user, "organization_id", org_id)
                await db.commit()
                logger.info(f"演示账号已更新: {DEMO_USERNAME} / {DEMO_PASSWORD}")
            else:
                await auth_service.create_user(
                    db,
                    username=DEMO_USERNAME,
                    email=DEMO_EMAIL,
                    password=DEMO_PASSWORD,
                    full_name="游客",
                    organization_id=org_id,
                    role="user",
                )
                await db.commit()
                logger.info(f"演示账号已创建: {DEMO_USERNAME} / {DEMO_PASSWORD}")
    except Exception as e:
        logger.exception(f"确保演示账号失败（不影响启动）: {e}")
        