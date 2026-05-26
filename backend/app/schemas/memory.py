"""记忆系统相关的 Pydantic 模型"""
from typing import Any

from pydantic import BaseModel, Field


class RememberRequest(BaseModel):
    """存储记忆"""
    content: str = Field(..., min_length=1, description="记忆内容")
    memory_type: str = Field("fact", description="记忆类型：fact/preference/interaction")
    importance: float = Field(0.5, ge=0.0, le=1.0, description="重要性 0-1")
    metadata: dict[str, Any] | None = Field(None, description="额外元数据")


class RecallRequest(BaseModel):
    """召回记忆"""
    query: str = Field(..., min_length=1, description="查询内容")
    memory_types: list[str] | None = Field(None, description="限定记忆类型")
    top_k: int = Field(5, ge=1, le=50, description="返回数量")


class InteractionRequest(BaseModel):
    """记录交互"""
    user_input: str = Field(..., min_length=1, description="用户输入")
    assistant_response: str = Field(..., min_length=1, description="助手回复")


class ExperienceRequest(BaseModel):
    """记录经验"""
    success: bool = Field(..., description="是否成功")
    action: str = Field(..., min_length=1, description="执行的动作")
    result: str = Field(..., min_length=1, description="执行结果")
    context: str | None = Field(None, description="上下文")


class ImportMemoriesRequest(BaseModel):
    """批量导入记忆"""
    data: dict[str, Any] = Field(..., description="记忆数据")
