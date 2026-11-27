"""OpenAI 兼容 ``POST /chat/completions``（批次十五）。

协议适配层，不新增任何检索/生成能力：
- 认证复用 ``require_scope("chat:write")``（``dm_`` API Key 或 JWT）；
- 月度配额复用批次十四 ``ensure_monthly_quota``，在返回流/调用 LLM 之前；
- 生成复用 ``rag_service.chat_stream``，不落聊天会话/消息（OpenAI 客户端
  自己持有对话历史）；
- 错误统一为 OpenAI ``{"error":{"message","type","code"}}`` 结构。

路由通过 ``register_openai_compat_routes(chat.router)`` 挂进既有 chat 模块，
不改变 ``ROUTER_SPECS`` 的模块清单（issue #479 结构契约）。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Callable

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.llm_failures import UserLLMRequestError
from app.core.request_id import get_request_id
from app.core.security import get_user_org_id, require_scope
from app.exceptions import (
    AppError,
    AuthorizationError,
    ExternalServiceError,
    PipelineError,
    ServiceUnavailableError,
)
from app.models.user import User
from app.rag.errors import LLMNotConfiguredError, RetrievalUnavailableError
from app.schemas.openai_compat import OpenAIChatCompletionRequest
from app.services import openai_compat_service as compat
from app.services.organization_settings_service import organization_settings_service

logger = logging.getLogger(__name__)

OPENAI_COMPAT_TAGS = ["OpenAI 兼容"]


# ── 错误结构 ────────────────────────────────────────────────────────


def _error_type(status_code: int, code: str) -> str:
    """HTTP 状态 → OpenAI error.type（尽量贴近官方取值）。"""
    if status_code == 401:
        return "authentication_error"
    if status_code == 403:
        return "permission_error"
    if status_code == 429:
        return "insufficient_quota" if code == "ORG_TOKEN_QUOTA_EXCEEDED" else "rate_limit_error"
    if status_code >= 500:
        return "server_error"
    return "invalid_request_error"


def _error_payload(message: str, error_type: str, code: str) -> dict:
    return {
        "error": {
            "message": str(message)[:1000],
            "type": error_type,
            "code": code,
        }
    }


def openai_error_response(
    status_code: int,
    message: str,
    *,
    code: str,
    error_type: str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """构造 OpenAI 错误 JSON；401 必须带 ``WWW-Authenticate``。"""
    if headers is None and status_code == 401:
        headers = {"WWW-Authenticate": "Bearer"}
    return JSONResponse(
        status_code=status_code,
        content=_error_payload(message, error_type or _error_type(status_code, code), code),
        headers=headers,
    )


def _from_app_error(exc: AppError) -> JSONResponse:
    return openai_error_response(
        exc.status_code,
        exc.message,
        code=exc.error_code,
    )


def _from_http_exception(exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, str) and exc.detail:
        message = exc.detail
    else:
        message = "请求失败"
    return openai_error_response(
        exc.status_code,
        message,
        code="invalid_api_key" if exc.status_code == 401 else "HTTP_ERROR",
        headers=exc.headers,
    )


def _from_validation_error(exc: RequestValidationError) -> JSONResponse:
    # Pydantic 的 input/ctx 会回显原始请求值；这里只保留定位与提示。
    details = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        details.append(f"{location}: {error.get('msg', '')}".strip(": "))
    message = "请求参数无效" + (": " + "; ".join(details) if details else "")
    return openai_error_response(
        400, message, code="INVALID_REQUEST", error_type="invalid_request_error"
    )


class OpenAICompatRoute(APIRoute):
    """端点级异常转换：认证/scope/校验/配额错误都必须是 OpenAI 错误结构。

    这些错误在依赖注入阶段抛出，早于端点函数体；默认的 DocMind 错误信封
    （``success/code/error_code/...``）会让 OpenAI SDK 无法识别，因此只在
    本路由上做转换，不影响其它端点。
    """

    def get_route_handler(self) -> Callable:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                return _from_validation_error(exc)
            except AppError as exc:
                return _from_app_error(exc)
            except HTTPException as exc:
                return _from_http_exception(exc)
            except Exception:
                # 依赖/端点内的未预期异常（DB 驱动错误等）也必须保持
                # OpenAI 错误结构；细节只进服务端日志。
                logger.exception("openai_compat unhandled error")
                return openai_error_response(
                    500,
                    "服务器内部错误，请稍后重试",
                    code="INTERNAL_ERROR",
                    error_type="server_error",
                )

        return handler


class OpenAICompatStreamingResponse(StreamingResponse):
    """在响应返回/断连后，于**当前请求任务上下文**关闭 body 迭代器。

    Starlette 在 ASGI 2.3 断连时只取消 stream task，不会 close body_iterator；
    若客户端恰好在 `send` 边界断开，端点生成器停在 `yield` 上，其 finally
    只能等 GC 才执行——而 GC finalizer 运行在另一份 context 里，内层
    pipeline 在 finally 里写入的 token 用量 ContextVar 对端点不可见，导致
    计量丢失。这里在 `__call__` 返回后主动 aclose，保证 finally 与
    `report_tokens`、`current_token_usage()` 处在同一上下文。
    """

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
                    logger.warning(
                        "openai_compat response body iterator close failed", exc_info=True
                    )


# ── 响应构造 ────────────────────────────────────────────────────────


def _usage_payload(usage: tuple[int, int] | None) -> dict[str, int]:
    input_tokens, output_tokens = usage if usage else (0, 0)
    return {
        "prompt_tokens": max(0, int(input_tokens)),
        "completion_tokens": max(0, int(output_tokens)),
        "total_tokens": max(0, int(input_tokens)) + max(0, int(output_tokens)),
    }


def _completion_payload(
    *,
    completion_id: str,
    created: int,
    requested_model: str,
    answer: str,
    citations: list[dict],
    usage: tuple[int, int] | None,
) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": requested_model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer,
                    "docmind_citations": citations,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": _usage_payload(usage),
    }


def _chunk_payload(
    *,
    completion_id: str,
    created: int,
    requested_model: str,
    delta: dict,
    finish_reason: str | None,
) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": requested_model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── 生成编排 ────────────────────────────────────────────────────────


async def _retrieve(
    *,
    organization_id: int,
    turn: compat.ChatTurn,
    user_id: int,
    knowledge_base_id: str | None,
    top_k: int,
) -> list[dict]:
    try:
        return await compat.retrieve_context(
            organization_id=organization_id,
            query=turn.query,
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            top_k=top_k,
        )
    except RetrievalUnavailableError as exc:
        logger.warning("openai_compat retrieval unavailable: %s", type(exc).__name__)
        raise ServiceUnavailableError(
            "知识库检索服务暂不可用，请稍后重试", error_code="RETRIEVAL_UNAVAILABLE"
        ) from exc
    except Exception as exc:  # noqa: BLE001 — 检索故障不得伪装成「没有资料」
        logger.warning("openai_compat retrieval failed: %s", type(exc).__name__)
        raise ServiceUnavailableError(
            "知识库检索服务暂不可用，请稍后重试", error_code="RETRIEVAL_UNAVAILABLE"
        ) from exc


def _map_generation_error(exc: Exception) -> AppError:
    if isinstance(exc, LLMNotConfiguredError):
        return ServiceUnavailableError(
            "LLM 服务暂不可用，请稍后重试", error_code="LLM_NOT_CONFIGURED"
        )
    if isinstance(exc, UserLLMRequestError):
        return ExternalServiceError("回答生成失败，请稍后重试", error_code="LLM_REQUEST_FAILED")
    logger.warning("openai_compat generation failed: %s", type(exc).__name__)
    return PipelineError("回答生成失败，请稍后重试", error_code="COMPLETION_FAILED")


async def _record_and_audit(
    *,
    db: AsyncSession,
    current_user: User,
    organization_id: int,
    requested_model: str,
    knowledge_base_id: str | None,
    stream: bool,
    usage: tuple[int, int] | None,
    model: str,
    request: Request,
) -> None:
    request_id = get_request_id()
    await compat.record_usage(
        db=db,
        user_id=current_user.id,
        organization_id=organization_id,
        model=model,
        usage=usage,
        request_id=request_id,
    )
    await compat.audit_completion(
        user_id=current_user.id,
        organization_id=organization_id,
        requested_model=requested_model,
        knowledge_base_id=knowledge_base_id,
        stream=stream,
        usage=usage,
        request=request,
    )


async def _stream_response(
    *,
    body: OpenAIChatCompletionRequest,
    current_user: User,
    db: AsyncSession,
    organization_id: int,
    target: compat.ModelTarget,
    turn: compat.ChatTurn,
    model: str,
    top_k: int,
    enable_masking: bool,
    request: Request,
) -> AsyncGenerator[str, None]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    usage: tuple[int, int] | None = None
    # 显式持有内层生成器：断连可能发生在 ASGI send 边界（生成器停在 yield，
    # 不会被取消进自己的帧）。只有 finally 里主动 aclose 才会触发 pipeline
    # 的 report_tokens；否则 current_token_usage() 仍为 None，计量丢失。
    completion: AsyncGenerator[str, None] | None = None

    try:
        # 首帧：标准 OpenAI 先声明 assistant 角色。
        yield _sse(
            _chunk_payload(
                completion_id=completion_id,
                created=created,
                requested_model=body.model,
                delta={"role": "assistant"},
                finish_reason=None,
            )
        )

        try:
            context_results = await _retrieve(
                organization_id=organization_id,
                turn=turn,
                user_id=current_user.id,
                knowledge_base_id=target.knowledge_base_id,
                top_k=top_k,
            )
        except AppError as exc:
            yield _sse(
                _error_payload(
                    exc.message, _error_type(exc.status_code, exc.error_code), exc.error_code
                )
            )
            yield "data: [DONE]\n\n"
            return

        citations = compat.build_public_citations(context_results)
        try:
            completion = compat.iter_completion_chunks(
                query=turn.query,
                context_results=context_results,
                history=turn.history,
                system_prompt=turn.system_prompt,
                model=model,
                enable_masking=enable_masking,
            )
            async for text in completion:
                yield _sse(
                    _chunk_payload(
                        completion_id=completion_id,
                        created=created,
                        requested_model=body.model,
                        delta={"content": text},
                        finish_reason=None,
                    )
                )
            usage = compat.current_token_usage()
        except Exception as exc:  # noqa: BLE001 — 错误必须落成 OpenAI error 结构
            mapped = _map_generation_error(exc)
            yield _sse(
                _error_payload(
                    mapped.message,
                    _error_type(mapped.status_code, mapped.error_code),
                    mapped.error_code,
                )
            )
            yield "data: [DONE]\n\n"
            return

        final_chunk = _chunk_payload(
            completion_id=completion_id,
            created=created,
            requested_model=body.model,
            delta={},
            finish_reason="stop",
        )
        # 非破坏性扩展：标准客户端忽略未知字段；只含公开引用信息。
        final_chunk["docmind_citations"] = citations
        if usage:
            final_chunk["usage"] = _usage_payload(usage)
        yield _sse(final_chunk)
        yield "data: [DONE]\n\n"
    finally:
        # 断连/取消也会走这里：先显式关闭内层生成器，触发 pipeline 的
        # 上游 close + token 估算（断在 send 边界时它仍停在 yield 上），
        # 再在 shield 内读取并落账。正常完成时 aclose 是 no-op，不会双记。
        with anyio.CancelScope(shield=True):
            if completion is not None:
                try:
                    await completion.aclose()
                except Exception:  # noqa: BLE001 — 关闭失败不得阻断记账
                    logger.warning("openai_compat completion generator close failed", exc_info=True)
            final_usage = usage if usage is not None else compat.current_token_usage()
            await _record_and_audit(
                db=db,
                current_user=current_user,
                organization_id=organization_id,
                requested_model=body.model,
                knowledge_base_id=target.knowledge_base_id,
                stream=True,
                usage=final_usage,
                model=model,
                request=request,
            )


async def _non_stream_response(
    *,
    body: OpenAIChatCompletionRequest,
    current_user: User,
    db: AsyncSession,
    organization_id: int,
    target: compat.ModelTarget,
    turn: compat.ChatTurn,
    model: str,
    top_k: int,
    enable_masking: bool,
    request: Request,
) -> JSONResponse:
    context_results = await _retrieve(
        organization_id=organization_id,
        turn=turn,
        user_id=current_user.id,
        knowledge_base_id=target.knowledge_base_id,
        top_k=top_k,
    )
    citations = compat.build_public_citations(context_results)
    try:
        answer = await compat.collect_completion(
            query=turn.query,
            context_results=context_results,
            history=turn.history,
            system_prompt=turn.system_prompt,
            model=model,
            enable_masking=enable_masking,
        )
    except Exception as exc:  # noqa: BLE001 — 统一映射为 OpenAI error 结构
        # 上游失败前也可能已经消耗 token（pipeline 在 finally 里估算）；
        # 如实入账，不把失败调用的成本丢掉。
        await _record_and_audit(
            db=db,
            current_user=current_user,
            organization_id=organization_id,
            requested_model=body.model,
            knowledge_base_id=target.knowledge_base_id,
            stream=False,
            usage=compat.current_token_usage(),
            model=model,
            request=request,
        )
        raise _map_generation_error(exc) from exc

    usage = compat.current_token_usage()
    await _record_and_audit(
        db=db,
        current_user=current_user,
        organization_id=organization_id,
        requested_model=body.model,
        knowledge_base_id=target.knowledge_base_id,
        stream=False,
        usage=usage,
        model=model,
        request=request,
    )
    return JSONResponse(
        content=_completion_payload(
            completion_id=f"chatcmpl-{uuid.uuid4().hex}",
            created=int(time.time()),
            requested_model=body.model,
            answer=answer,
            citations=citations,
            usage=usage,
        )
    )


# ── 端点 ────────────────────────────────────────────────────────────


async def chat_completions(
    body: OpenAIChatCompletionRequest,
    request: Request,
    current_user: User = Depends(require_scope("chat:write")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """OpenAI 兼容聊天补全（非流式 + SSE 流式）。"""
    organization_id = get_user_org_id(current_user)
    if organization_id is None:
        raise AuthorizationError(
            "当前用户未加入任何组织，无法进行知识库问答", error_code="ORGANIZATION_REQUIRED"
        )
    organization_id = int(organization_id)

    turn = compat.parse_chat_messages(body.messages)
    target = await compat.resolve_model_target(db, body.model, organization_id)

    # 批次十四 C4：必须在任何 LLM 调用 / 返回 StreamingResponse 之前。
    await organization_settings_service.ensure_monthly_quota(
        db,
        organization_id,
        user=current_user,
        request=request,
    )

    org_policy = await organization_settings_service.get_runtime_policy(db, organization_id)
    model = compat.effective_model(org_policy)
    top_k = compat.retrieval_top_k(org_policy)
    enable_masking = bool(getattr(org_policy, "allow_privacy_mode", True))

    common = {
        "body": body,
        "current_user": current_user,
        "db": db,
        "organization_id": organization_id,
        "target": target,
        "turn": turn,
        "model": model,
        "top_k": top_k,
        "enable_masking": enable_masking,
        "request": request,
    }
    if body.stream:
        return OpenAICompatStreamingResponse(
            _stream_response(**common),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    return await _non_stream_response(**common)


def register_openai_compat_routes(router: APIRouter) -> None:
    """把本端点挂到既有 chat router（最终路径 ``/api/v1/chat/completions``）。"""
    router.add_api_route(
        "/completions",
        chat_completions,
        methods=["POST"],
        name="openai_chat_completions",
        summary="OpenAI 兼容聊天补全",
        tags=OPENAI_COMPAT_TAGS,
        route_class_override=OpenAICompatRoute,
        responses={
            400: {"description": "请求无效或模型不支持"},
            401: {"description": "API Key 无效或已吊销"},
            403: {"description": "API Key 缺少 chat:write scope 或无组织上下文"},
            404: {"description": "知识库不存在或无权访问"},
            429: {"description": "组织月度 token 配额已用尽"},
        },
    )
