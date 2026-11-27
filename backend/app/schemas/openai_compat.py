"""OpenAI 兼容聊天补全请求 schema（批次十五）。

只声明本批真正接地气的字段；``extra="allow"`` 保证 OpenAI SDK 新增参数
（``stream_options`` / ``presence_penalty`` / ``response_format`` 等）不会把
请求打成 422——本端点明确忽略不理解的标准字段，而不是假装支持它们。

业务校验（角色集合、content 类型、空 messages、模型映射）在
``app.services.openai_compat_service`` 中完成，以便返回 OpenAI 错误结构
而不是 FastAPI 默认的 422 信封。
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class OpenAIChatMessage(BaseModel):
    """一条 OpenAI ``messages[]`` 元素；content 允许任意形状，业务层再判定。"""

    model_config = ConfigDict(extra="allow")

    role: str
    content: Any = None


class OpenAIChatCompletionRequest(BaseModel):
    """``POST /chat/completions`` 请求体。"""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[OpenAIChatMessage]
    stream: bool | None = False
    temperature: float | None = None
    max_tokens: int | None = None
