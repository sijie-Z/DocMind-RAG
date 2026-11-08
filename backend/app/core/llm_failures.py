"""个人 AI 模型（用户自带 provider/key）失败的共享契约。

RAG 与 Agent 两条链路共用同一错误码与可操作文案：上游失败必须以 error
事件外发（带机器码，前端四语化），不得把诊断文案当成回答落库。
"""

from __future__ import annotations

import asyncio
from typing import Any

USER_LLM_REQUEST_FAILED_CODE = "USER_LLM_REQUEST_FAILED"
USER_LLM_REQUEST_FAILED_MESSAGE = (
    "个人模型调用失败。请在个人设置的 AI 模型设置中检查 API Key / Base URL，或稍后重试。"
)

# 个人模型客户端的一次性标记：由 llm_settings_service 创建客户端时打上，
# Agent 的 planner/executor 等在异常处理里据此区分「用户配置问题」与
# 「服务端默认模型问题」——只有前者才给可操作文案并中止运行。
USER_LLM_CLIENT_ATTR = "_docmind_user_llm"


class UserLLMRequestError(RuntimeError):
    """个人模型上游失败；聊天/Agent 层据此发 error 事件，不得把诊断当作回答落库。"""


def mark_user_llm_client(client: Any) -> None:
    """给 per-user 客户端打标；失败不影响调用（标记只是增强）。"""
    if client is None:
        return
    try:
        setattr(client, USER_LLM_CLIENT_ATTR, True)
    except Exception:  # noqa: BLE001 - 个别客户端可能禁止属性写入
        return


def is_user_llm_client(client: Any) -> bool:
    # 严格 `is True`：MagicMock/代理对象对未知属性的自动返回值是 truthy，
    # 用 bool(getattr(...)) 会把「未标记」误判成个人模型。
    return getattr(client, USER_LLM_CLIENT_ATTR, False) is True


def is_provider_call_error(exc: BaseException) -> bool:
    """是否为「模型端点调用」类异常（鉴权/连接/超时/限流/HTTP 状态）。

    解析/落盘等下游错误不算：它们不该被提示成「检查 API Key」。
    """
    import httpx
    import openai

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.HTTPError)):
        return True
    return isinstance(exc, openai.OpenAIError)
