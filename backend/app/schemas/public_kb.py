"""公开知识库发布请求 schema（批次十三）。

只做防滥用粗拦（类型/长度/数量上限）；真正的归属/状态校验（KB 属于本组织、
文档属于本组织且与 KB 一致、``INDEXED``、启用）在 ``public_kb_service`` 统一
执行，内部调用同样绕不过去。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

MAX_TITLE_CHARS = 100
MAX_DESCRIPTION_CHARS = 500
MAX_DOCUMENTS_PER_BATCH = 200
MAX_QUERY_CHARS = 200
MAX_TOP_K = 10
# 公开聊天：schema 只做防滥用硬上限（True 的业务上限由服务层按配置校验并返回 400）。
MAX_CHAT_MESSAGE_HARD_CAP = 4000
MAX_CHAT_HISTORY_HARD_CAP = 20
MAX_CHAT_HISTORY_CONTENT_HARD_CAP = 8000
MAX_DAILY_TOKEN_BUDGET = 10_000_000


class PublicationCreate(BaseModel):
    """创建发布：标题/描述/有效期 + 可选首批白名单文档。"""

    knowledge_base_id: str = Field(..., min_length=1, max_length=36, description="发布的知识库 id")
    title: str = Field(..., min_length=1, max_length=MAX_TITLE_CHARS, description="公开页标题")
    description: str | None = Field(
        default=None, max_length=MAX_DESCRIPTION_CHARS, description="公开页描述（可空）"
    )
    expires_in_days: int | None = Field(
        default=None, ge=1, le=90, description="有效期（天）；空 = 不过期"
    )
    document_ids: list[str] | None = Field(
        default=None,
        max_length=MAX_DOCUMENTS_PER_BATCH,
        description="首批白名单文档 id（必须是本组织、同 KB、已索引且启用）",
    )


class PublicationRotateRequest(BaseModel):
    """轮换 token；可选更新有效期（缺省 = 保持当前过期时间）。"""

    expires_in_days: int | None = Field(
        default=None, ge=1, le=90, description="新的有效期（天）；空 = 保持当前设置"
    )


class PublicationDocumentsUpdate(BaseModel):
    """批量设置白名单：replace=整体替换，add=追加，remove=移除。"""

    mode: Literal["replace", "add", "remove"] = "replace"
    document_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_DOCUMENTS_PER_BATCH,
        description="文档 id 列表（上限 200 条）",
    )


class PublicKbSearchRequest(BaseModel):
    """公开检索请求（无登录；token 即全部权限）。"""

    query: str = Field(..., description="检索词（服务层限制 1–200 字符）")
    top_k: int = Field(default=5, description="返回条数（服务层限制 1–10）")


class PublicKbChatHistoryItem(BaseModel):
    """客户端携带的单条历史（只允许 user/assistant，服务层复核）。"""

    role: str = Field(..., max_length=20, description="角色：user / assistant")
    content: str = Field(
        ...,
        max_length=MAX_CHAT_HISTORY_CONTENT_HARD_CAP,
        description="历史消息正文（服务层限制 1–2000 字符）",
    )


class PublicKbChatRequest(BaseModel):
    """公开匿名聊天请求（非流式 / SSE 流式共用）。

    历史由客户端携带，服务端**不持久化**任何会话/消息；``history`` 的服务层
    上限为 ``PUBLIC_KB_CHAT_MAX_HISTORY`` 条、每条 2000 字符。
    """

    message: str = Field(
        ...,
        max_length=MAX_CHAT_MESSAGE_HARD_CAP,
        description="提问（服务层限制 1–PUBLIC_KB_CHAT_MAX_QUERY_CHARS 字符）",
    )
    history: list[PublicKbChatHistoryItem] | None = Field(
        default=None,
        max_length=MAX_CHAT_HISTORY_HARD_CAP,
        description="最近对话（仅 user/assistant；缺省 = 无历史）",
    )
    stream: bool = Field(default=False, description="true = SSE 流式")


class PublicationChatSettingsUpdate(BaseModel):
    """管理面：更新发布级聊天开关与日预算（空预算 = 使用全局默认）。"""

    chat_enabled: bool = Field(..., description="发布级聊天开关")
    daily_token_budget: int | None = Field(
        default=None,
        ge=0,
        le=MAX_DAILY_TOKEN_BUDGET,
        description="每日 token 预算；null = 全局默认，0 = 禁止聊天",
    )

    @field_validator("daily_token_budget", mode="before")
    @classmethod
    def _reject_boolean_budget(cls, value):
        """bool 是 int 的子类；True/False 不得被静默转成 1/0。"""
        if isinstance(value, bool):
            raise ValueError("daily_token_budget 必须是整数或空，不能是布尔值")
        return value
