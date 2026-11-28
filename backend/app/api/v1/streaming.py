"""SSE/流式响应的公共包装器（批次十五经验，批次十七复用）。

Starlette 在 ASGI 2.3 断连时只取消 stream task，不会 ``aclose`` body iterator。
若客户端恰好在 ``send`` 边界断开，端点生成器停在 ``yield`` 上，其 ``finally``
只能等 GC 才执行——而 GC finalizer 运行在另一份 context 里，内层 pipeline 在
``finally`` 里写入的 token 用量 ContextVar 对端点不可见，导致计量丢失。

本类在 ``__call__`` 返回后主动 ``aclose`` body iterator，保证 finally 与
``report_tokens`` / ``current_token_usage()`` 处在同一上下文；关闭动作包在
``anyio.CancelScope(shield=True)`` 内，已取消的 cancel scope 不会打断收尾。
"""

from __future__ import annotations

import logging

import anyio
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)


class ClosingStreamingResponse(StreamingResponse):
    """在响应返回/断连后，于当前请求任务上下文关闭 body 迭代器。"""

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close_method = getattr(self.body_iterator, "aclose", None)
            if close_method is not None:
                try:
                    with anyio.CancelScope(shield=True):
                        await close_method()
                except Exception:  # noqa: BLE001 — 收尾失败不得覆盖响应结果
                    logger.warning("streaming response body iterator close failed", exc_info=True)
