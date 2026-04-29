"""
最小化FastAPI兼容层
"""

from typing import Any, Dict, List, Optional, Type, Union
import json
import sys

class FastAPI:
    """最小化FastAPI兼容类"""
    def __init__(self, title: str = "FastAPI", version: str = "1.0.0", **kwargs):
        self.title = title
        self.version = version
        self.routes = []
        self.middleware = []
        print(f"🚀 FastAPI兼容层启动: {title} v{version}")
    
    def get(self, path: str, **kwargs):
        """GET路由装饰器"""
        def decorator(func):
            self.routes.append(("GET", path, func))
            print(f"📡 注册GET路由: {path}")
            return func
        return decorator
    
    def post(self, path: str, **kwargs):
        """POST路由装饰器"""
        def decorator(func):
            self.routes.append(("POST", path, func))
            print(f"📡 注册POST路由: {path}")
            return func
        return decorator
    
    def websocket(self, path: str):
        """WebSocket路由装饰器"""
        def decorator(func):
            self.routes.append(("WEBSOCKET", path, func))
            print(f"📡 注册WebSocket路由: {path}")
            return func
        return decorator
    
    def add_middleware(self, middleware_class, **kwargs):
        """添加中间件"""
        self.middleware.append((middleware_class, kwargs))
        print(f"🔧 添加中间件: {middleware_class.__name__}")

class HTTPException(Exception):
    """HTTP异常"""
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)

class WebSocket:
    """最小化WebSocket兼容类"""
    def __init__(self):
        self.client_state = "connected"
    
    async def accept(self):
        """接受连接"""
        print("🔄 WebSocket连接已接受")
    
    async def receive_json(self):
        """接收JSON消息"""
        return {"message": "模拟消息"}
    
    async def send_json(self, data: dict):
        """发送JSON消息"""
        print(f"📤 WebSocket发送: {data}")

class WebSocketDisconnect(Exception):
    """WebSocket断开连接异常"""
    pass

class UploadFile:
    """文件上传兼容类"""
    def __init__(self, filename: str = "", content_type: str = ""):
        self.filename = filename
        self.content_type = content_type
    
    async def read(self):
        return b"file content"

class Form:
    """表单字段兼容类"""
    def __init__(self, default: Any = None):
        self.default = default

class File:
    """文件字段兼容类"""
    def __init__(self, default: Any = None):
        self.default = default

class JSONResponse:
    """JSON响应兼容类"""
    def __init__(self, content: Any, status_code: int = 200):
        self.content = content
        self.status_code = status_code
    
    def __call__(self):
        return json.dumps(self.content, ensure_ascii=False, indent=2)

class StreamingResponse:
    """流式响应兼容类"""
    def __init__(self, content: Any, media_type: str = "text/plain"):
        self.content = content
        self.media_type = media_type

class CORSMiddleware:
    """CORS中间件兼容类"""
    def __init__(self, app, **kwargs):
        self.app = app
        print(f"🔒 CORS中间件已配置: allow_origins={kwargs.get('allow_origins', [])}")

# 导出所有兼容类
__all__ = [
    "FastAPI", "HTTPException", "WebSocket", "WebSocketDisconnect",
    "UploadFile", "Form", "File", "JSONResponse", "StreamingResponse", 
    "CORSMiddleware"
]
