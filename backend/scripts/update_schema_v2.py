import sys
import os

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from app.core.database import sync_engine
from app.models.user import User
from app.models.manual import SystemManual

def update_schema():
    print("开始更新数据库结构...")
    
    # 1. 为 users 表添加 api_key 字段
    try:
        with sync_engine.connect() as conn:
            # 检查字段是否存在
            result = conn.execute(text("SHOW COLUMNS FROM users LIKE 'api_key'"))
            if not result.fetchone():
                print("正在添加 api_key 字段到 users 表...")
                conn.execute(text("ALTER TABLE users ADD COLUMN api_key VARCHAR(100) UNIQUE DEFAULT NULL"))
                conn.commit()
                print("api_key 字段添加成功！")
            else:
                print("api_key 字段已存在，跳过。")
    except Exception as e:
        print(f"添加 api_key 字段失败: {e}")

    # 2. 创建 system_manuals 表
    try:
        # 使用 SQLAlchemy 的 create_all 方法，只会创建不存在的表
        SystemManual.__table__.create(bind=sync_engine, checkfirst=True)
        print("system_manuals 表检查/创建成功！")
    except Exception as e:
        print(f"创建 system_manuals 表失败: {e}")

    print("数据库结构更新完成！")

if __name__ == "__main__":
    update_schema()
