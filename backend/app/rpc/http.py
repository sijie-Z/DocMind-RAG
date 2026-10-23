"""``POST /api/v1/rpc``（issue #480 §5.3 / §5.5）。

- 仅 web profile 的 agent bundle 会挂载本路由；``RPC_HTTP_ENABLED=false`` 时
  端点 404（sdk profile 只提供 stdio，不启动 HTTP）；
- 认证必须走 ``dm_`` API Key 且 scope 含 ``rpc``（JWT 不能满足"API key"要求）；
- 接受单个 JSON-RPC 对象或批量数组（上限 ``RPC_BATCH_MAX``=20）；
- HTTP 没有服务端→客户端回推通道：``session.event``/``session.status`` 通知被
  丢弃（客户端流式消费请走 REST + SSE）；``session/request_permission`` 不可用，
  审批改由 ``approvals/respond`` 主动应答。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.core.config import settings
from app.core.error_mapping import JSONRPC_INVALID_REQUEST, JSONRPC_PARSE_ERROR
from app.core.security import get_user_org_id, require_scope
from app.models.user import User
from app.rpc.dispatcher import RPCService

router = APIRouter()
logger = logging.getLogger(__name__)


def _error_response(code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": None, "error": {"code": code, "message": message}}


def _rpc_enabled() -> None:
    """开关先于认证求值：关闭时如实 404（回滚口径，不暴露内部认证细节）。"""
    if not settings.RPC_HTTP_ENABLED:
        raise HTTPException(status_code=404, detail="JSON-RPC HTTP 未启用")


def _require_bearer(request: Request) -> None:
    """缺 Authorization 头时统一 401（HTTPBearer 的缺省语义是 403）。"""
    if not request.headers.get("authorization"):
        raise HTTPException(
            status_code=401,
            detail="认证失败",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post(
    "",
    response_model=None,
    dependencies=[Depends(_rpc_enabled), Depends(_require_bearer)],
)
async def rpc_http(
    request: Request,
    current_user: User = Depends(require_scope("rpc")),
):
    if getattr(current_user, "api_key_id", None) is None:
        return JSONResponse(
            status_code=403,
            content={"detail": "JSON-RPC 需要通过 API Key 认证（Bearer dm_...）"},
        )

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - 非法 JSON → JSON-RPC Parse error
        return JSONResponse(
            status_code=200, content=_error_response(JSONRPC_PARSE_ERROR, "Parse error")
        )

    service = RPCService(
        organization_id=get_user_org_id(current_user),
        user_id=current_user.id,
        is_superuser=bool(getattr(current_user, "is_superuser", False)),
    )
    dispatcher = service.build_dispatcher(send=None)  # HTTP：无服务端回推通道

    if isinstance(payload, list):
        if len(payload) > int(settings.RPC_BATCH_MAX):
            return JSONResponse(
                status_code=200,
                content=_error_response(
                    JSONRPC_INVALID_REQUEST,
                    f"批量请求超过上限 {settings.RPC_BATCH_MAX}",
                ),
            )
        responses: list[dict[str, Any]] = []
        for message in payload:
            response = await dispatcher.handle_message(message)
            if response is not None:
                responses.append(response)
        if not responses:
            return Response(status_code=204)
        return JSONResponse(status_code=200, content=responses)

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=200,
            content=_error_response(JSONRPC_INVALID_REQUEST, "请求必须是 JSON 对象或数组"),
        )
    response = await dispatcher.handle_message(payload)
    if response is None:
        return Response(status_code=204)
    return JSONResponse(status_code=200, content=response)
