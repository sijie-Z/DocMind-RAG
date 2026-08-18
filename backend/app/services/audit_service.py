import logging

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.user_audit import UserActivityLog

logger = logging.getLogger(__name__)

def extract_client_ip(request: Request | None) -> str | None:
    """统一客户端 IP 提取：仅信任直连地址，不信任客户端可伪造的 X-Forwarded-For。

    若部署在受信反向代理后，请在代理层覆写该逻辑或配置代理白名单。
    """
    if request is None:
        return None
    return request.client.host if request.client else None


class AuditService:
    @staticmethod
    async def log_activity(
        user_id: int,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        detail: str | None = None,
        request: Request | None = None,
        db: AsyncSession | None = None
    ) -> None:
        """记录用户活动日志 — 始终使用独立 session，不干扰调用方事务"""
        try:
            ip_address = None
            user_agent = None

            if request:
                ip_address = extract_client_ip(request)
                user_agent = request.headers.get("user-agent")

            log_entry = UserActivityLog(
                user_id=user_id,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
                detail=detail,
                ip_address=ip_address,
                user_agent=user_agent
            )

            async with AsyncSessionLocal() as session:
                session.add(log_entry)
                await session.commit()

        except Exception as e:
            logger.error(f"Failed to log activity: {e}")

audit_service = AuditService()
