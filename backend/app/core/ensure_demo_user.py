"""应用启动时确保演示账号和默认组织存在。"""
import logging
import os

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.organization import Organization
from app.models.user import User
from app.services.auth_service import auth_service

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
    """若不存在则创建演示账号；已存在则不动密码（安全加固：防止每次启动把密码重置为已知弱口令）。"""
    try:
        async with AsyncSessionLocal() as db:
            org_id = await _ensure_default_org(db)

            result = await db.execute(select(User).where(User.username == DEMO_USERNAME))
            user = result.scalar_one_or_none()
            if user:
                # 安全加固：不重置密码。仅补齐组织归属（账号由管理员接管后密码保持不变）。
                if not user.organization_id:
                    user.organization_id = org_id
                    await db.commit()
                    logger.info(f"演示账号已补齐组织归属: {DEMO_USERNAME}")
                else:
                    logger.info(f"演示账号已存在: {DEMO_USERNAME}（不重置密码）")
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
                logger.info(f"演示账号已创建: {DEMO_USERNAME}（请通过环境变量 DEMO_PASSWORD 配置初始密码）")
    except Exception as e:
        logger.exception(f"确保演示账号失败（不影响启动）: {e}")
