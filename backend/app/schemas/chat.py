"""
聊天相关的Pydantic模型
"""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime


class ChatType(str, Enum):
    """聊天类型"""
    KNOWLEDGE = "knowledge"  # 知识问答
    TEXT2SQL = "text2sql"     # TextToSQL
    GENERAL = "general"       # 通用对话


class MessageType(str, Enum):
    """消息类型"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatSessionSettings(BaseModel):
    """会话设置模型"""
    bound_document_ids: Optional[List[str]] = Field(None, description="绑定的文档ID列表")


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., min_length=1, description="用户消息")
    session_id: Optional[str] = Field(None, description="会话ID（用于多轮对话）")
    organization_id: Optional[str] = Field(None, description="组织ID（用于知识库问答）")
    chat_type: ChatType = Field(ChatType.GENERAL, description="对话类型")
    context: Optional[Dict[str, Any]] = Field(None, description="额外上下文信息")
    file_ids: Optional[List[str]] = Field(None, description="本次提问关联的文件ID")


class ChatResponse(BaseModel):
    """聊天响应"""
    message: str = Field(..., description="AI回答")
    session_id: Optional[str] = Field(None, description="会话ID")
    chat_type: str = Field(..., description="对话类型")
    timestamp: float = Field(..., description="时间戳")


class ChatSessionCreate(BaseModel):
    """创建聊天会话请求"""
    title: Optional[str] = Field(None, description="会话标题")
    organization_id: Optional[str] = Field(None, description="组织ID")


class ChatSessionResponse(BaseModel):
    """聊天会话响应"""
    session_id: str = Field(..., description="会话ID")
    title: Optional[str] = Field(None, description="会话标题")
    user_id: str = Field(..., description="用户ID")
    organization_id: Optional[str] = Field(None, description="组织ID")
    status: str = Field(..., description="会话状态")
    message_count: int = Field(0, description="消息数量")
    last_message_at: Optional[datetime] = Field(None, description="最后消息时间")
    created_at: datetime = Field(..., description="创建时间")
    settings: Optional[ChatSessionSettings] = Field(None, description="会话设置")


class ChatMessageResponse(BaseModel):
    """聊天消息响应"""
    id: str = Field(..., description="消息ID")
    session_id: str = Field(..., description="会话ID")
    content: str = Field(..., description="消息内容")
    message_type: str = Field(..., description="消息类型")
    metadata: Optional[Dict[str, Any]] = Field(None, description="元数据")
    created_at: datetime = Field(..., description="创建时间")


class ChatHistoryResponse(BaseModel):
    """聊天历史响应"""
    session_id: str = Field(..., description="会话ID")
    messages: List[ChatMessageResponse] = Field(..., description="消息列表")
    total_count: int = Field(..., description="消息总数")


class ChatStreamChunk(BaseModel):
    """聊天流式数据块"""
    type: str = Field(..., description="数据类型：chunk, end, error")
    content: str = Field(..., description="内容")
    timestamp: float = Field(..., description="时间戳")