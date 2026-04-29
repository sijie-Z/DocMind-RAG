"""
最小化Pydantic兼容层
"""

from typing import Any, Optional, Union, List, Dict
import json

class BaseModel:
    """基础模型兼容类"""
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
    
    def dict(self):
        """转换为字典"""
        return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}
    
    def json(self):
        """转换为JSON"""
        return json.dumps(self.dict(), ensure_ascii=False)

class Field:
    """字段定义兼容类"""
    def __init__(self, default: Any = None, description: str = "", **kwargs):
        self.default = default
        self.description = description
        self.kwargs = kwargs

__all__ = ["BaseModel", "Field"]
