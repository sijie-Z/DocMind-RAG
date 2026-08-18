import asyncio
import os

from sqlalchemy import update

from app.core.database import engine
from app.models.user import User
from app.services.auth_service import auth_service


async def main():
    # 安全加固：不再硬编码弱口令 123456，密码由 ADMIN_PASSWORD 环境变量指定
    new_password = os.environ.get("ADMIN_PASSWORD", "").strip()
    if not new_password or len(new_password) < 8:
        print("【失败】请先设置环境变量 ADMIN_PASSWORD（至少 8 位），例如：")
        print('  $env:ADMIN_PASSWORD="your-strong-password" ; python reset_admin.py')
        raise SystemExit(1)

    print("正在连接数据库并修复管理员账号...")
    async with engine.begin() as conn:
        # 1. 检查 admin 是否存在
        hashed = auth_service.hash_password(new_password)
        
        # 2. 尝试更新
        result = await conn.execute(
            update(User)
            .where(User.username == "admin")
            .values(hashed_password=hashed, is_active=True, role="admin")
        )
        
        if result.rowcount == 0:
            print("未找到 admin 用户，正在重新创建...")
            from datetime import datetime

            # 如果不存在，直接插入一个新的
            from sqlalchemy import insert
            await conn.execute(
                insert(User).values(
                    username="admin",
                    email="admin@example.com",
                    hashed_password=hashed,
                    role="admin",
                    is_active=True,
                    is_superuser=True,
                    created_at=datetime.utcnow(),
                    updated_at=datetime.utcnow()
                )
            )
        
        print("\n" + "="*30)
        print("【成功】管理员账号已重置！")
        print("用户名: admin")
        print("密  码: " + new_password)
        print("="*30)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"【失败】修复过程中出现错误: {str(e)}")
