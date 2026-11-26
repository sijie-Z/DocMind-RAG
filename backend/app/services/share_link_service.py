"""会话只读分享 token 的共享工具（批次八复用既有分享机制）。

分享链接只有一种授权模型（``chat_sessions.share_token_hash`` + 公开只读端点
``GET /api/v1/chat/shares/{token}``）。会话树分享若自行发明第二套 token/鉴权，
会扩大攻击面且与既有语义漂移，因此这里只抽出**同一套** token 生成与摘要算法，
供 chat 分享端点与会话树分享端点共用：

- token 原文只在创建/轮换响应中返回一次，DB 只存 SHA-256 摘要；
- 日志/审计不落 token 原文；
- 读取侧只按摘要等值查询（与 ``chat.py`` 既有实现逐字一致）。
"""

from __future__ import annotations

import hashlib
import secrets

# token 熵：24 字节 URL-safe base64（约 32 字符），与既有分享链接一致。
SHARE_TOKEN_BYTES = 24


def new_share_token() -> str:
    """生成新的分享 token 原文（只在创建响应中返回一次）。"""
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


def share_token_digest(token: str) -> str:
    """分享 token 的稳定摘要；不记录原文，避免日志/数据库泄露链接。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
