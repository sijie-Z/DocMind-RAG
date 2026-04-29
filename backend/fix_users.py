# backend/fix_users.py
import asyncio
from typing import cast
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.user import User
from app.models.organization import Organization
# Role 虽然导入但未使用，如果不需要可以移除
# from app.models.rbac import Role 
from app.services.auth_service import AuthService

async def fix():
    auth_service = AuthService()
    async with AsyncSessionLocal() as db:
        print("开始修复账号权限...")

        # 1. 确保有一个默认组织
        result = await db.execute(select(Organization).where(Organization.name == "总公司"))
        org = result.scalar_one_or_none()
        if not org:
            org = Organization(name="总公司", description="系统默认组织", owner_id=1)
            db.add(org)
            await db.flush()
            print("已创建默认组织：总公司")
        
        # 此时 org 已经确定不是 None，进行显式类型转换辅助检查器
        org = cast(Organization, org)

        # 2. 修复 admin 账号
        result = await db.execute(select(User).where(User.username == "admin"))
        admin = result.scalar_one_or_none()
        if admin:
            # 使用 # type: ignore 解决 SQLAlchemy Column 与 Literal 的赋值冲突
            admin.is_superuser = True  # type: ignore
            admin.role = "admin"       # type: ignore
            admin.organization_id = org.id # type: ignore
            print("已将 admin 设为超级管理员并关联组织")

        # 3. 创建 guest 账号
        result = await db.execute(select(User).where(User.username == "guest"))
        guest = result.scalar_one_or_none()
        if not guest:
            hashed_pwd = auth_service.hash_password("guest123")
            guest = User(
                username="guest",
                email="guest@example.com",
                hashed_password=hashed_pwd,
                full_name="演示访客",
                role="user",           
                is_superuser=False,    
                organization_id=org.id 
            )
            db.add(guest)
            print("已创建演示账号：guest / guest123")
        
        await db.commit()
        print("所有更改已提交数据库！")

if __name__ == "__main__":
    asyncio.run(fix())
    