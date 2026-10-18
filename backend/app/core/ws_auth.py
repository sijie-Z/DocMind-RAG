"""WebSocket 认证助手（issue #82 / PR C —— WS 认证生命周期）。

WS 握手没有 HTTP 状态码，认证失败的契约统一为 **close code + reason**：

* ``4003`` ``Token required``  —— 请求未携带令牌
* ``4002`` ``Missing user id`` —— 令牌本身可用，但缺少可用的 user_id 声明
* ``4001`` 其余一切认证失败：令牌无效 / 类型错误 / 已吊销 / 用户已删除 / 用户已禁用

安全契约（全局，见 issue #82）：**已删除（用户不存在）与已禁用（is_active=False）
必须返回完全相同的 code + reason**。这里进一步让"用户失效"与"令牌无效"同码同因，
调用方无法借响应差异区分凭证失效的原因，也就无法枚举账号状态。

握手必须查库：访问令牌有效期默认 24 小时，只验签意味着已删除/已禁用的用户仍能
建连并持续消耗 LLM 与检索资源。长连接的每一条消息同样要复查用户状态。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import user_organization
from app.models.user import User
from app.services.auth_service import auth_service

logger = logging.getLogger(__name__)

# ── close code 约定（与两个 WS 端点既有取值保持一致）────────────────
WS_CLOSE_AUTH_FAILED = 4001
WS_CLOSE_MISSING_USER_ID = 4002
WS_CLOSE_TOKEN_REQUIRED = 4003

WS_REASON_TOKEN_REQUIRED = "Token required"
WS_REASON_MISSING_USER_ID = "Missing user id"
WS_REASON_INVALID_TOKEN = "Invalid token"
WS_REASON_INVALID_TOKEN_TYPE = "Invalid token type"
WS_REASON_TOKEN_REVOKED = "Token revoked"
# A2 P0：被移出组织的成员（令牌组织已无成员关系）在 WS 握手层即拒绝，
# 与 HTTP 侧 auth_service.get_current_user 的 403 同语义（close code 4001）。
WS_REASON_ORG_MEMBERSHIP = "Organization membership invalid"

# 用户已删除 / 已禁用 / 令牌无效 共用这一组 code+reason（不可区分）
WS_REASON_USER_INVALID = WS_REASON_INVALID_TOKEN


@dataclass(frozen=True)
class WsUserState:
    """WS 认证所需的最小用户状态。"""

    id: int
    is_active: bool
    organization_id: int | None


def _clean_token(raw: str) -> str:
    """去掉客户端可能带上的引号与空白（旧客户端的拼接方式不统一）。"""
    return raw.strip().replace('"', "").replace("'", "")


def _first_auth_subprotocol(websocket: WebSocket) -> str | None:
    """返回握手中第一个 ``auth.*`` 子协议的**原文**（未清洗、未二次拼接）。

    按客户端原始顺序逐项匹配（浏览器可带多个子协议，逗号分隔），大小写敏感、
    不带任何改写——鉴权与回显必须选中同一个子协议，故共用这一处解析。
    """
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    for proto in protocols.split(","):
        proto = proto.strip()
        if proto.startswith("auth."):
            return proto
    return None


def extract_ws_token(websocket: WebSocket) -> str | None:
    """提取令牌：优先 Sec-WebSocket-Protocol 的 ``auth.<token>``（令牌不落 URL/日志），
    兼容旧的 ``?token=`` 查询参数。"""
    proto = _first_auth_subprotocol(websocket)
    if proto is not None:
        return _clean_token(proto[5:])

    token = websocket.query_params.get("token")
    return _clean_token(token) if token else None


def extract_ws_subprotocol(websocket: WebSocket) -> str | None:
    """提取客户端请求的 ``auth.*`` 子协议原文，供 ``accept(subprotocol=...)`` 回显。

    浏览器契约：请求了 ``Sec-WebSocket-Protocol`` 而 101 响应未回显其中之一时，
    必须判定握手失败（close 1006）。回显串必须与客户端发送的原文逐字符一致
    （大小写、引号等原样保留），因此返回原始子协议而不是用清洗后的令牌重新拼接。
    提取不到时返回 ``None``，调用方以裸 ``accept()`` 语义处理。
    """
    return _first_auth_subprotocol(websocket)


async def _load_user_state(db: AsyncSession, user_id: int) -> WsUserState | None:
    """按 id 读用户状态；不存在返回 ``None``。

    刻意用列级查询而不是 ``auth_service.get_user_by_id()`` 的实体加载：``User`` 的
    关联关系是 ``lazy="selectin"``，取一个实体要多打好几条查询（documents /
    chat_sessions / organizations …），而握手与每条消息都要查，认证只需要
    id / is_active / organization_id 三个字段。
    """
    row = (
        await db.execute(
            select(User.id, User.is_active, User.organization_id).where(User.id == user_id)
        )
    ).first()
    if row is None:
        return None
    return WsUserState(
        id=int(row.id),
        is_active=bool(row.is_active),
        organization_id=row.organization_id,
    )


async def close_ws(websocket: WebSocket, code: int, reason: str) -> None:
    """关闭连接。握手阶段客户端可能已经离开，close 失败不应影响调用方收尾。"""
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        logger.debug("WebSocket close(code=%s) 失败", code, exc_info=True)


async def authenticate_ws(websocket: WebSocket, db: AsyncSession) -> WsUserState | None:
    """WS 握手认证：验签 → 令牌类型 → 吊销 → **查库** → is_active。

    成功返回可用的用户状态；任何一步失败都返回 ``None``，并已按统一契约关闭连接。
    调用方只需 ``if user is None: return``。
    """
    try:
        return await _authenticate_ws(websocket, db)
    except Exception:
        # 认证过程中的意外异常（如数据库不可用）一律按认证失败处理：
        # 既不把异常细节泄露给客户端，也保持 fail-closed。
        logger.exception("WebSocket 认证过程出现异常")
        await close_ws(websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_USER_INVALID)
        return None


async def _authenticate_ws(websocket: WebSocket, db: AsyncSession) -> WsUserState | None:
    token = extract_ws_token(websocket)
    if not token:
        await close_ws(websocket, WS_CLOSE_TOKEN_REQUIRED, WS_REASON_TOKEN_REQUIRED)
        return None

    payload = auth_service.verify_token(token)
    if not payload:
        await close_ws(websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_INVALID_TOKEN)
        return None

    # 安全加固：仅接受 access token（refresh token 不得建立 WS 连接）
    if payload.get("type") != "access":
        await close_ws(websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_INVALID_TOKEN_TYPE)
        return None

    # 安全加固：校验令牌是否已被吊销
    if await auth_service.is_token_blacklisted(token):
        await close_ws(websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_TOKEN_REVOKED)
        return None

    try:
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        await close_ws(websocket, WS_CLOSE_MISSING_USER_ID, WS_REASON_MISSING_USER_ID)
        return None

    # 安全加固：不能只信 JWT —— 令牌有效期内用户可能已被删除或禁用
    user = await _load_user_state(db, user_id)
    if user is None or not user.is_active:
        # 已删除与已禁用必须不可区分（同一 code + 同一 reason）
        logger.warning("WebSocket 认证失败：用户 %s 不存在或已被禁用", user_id)
        await close_ws(websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_USER_INVALID)
        return None

    # A2 P0（评审 m-3）：与 HTTP 侧同一成员校验——token 组织回退必须回源
    # user_organization；被移出成员的旧 token 在 WS 握手层即时失效。
    token_org_id = payload.get("organization_id")
    if token_org_id is not None and user.organization_id is None:
        membership = await db.execute(
            select(user_organization.c.user_id).where(
                user_organization.c.user_id == user.id,
                user_organization.c.organization_id == token_org_id,
            )
        )
        if membership.first() is None:
            logger.warning(
                "WebSocket 认证失败：用户 %s 的令牌组织 %s 已无成员关系",
                user_id,
                token_org_id,
            )
            await close_ws(
                websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_ORG_MEMBERSHIP
            )
            return None

    return user


async def load_active_ws_user(db: AsyncSession, user_id: int) -> WsUserState | None:
    """消息阶段的实时用户状态校验：返回用户状态；已删除/已禁用返回 ``None``。

    连接建立后用户仍可能被删除或禁用，长连接不能凭握手时的结果一直跑下去。
    """
    user = await _load_user_state(db, user_id)
    if user is None or not user.is_active:
        return None
    return user
