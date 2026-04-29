# -*- coding: utf-8 -*-
"""应用启动时确保演示账号存在，避免 401。"""
import logging
from app.core.database import AsyncSessionLocal
from app.services.auth_service import auth_service
from app.models.user import User
from sqlalchemy import select

logger = logging.getLogger(__name__)

DEMO_USERNAME = "guest"
DEMO_PASSWORD = "123456"
DEMO_EMAIL = "guest@example.com"


async def ensure_demo_user():
    """若不存在则创建 guest/123456，存在则确保密码为 123456 且已激活。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(User).where(User.username == DEMO_USERNAME))
            user = result.scalar_one_or_none()
            if user:
                hashed = auth_service.hash_password(DEMO_PASSWORD)
                # 使用 setattr 避免基于 SQLAlchemy 模型定义时的 pyright 类型报错
                setattr(user, "hashed_password", hashed)
                setattr(user, "is_active", True)
                setattr(user, "role", "user")
                await db.commit()
                logger.info(f"演示账号已更新: {DEMO_USERNAME} / {DEMO_PASSWORD}")
            else:
                await auth_service.create_user(
                    db,
                    username=DEMO_USERNAME,
                    email=DEMO_EMAIL,
                    password=DEMO_PASSWORD,
                    full_name="游客",
                    organization_id=None,
                    role="user",
                )
                await db.commit()
                logger.info(f"演示账号已创建: {DEMO_USERNAME} / {DEMO_PASSWORD}")
    except Exception as e:
        logger.exception(f"确保演示账号失败（不影响启动）: {e}")
        