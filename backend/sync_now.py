# backend/sync_now.py
import os
import sys
import asyncio
from pathlib import Path

# 1. 强制加载 .env 环境变量
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
        print(f"✅ 成功加载环境变量: {env_path}")
    else:
        print(f"❌ 未找到 .env 文件: {env_path}")
except ImportError:
    print("⚠️ 请运行 'pip install python-dotenv' 以确保环境加载正常")

# 2. 将当前路径加入 sys.path 确保能导入 app 和 worker
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.core.database import AsyncSessionLocal
from app.models.document import Document
from worker.doc_consumer import process_document
from sqlalchemy import select
from app.core.config import settings

async def run():
    print(f"🔍 检查配置: ES_INDEX={settings.ELASTICSEARCH_INDEX_NAME}")
    api_key = settings.EMBEDDING_API_KEY or ""
    masked_api_key = f"{api_key[:8]}***" if api_key else "<未配置>"
    print(f"🔍 检查 API KEY: {masked_api_key}")
    
    async with AsyncSessionLocal() as session:
        # 获取所有文档记录
        res = await session.execute(select(Document))
        docs = res.scalars().all()
        
        if not docs:
            print("❌ 数据库中没有文档记录。请先在前端上传文件！")
            return
            
        print(f"🚀 找到 {len(docs)} 个文档，准备强制同步到 ES...")
        
        for doc in docs:
            print(f"\n📄 正在重新处理: {doc.filename} (ID: {doc.id})")
            task = {
                "document_id": doc.id,
                "file_path": doc.file_path,
                "organization_id": doc.organization_id,
                "filename": doc.filename,
                "file_type": doc.file_type
            }
            try:
                # 调用我们在 worker/doc_consumer.py 中修改过的带错误捕捉的函数
                await process_document(task)
            except Exception as e:
                print(f"💥 处理文档时崩溃: {e}")

    print("\n✨ 同步尝试结束。请再次运行 curl 命令检查 ES 数据。")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
    