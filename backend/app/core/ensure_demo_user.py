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
DEMO_EMAIL = "guest@example.com"


def _demo_password() -> str:
    """演示账号初始密码：优先环境变量，否则随机生成（绝不使用已知弱口令）。"""
    configured = os.environ.get("DEMO_PASSWORD")
    if configured and configured != "123456":
        return configured
    import secrets
    return secrets.token_urlsafe(12)


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
                # 安全加固：初始密码随机生成（或由环境变量指定），仅创建时打印一次；
                # 管理员可从日志获取并修改。
                initial_password = _demo_password()
                await auth_service.create_user(
                    db,
                    username=DEMO_USERNAME,
                    email=DEMO_EMAIL,
                    password=initial_password,
                    full_name="游客",
                    organization_id=org_id,
                    role="user",
                )
                await db.commit()
                logger.warning(
                    f"演示账号已创建: {DEMO_USERNAME}，初始密码为: {initial_password}"
                    "（仅此一次可见，请立即修改或设置 DEMO_PASSWORD 环境变量）"
                )
    except Exception as e:
        logger.exception(f"确保演示账号失败（不影响启动）: {e}")
