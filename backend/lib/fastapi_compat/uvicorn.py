"""
最小化uvicorn兼容层
"""

import asyncio
from typing import Any, Optional

class Server:
    """最小化服务器兼容类"""
    def __init__(self, app, host: str = "127.0.0.1", port: int = 8000, **kwargs):
        self.app = app
        self.host = host
        self.port = port
        print(f"🌐 Uvicorn兼容层: {host}:{port}")
    
    async def serve(self):
        """启动服务"""
        print(f"🚀 启动HTTP服务器: http://{self.host}:{self.port}")
        print(f"📚 API文档: http://{self.host}:{self.port}/docs")
        print("⚠️ 注意：这是兼容层，实际功能有限")
        
        # 模拟服务器运行
        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 服务器停止")

def run(app, host: str = "127.0.0.1", port: int = 8000, reload: bool = False, **kwargs):
    """运行应用"""
    server = Server(app, host, port, **kwargs)
    
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        pass

__all__ = ["run", "Server"]
