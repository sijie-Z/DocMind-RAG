# -*- coding: utf-8 -*-
"""
本地诊断：不启动 FastAPI，直接测登录相关逻辑，用于排查 500。
在 1_demo/backend 目录下运行: python scripts/check_login.py
"""
import asyncio
import sys
import os

# 确保 backend 在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    print("1. 检查数据库连接...")
    try:
        from app.core.database import AsyncSessionLocal
        from sqlalchemy import text
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        print("   数据库连接 OK")
    except Exception as e:
        print(f"   数据库失败: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return

    print("2. 认证用户 (admin / 123456)...")
    try:
        from app.core.database import AsyncSessionLocal
        from app.services.auth_service import auth_service
        async with AsyncSessionLocal() as db:
            user = await auth_service.authenticate_user(db, "admin", "123456")
        if user:
            print("   认证 OK, user_id:", user.id)
        else:
            print("   认证返回 None（用户不存在或密码错误）")
    except Exception as e:
        print(f"   认证异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return

    print("3. 生成 JWT...")
    try:
        from app.services.auth_service import auth_service
        token = auth_service.create_access_token(data={"sub": "admin", "user_id": 1, "role": "user"})
        print("   JWT OK, 长度:", len(token))
    except Exception as e:
        print(f"   JWT 异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return

    print("全部检查通过。若浏览器仍 500，请看后端终端或 auth_debug.log 的堆栈。")


if __name__ == "__main__":
    asyncio.run(main())
