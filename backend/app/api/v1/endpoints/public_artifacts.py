"""工件公开分享端点（批次九）：凭 token 的只读元数据与正文。

授权模型：**token 即全部权限**，没有 JWT/scope/组织依赖。token 校验统一走
``artifact_share_service.resolve_public_share``——撤销/过期/不存在全部 404，
不区分原因。响应刻意只含展示字段（title/kind/filename/mime/version/时间），
不暴露内部 id、组织、用户、会话、任务、版本链。

部署开关 ``ARTIFACT_PUBLIC_SHARE_ENABLED`` 默认 false：关闭时元数据与正文端点
都按 404 处理。限流为进程内固定窗口桶（每 IP + 每 token 每分钟上限），超限
429；不信任 ``X-Forwarded-For``（避免轮换头绕过桶），多副本部署时每副本各自
计数（已知边界，与全局 RateLimit 中间件的进程内语义一致）。

正文响应头是安全边界的一部分：HTML/SVG 工件即使被直接打开，也必须在无同源
权限的沙箱中运行（CSP sandbox + nosniff + no-store + no-referrer + noindex）。
"""

from __future__ import annotations

import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.exceptions import AppError, NotFoundError, RateLimitError
from app.services import artifact_service, artifact_share_service
from app.services.share_link_service import share_token_digest

logger = logging.getLogger(__name__)
router = APIRouter()

_SHARE_INVALID = "分享链接不存在或已失效"
_RATE_WINDOW_SECONDS = 60
# 固定窗口内存桶：key -> (window_start, count)。
# - 有效 token：只按 token 摘要计数（不按 IP）。反向代理后所有访客共享代理 IP，
#   按 IP 聚合会让一个客户端打满全站；按链接桶最多只影响该链接自身。
# - 无效 token：按客户端 IP 单独计数，阈值更高；失败请求不占用有效链接桶。
_RATE_BUCKETS: dict[str, tuple[int, int]] = {}
_MAX_BUCKET_KEYS = 4096


def _require_share_enabled() -> None:
    """开关关闭时公开面按不存在处理（404，而非 403/503）。"""
    if not settings.ARTIFACT_PUBLIC_SHARE_ENABLED:
        raise NotFoundError(_SHARE_INVALID)


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client is not None and client.host else "unknown"


def _consume_bucket(key: str, limit: int) -> None:
    now = int(time.time())
    window_start = now - (now % _RATE_WINDOW_SECONDS)
    current = _RATE_BUCKETS.get(key)
    count = 1 if current is None or current[0] != window_start else current[1] + 1
    _RATE_BUCKETS[key] = (window_start, count)
    if count > limit:
        raise RateLimitError()
    # 有界内存：先清过期桶；仍超上限则淘汰任意一个旧键，保证 O(1) 有界。
    if len(_RATE_BUCKETS) > _MAX_BUCKET_KEYS:
        for bucket_key, (start, _count) in list(_RATE_BUCKETS.items()):
            if start != window_start:
                _RATE_BUCKETS.pop(bucket_key, None)
        while len(_RATE_BUCKETS) > _MAX_BUCKET_KEYS:
            _RATE_BUCKETS.pop(next(iter(_RATE_BUCKETS)))


def _check_link_rate_limit(token: str) -> None:
    """有效链接：按 token 摘要限流，单客户端不影响其它链接。"""
    _consume_bucket(
        f"token:{share_token_digest(token)}",
        max(1, int(settings.ARTIFACT_PUBLIC_RATE_LIMIT_PER_MINUTE)),
    )


def _check_invalid_rate_limit(request: Request) -> None:
    """无效 token：按 IP 用更高阈值限流，仅约束扫描，不阻断有效链接。"""
    limit = max(1, int(settings.ARTIFACT_PUBLIC_RATE_LIMIT_PER_MINUTE))
    _consume_bucket(f"invalid-ip:{_client_ip(request)}", max(300, limit * 5))


def _filename_headers(filename: str) -> str:
    """Content-Disposition：ASCII 兜底 + RFC 5987 的 UTF-8 文件名。"""
    safe = (filename or "artifact").replace('"', "_")
    ascii_name = safe.encode("ascii", "replace").decode("ascii").replace("?", "_")
    quoted = quote(safe, safe="")
    return f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quoted}"


@router.get("/artifacts/{token}", response_model=dict)
async def get_public_artifact(
    token: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """公开分享的工件元数据（展示字段白名单；无任何内部标识）。"""
    _require_share_enabled()
    try:
        try:
            artifact, share = await artifact_share_service.resolve_public_share(db, token)
        except NotFoundError:
            _check_invalid_rate_limit(request)
            raise
        _check_link_rate_limit(token)
        # 元数据同样绑定 token 且含过期时间：撤销后不得被缓存复用。
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return {
            "success": True,
            "data": artifact_share_service.public_artifact_dict(artifact, share, token),
        }
    except NotFoundError:
        raise
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 — 映射为统一失败信封
        logger.error(f"Get public artifact failed: {type(e).__name__}: {e}")
        raise AppError("获取分享工件失败") from e


@router.get("/artifacts/{token}/content")
async def get_public_artifact_content(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """公开分享的工件正文（有界内容 ≤512 KiB，以单块 StreamingResponse 返回）。

    安全头逐项设置，见模块 docstring。
    """
    _require_share_enabled()
    try:
        try:
            artifact, share = await artifact_share_service.resolve_public_share(db, token)
        except NotFoundError:
            _check_invalid_rate_limit(request)
            raise
        _check_link_rate_limit(token)
        payload = await artifact_service.read_content(artifact)
        # 先把响应所需字段固化进响应对象，再执行计数：计数失败会 rollback
        # 请求会话并使 ORM 实例过期，之后再同步读取 artifact.mime_type/filename
        # 会触发 async 惰性加载（MissingGreenlet → 500），违反「计数失败不影响
        # 200」。响应构造本身不发送，计数仍发生在正文成功读取之后。
        response = StreamingResponse(
            iter([payload]),
            media_type=artifact.mime_type,
            headers={
                # 文件名回退串**不得**含 artifact.id（内部标识不进公开面）。
                "Content-Disposition": _filename_headers(artifact.filename or "artifact"),
                "X-Content-Type-Options": "nosniff",
                # 模型生成的 HTML/SVG 直开此 URL 时也必须在无同源权限的沙箱中运行。
                "Content-Security-Policy": (
                    "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
                ),
                "Referrer-Policy": "no-referrer",
                "X-Robots-Tag": "noindex",
                # 分享可被撤销/过期：响应不得被中间层或浏览器缓存复用。
                "Cache-Control": "no-store",
            },
        )
        # 访问统计（批次十一）：正文成功读取后才计数；元数据端点不计数。
        # 计数是旁路：即使服务层意外抛错，也必须返回正常正文。
        try:
            await artifact_share_service.record_public_access(db, share)
        except Exception as e:  # noqa: BLE001 — 计数失败不影响响应
            logger.warning(f"Record public artifact access failed: {type(e).__name__}: {e}")
        return response
    except NotFoundError:
        raise
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 — 映射为统一失败信封
        logger.error(f"Get public artifact content failed: {type(e).__name__}: {e}")
        raise AppError("读取分享工件内容失败") from e
