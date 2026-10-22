"""会话分叉请求模型（issue #476）。"""

from pydantic import BaseModel, Field


class SessionForkRequest(BaseModel):
    """``POST /api/v1/sessions/{id}/fork`` 的请求体。

    所有字段可选：空请求 = 从最后一条已结算消息默认分叉，不克隆 checkpoint。
    """

    message_id: str | None = Field(
        None,
        max_length=36,
        description="边界消息 ID：ASSISTANT=复制到该消息为止；USER=在该消息之前分叉。默认最后一条已结算消息",
    )
    title: str | None = Field(None, max_length=200, description="子会话标题；默认「原标题 (fork)」")
    include_checkpoint: bool = Field(
        False,
        description="是否深拷最近一个已结算 checkpoint 到新 PAUSED 任务（可 resume 继续）",
    )
    seed: str | None = Field(
        None,
        max_length=4000,
        description="种子内容：写入子会话独立的 session:<id> 记忆命名空间（不复制母记忆）",
    )
    reason: str | None = Field(None, max_length=100, description="分叉原因标签（默认 user_fork）")
