"""
派聪明AI知识库系统 - 用户管理端点
"""

import csv
import io
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Request

logger = logging.getLogger(__name__)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import Date, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.notifications import create_notification
from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, permission_required
from app.exceptions import (
    AppError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document, DocumentStatus
from app.models.rbac import PermissionType
from app.models.user import User
from app.models.user_audit import UserActivityLog, UserLoginSession
from app.schemas.user import (
    UserAuditLogResponse,
    UserInfoResponse,
    UserSessionResponse,
    UserStatsResponse,
    UserUpdatePassword,
    UserUpdateProfile,
)
from app.services.auth_service import auth_service

router = APIRouter()


class ActivityResponse(BaseModel):
    id: str
    type: str
    content: str
    time: str


def _client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


async def _log_user_activity(
    db: AsyncSession,
    user_id: int,
    action: str,
    request: Request | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    detail: str | None = None,
):
    log = UserActivityLog(
        user_id=user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
        ip_address=_client_ip(request) if request else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    db.add(log)


# --- Endpoints ---

@router.get("/audit-logs", response_model=dict[str, Any], dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def get_audit_logs(
    skip: int = 0,
    limit: int = 50,
    user_id: int | None = None,
    action: str | None = None,
    search: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取审计日志列表（仅管理员）- 支持组织隔离
    """
    try:
        stmt = select(UserActivityLog)

        # 组织隔离：只查看同组织的日志
        if current_user.organization_id and not current_user.is_superuser:
            stmt = stmt.join(User).where(User.organization_id == current_user.organization_id)

        if user_id:
            stmt = stmt.where(UserActivityLog.user_id == user_id)
        if action:
            stmt = stmt.where(UserActivityLog.action == action)
        if search:
            stmt = stmt.where(
                or_(
                    UserActivityLog.action.ilike(f"%{search}%"),
                    UserActivityLog.detail.ilike(f"%{search}%")
                )
            )
        if start_date:
            stmt = stmt.where(UserActivityLog.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            stmt = stmt.where(UserActivityLog.created_at <= datetime.fromisoformat(end_date))

        # 统计总数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await db.execute(count_stmt)).scalar() or 0

        # 排序和分页
        stmt = stmt.order_by(UserActivityLog.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(stmt)
        logs = result.scalars().all()

        return {
            "success": True,
            "data": {
                "items": [
                    {
                        "id": log.id,
                        "user_id": log.user_id,
                        "username": log.user.username if log.user else "未知",
                        "action": log.action,
                        "target_type": log.target_type,
                        "target_id": log.target_id,
                        "detail": log.detail,
                        "ip_address": log.ip_address,
                        "created_at": log.created_at.isoformat()
                    } for log in logs
                ],
                "total": total
            }
        }
    except Exception as e:
        return {"success": False, "message": f"获取失败: {str(e)}"}


@router.get("/audit-logs/export", dependencies=[Depends(permission_required([PermissionType.VIEW_SYSTEM_HEALTH]))])
async def export_audit_logs(
    user_id: int | None = None,
    action: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """导出审计日志为 CSV 文件"""
    try:
        stmt = select(UserActivityLog)

        if user_id:
            stmt = stmt.where(UserActivityLog.user_id == user_id)
        if action:
            stmt = stmt.where(UserActivityLog.action == action)
        if start_date:
            stmt = stmt.where(UserActivityLog.created_at >= datetime.fromisoformat(start_date))
        if end_date:
            stmt = stmt.where(UserActivityLog.created_at <= datetime.fromisoformat(end_date))

        stmt = stmt.order_by(UserActivityLog.created_at.desc()).limit(10000)
        result = await db.execute(stmt)
        logs = result.scalars().all()

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Time', 'User ID', 'Username', 'Action', 'Target Type', 'Target ID', 'Detail', 'IP Address'])

        for log in logs:
            writer.writerow([
                log.created_at.isoformat() if log.created_at else '',
                log.user_id,
                log.user.username if log.user else 'Unknown',
                log.action or '',
                log.target_type or '',
                log.target_id or '',
                log.detail or '',
                log.ip_address or ''
            ])

        output.seek(0)
        filename = f"audit_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )
    except Exception as e:
        raise AppError(f"Export failed: {str(e)}")

@router.get("/me", response_model=dict[str, Any], summary="获取当前用户信息")
async def get_user_profile(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取当前登录用户信息"""
    try:
        # 1. 重新绑定用户到当前 Session
        current_user = await db.get(User, current_user.id)

        if not current_user:
            raise NotFoundError("User not found")

        # 2. 安全解析 preferences
        bio = None
        if current_user.preferences:
            try:
                pref_str = current_user.preferences.strip()
                if pref_str.startswith('{') or pref_str.startswith('['):
                    pref_json = json.loads(pref_str)
                    if isinstance(pref_json, dict):
                        bio = pref_json.get('bio', '')
                else:
                    bio = pref_str
            except Exception:
                bio = current_user.preferences

        # 3. 构造返回 (确保所有字段都存在)
        return {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "nickname": current_user.full_name or current_user.username,
            "phone": current_user.phone,
            "avatar_url": current_user.avatar_url,
            "bio": bio,
            "organization_id": current_user.organization_id,
            "role": current_user.role,
            "is_active": current_user.is_active,
            "created_at": current_user.created_at,
            "last_login_at": current_user.last_login_at,
            "preferences": current_user.preferences,
            "api_key": current_user.api_key
        }
    except Exception as e:
        logger.exception(f"Error in GET /me: {str(e)}")
        raise AppError(f"Get user profile failed: {str(e)}")


@router.get("/me/sessions", response_model=list[UserSessionResponse], summary="获取当前用户登录设备")
async def get_my_sessions(
    request: Request,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(UserLoginSession)
        .where(UserLoginSession.user_id == current_user.id)
        .order_by(UserLoginSession.last_seen_at.desc())
        .limit(50)
    )
    sessions = result.scalars().all()

    data = [
        {
            "id": s.id,
            "device_name": s.device_name,
            "ip_address": s.ip_address,
            "user_agent": s.user_agent,
            "is_active": s.is_active,
            "last_seen_at": s.last_seen_at,
            "created_at": s.created_at,
        }
        for s in sessions
    ]

    if not data:
        data.append({
            "id": 0,
            "device_name": request.headers.get("sec-ch-ua-platform") or "当前设备",
            "ip_address": (request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)),
            "user_agent": request.headers.get("user-agent"),
            "is_active": True,
            "last_seen_at": current_user.last_login_at,
            "created_at": current_user.last_login_at,
        })

    return data


@router.delete("/me/sessions/{session_id}", response_model=dict, summary="下线指定设备")
async def revoke_my_session(
    session_id: int,
    request: Request,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserLoginSession).where(
            UserLoginSession.id == session_id,
            UserLoginSession.user_id == current_user.id,
        )
    )
    session = result.scalar_one_or_none()
    if not session:
        raise NotFoundError("会话不存在")

    session_obj: Any = session
    session_obj.is_active = False
    session_obj.revoked_at = datetime.now()

    await _log_user_activity(
        db,
        current_user.id,
        action="session_revoked",
        request=request,
        target_type="session",
        target_id=str(session_id),
        detail="用户主动下线设备",
    )

    await db.commit()
    return {"message": "设备已下线"}


@router.get("/me/activity-logs", response_model=list[UserAuditLogResponse], summary="获取用户操作审计日志")
async def get_my_activity_logs(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(UserActivityLog)
        .where(UserActivityLog.user_id == current_user.id)
        .order_by(UserActivityLog.created_at.desc())
        .limit(100)
    )
    logs = result.scalars().all()
    return [
        {
            "id": l.id,
            "action": l.action,
            "target_type": l.target_type,
            "target_id": l.target_id,
            "detail": l.detail,
            "ip_address": l.ip_address,
            "created_at": l.created_at,
        }
        for l in logs
    ]


@router.get("/activities", response_model=list[ActivityResponse], summary="获取用户最近动态")
async def get_user_activities(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取用户最近动态（聚合）"""
    activities =[]

    # 1. 登录活动
    if current_user.last_login_at:
        activities.append({
            "id": "login_1",
            "type": "login",
            "content": "登录系统",
            "time": current_user.last_login_at,
            "timestamp": current_user.last_login_at.timestamp()
        })

    # 2. 文档上传活动
    doc_query = select(Document).where(
        Document.uploaded_by == current_user.id
    ).order_by(Document.created_at.desc()).limit(5)
    doc_result = await db.execute(doc_query)
    docs = doc_result.scalars().all()

    for doc in docs:
        activities.append({
            "id": f"doc_{doc.id}",
            "type": "upload",
            "content": f"上传了文档 \"{doc.filename}\"",
            "time": doc.created_at,
            "timestamp": doc.created_at.timestamp()
        })

    # 3. 聊天活动
    chat_query = select(ChatSession).where(
        ChatSession.user_id == current_user.id
    ).order_by(ChatSession.created_at.desc()).limit(5)
    chat_result = await db.execute(chat_query)
    chats = chat_result.scalars().all()

    for chat in chats:
        activities.append({
            "id": f"chat_{chat.id}",
            "type": "chat",
            "content": f"创建了新的对话 \"{chat.title[:20]}\"",
            "time": chat.created_at,
            "timestamp": chat.created_at.timestamp()
        })

    # 排序并取前10
    activities.sort(key=lambda x: x["timestamp"], reverse=True)
    activities = activities[:10]

    return[
        {
            "id": item["id"],
            "type": item["type"],
            "content": item["content"],
            "time": item["time"].isoformat() if isinstance(item["time"], datetime) else str(item["time"])
        }
        for item in activities
    ]

@router.get("/db-health", response_model=dict, summary="用户相关数据库健康自检")
async def user_db_health_check(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """检查用户模块关键表与字段是否可用，便于排查头像/会话/审计问题。"""
    checks = {
        "users_table": False,
        "users_avatar_url_column": False,
        "user_login_sessions_table": False,
        "user_activity_logs_table": False,
    }

    try:
        await db.execute(text("SELECT 1 FROM users LIMIT 1"))
        checks["users_table"] = True
    except Exception:
        pass

    try:
        await db.execute(text("SELECT avatar_url FROM users LIMIT 1"))
        checks["users_avatar_url_column"] = True
    except Exception:
        pass

    try:
        await db.execute(text("SELECT 1 FROM user_login_sessions LIMIT 1"))
        checks["user_login_sessions_table"] = True
    except Exception:
        pass

    try:
        await db.execute(text("SELECT 1 FROM user_activity_logs LIMIT 1"))
        checks["user_activity_logs_table"] = True
    except Exception:
        pass

    return {
        "ok": all(checks.values()),
        "checks": checks,
        "current_user_id": current_user.id,
    }


@router.get("/stats", response_model=UserStatsResponse, summary="获取用户统计信息")
async def get_user_stats(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取用户统计数据 (✅ 已修复知识库状态为 INDEXED 和消息总数统计)"""
    try:
        # 1. 会话数量
        conv_query = select(func.count(ChatSession.id)).where(ChatSession.user_id == current_user.id)
        conv_result = await db.execute(conv_query)
        conversation_count = conv_result.scalar() or 0

        # 2. 消息总数 (✅ 修复：通过关联 ChatMessage 直接统计真实消息数)
        msg_query = select(func.count(ChatMessage.id)).join(ChatSession).where(ChatSession.user_id == current_user.id)
        msg_result = await db.execute(msg_query)
        message_count = msg_result.scalar() or 0

        # 3. 文件数量
        file_query = select(func.count(Document.id)).where(Document.uploaded_by == current_user.id)
        file_result = await db.execute(file_query)
        file_count = file_result.scalar() or 0

        # 4. 知识库数量 (✅ 修复：状态由 COMPLETED 改为 INDEXED)
        kb_query = select(func.count(Document.id)).where(
            Document.uploaded_by == current_user.id,
            Document.status == DocumentStatus.INDEXED
        )
        kb_result = await db.execute(kb_query)
        knowledge_count = kb_result.scalar() or 0

        # 5. 存储使用量 (Bytes)
        storage_query = select(func.sum(Document.file_size)).where(Document.uploaded_by == current_user.id)
        storage_result = await db.execute(storage_query)
        storage_used = storage_result.scalar() or 0

        # 6. 获取最近7天的活动趋势 (保持不变)
        seven_days_ago = datetime.now() - timedelta(days=7)

        doc_trend_query = select(
            cast(Document.created_at, Date).label('date'),
            func.count(Document.id).label('total')
        ).where(
            Document.uploaded_by == current_user.id,
            Document.created_at >= seven_days_ago
        ).group_by(cast(Document.created_at, Date))
        doc_trend_result = await db.execute(doc_trend_query)
        doc_counts = {row.date: int(row.total) for row in doc_trend_result}

        chat_trend_query = select(
            cast(ChatSession.created_at, Date).label('date'),
            func.count(ChatSession.id).label('total')
        ).where(
            ChatSession.user_id == current_user.id,
            ChatSession.created_at >= seven_days_ago
        ).group_by(cast(ChatSession.created_at, Date))
        chat_trend_result = await db.execute(chat_trend_query)
        chat_counts = {row.date: int(row.total) for row in chat_trend_result}

        activity_trend =[]
        for i in range(6, -1, -1):
            day = (datetime.now() - timedelta(days=i)).date()
            count = doc_counts.get(day, 0) + chat_counts.get(day, 0)
            activity_trend.append(count)

        return {
            "conversation_count": conversation_count,
            "message_count": int(message_count),
            "file_count": file_count,
            "knowledge_count": knowledge_count,
            "storage_used": int(storage_used),
            "storage_limit": int(settings.USER_STORAGE_LIMIT_BYTES),
            "activity_trend": activity_trend
        }
    except Exception as e:
        logger.exception(f"Error in get_user_stats: {str(e)}")
        return {
            "conversation_count": 0, "message_count": 0, "file_count": 0,
            "knowledge_count": 0, "storage_used": 0, "storage_limit": int(settings.USER_STORAGE_LIMIT_BYTES), "activity_trend": [0] * 7
        }

@router.get("/", response_model=dict, summary="获取用户列表")
async def get_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    has_permission: bool = Depends(permission_required([PermissionType.VIEW_USER_DETAIL])),
    db: AsyncSession = Depends(get_db)
):
    """获取用户列表（管理员功能）- 仅显示当前组织下的用户"""
    try:
        # 组织隔离：只返回同一组织的用户
        if current_user.organization_id:
            base_filter = User.organization_id == current_user.organization_id
        else:
            base_filter = User.id == current_user.id

        total_result = await db.execute(select(func.count(User.id)).where(base_filter))
        total = total_result.scalar()

        result = await db.execute(
            select(User)
            .where(base_filter)
            .offset(skip)
            .limit(limit)
            .order_by(User.created_at.desc())
        )
        users = result.scalars().all()

        user_list =[]
        for user in users:
            user_list.append({
                "id": user.id, "username": user.username, "email": user.email,
                "full_name": user.full_name, "organization_id": user.organization_id,
                "role": user.role, "is_active": user.is_active,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None
            })

        return {
            "total": total, "skip": skip, "limit": limit, "users": user_list
        }

    except Exception as e:
        raise AppError(f"获取用户列表失败: {str(e)}")

@router.get("/{user_id}", response_model=dict, summary="获取用户信息")
async def get_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    has_permission: bool = Depends(permission_required([PermissionType.VIEW_USER_DETAIL]))
):
    """获取用户信息 - 仅允许查看同组织用户"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise NotFoundError("用户不存在")

    # 组织隔离：不允许查看其他组织的用户（除非是超级管理员）
    if not current_user.is_superuser and current_user.organization_id:
        if user.organization_id != current_user.organization_id:
            raise AuthorizationError("无权查看该用户信息")

    # 安全解析 bio
    bio_val = ""
    if user.preferences:
        try:
            pref = json.loads(user.preferences)
            bio_val = pref.get('bio', '') if isinstance(pref, dict) else ""
        except (json.JSONDecodeError, TypeError):
            bio_val = ""

    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "full_name": user.full_name,
        "nickname": user.full_name or user.username,
        "phone": user.phone,
        "avatar_url": user.avatar_url,
        "bio": bio_val,
        "organization_id": user.organization_id,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
        "last_login_at": user.last_login_at,
        "preferences": user.preferences
    }

@router.put("/{user_id}/role", response_model=dict, summary="修改用户角色")
async def update_user_role(
    user_id: int,
    role: str,
    current_user: User = Depends(get_current_user),
    has_permission: bool = Depends(permission_required([PermissionType.MANAGE_USER_ROLES])),
    db: AsyncSession = Depends(get_db)
):
    """修改用户角色（管理员功能）"""
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundError("用户不存在")

        if role not in ["user", "admin"]:
            raise ValidationError("无效的角色")

        if user.id == current_user.id:
            raise ValidationError("不能修改自己的角色")

        user_obj: Any = user
        user_obj.role = role
        await _log_user_activity(
            db,
            current_user.id,
            action="update_role",
            target_type="user",
            target_id=str(user_id),
            detail=f"将用户 {user.username} 的角色从 {user.role} 改为 {role}",
        )
        await db.commit()
        return {"message": "用户角色修改成功", "user_id": user_id, "new_role": role}
    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        raise AppError(f"修改用户角色失败: {str(e)}")

@router.delete("/{user_id}", response_model=dict, summary="删除用户")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    has_permission: bool = Depends(permission_required([PermissionType.DELETE_USER])),
    db: AsyncSession = Depends(get_db)
):
    """删除用户（管理员功能）"""
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise NotFoundError("用户不存在")
        if user.id == current_user.id:
            raise ValidationError("不能删除自己的账户")
        if user.is_superuser:
            raise AuthorizationError("不能删除超级管理员账户")

        user_to_deactivate: Any = user
        user_to_deactivate.is_active = False
        await _log_user_activity(
            db,
            current_user.id,
            action="delete_user",
            target_type="user",
            target_id=str(user_id),
            detail=f"删除用户 {user.username}",
        )
        await db.commit()
        return {"message": "用户删除成功", "user_id": user_id, "username": user.username}
    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        raise AppError(f"删除用户失败: {str(e)}")

@router.put("/me", response_model=UserInfoResponse, summary="更新个人信息")
async def update_user_profile(
    user_in: UserUpdateProfile,
    request: Request,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新当前登录用户信息 (✅ 修复了页面刷新导致无限发送通知的Bug)"""
    try:
        current_user = await db.merge(current_user)
        has_changed = False # 标志位：是否真正发生了数据变更

        # 对比字段变化
        current_user_obj: Any = current_user

        if user_in.full_name is not None and current_user_obj.full_name != user_in.full_name:
            current_user_obj.full_name = user_in.full_name
            has_changed = True

        # 邮箱不允许修改，避免重复问题
        # if user_in.email is not None and current_user.email != user_in.email:
        #     result = await db.execute(select(User).where(User.email == user_in.email))
        #     if result.scalar_one_or_none():
        #          raise ValidationError("Email already registered")
        #     current_user_obj.email = user_in.email
        #     has_changed = True

        if user_in.phone is not None and current_user.phone != user_in.phone:
            current_user_obj.phone = user_in.phone
            has_changed = True

        if user_in.avatar_url is not None and current_user.avatar_url != user_in.avatar_url:
            current_user_obj.avatar_url = user_in.avatar_url
            has_changed = True

        # 处理 preferences (包含 bio)
        current_pref_str = current_user.preferences
        pref_dict = {}
        try:
            if current_pref_str and (current_pref_str.strip().startswith('{') or current_pref_str.strip().startswith('[')):
                pref_dict = json.loads(current_pref_str)
                if not isinstance(pref_dict, dict):
                     pref_dict = {"bio": current_pref_str}
            elif current_pref_str:
                pref_dict = {"bio": current_pref_str}
        except (json.JSONDecodeError, TypeError):
            pref_dict = {"bio": current_pref_str} if current_pref_str else {}

        # 检查 bio 或 preferences 是否真的变化了
        if user_in.bio is not None and pref_dict.get("bio") != user_in.bio:
            pref_dict["bio"] = user_in.bio
            has_changed = True

        if user_in.preferences is not None:
            # 简单校验是否有新键值对（排除主题变更，主题变更不触发通知）
            for k, v in user_in.preferences.items():
                if k == 'theme':
                    # 主题变更只更新，不计入 has_changed
                    pref_dict[k] = v
                elif pref_dict.get(k) != v:
                    pref_dict[k] = v
                    has_changed = True

        if has_changed:
            current_user_obj.preferences = json.dumps(pref_dict, ensure_ascii=False)
            await create_notification(
                db,
                user_id=current_user.id,
                title="个人资料已更新",
                content="您的个人资料信息已成功更新。",
                type="account",
                target_route="Profile",
                target_id=str(current_user.id),
            )
            await _log_user_activity(
                db,
                current_user.id,
                action="profile_updated",
                request=request,
                target_type="user",
                target_id=str(current_user.id),
                detail="更新个人资料",
            )

        await db.commit()
        await db.refresh(current_user)

        bio_response = pref_dict.get('bio', '')

        return {
            "id": current_user.id, "username": current_user.username, "email": current_user.email,
            "full_name": current_user.full_name, "phone": current_user.phone, "avatar_url": current_user.avatar_url,
            "bio": bio_response, "organization_id": current_user.organization_id, "role": current_user.role,
            "is_active": current_user.is_active, "created_at": current_user.created_at,
            "last_login_at": current_user.last_login_at, "preferences": current_user.preferences,
            "api_key": current_user.api_key
        }
    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        raise AppError(f"更新个人信息失败: {str(e)}")

@router.put("/password", response_model=dict, summary="修改密码")
async def update_password(
    password_in: UserUpdatePassword,
    request: Request,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """修改当前用户密码"""
    try:
        if not auth_service.verify_password(password_in.old_password, current_user.hashed_password):
            raise ValidationError("旧密码错误")

        current_user_obj: Any = current_user
        current_user_obj.hashed_password = auth_service.hash_password(password_in.new_password)

        await create_notification(
            db,
            user_id=current_user.id,
            title="安全提醒：密码已修改",
            content="您的账户密码刚刚已修改成功。如果不是您本人操作，请立即联系管理员。",
            type="security",
            target_route="Profile",
            target_id=str(current_user.id),
        )
        await _log_user_activity(
            db,
            current_user.id,
            action="password_changed",
            request=request,
            target_type="user",
            target_id=str(current_user.id),
            detail="修改账户密码",
        )

        await db.commit()
        return {"message": "密码修改成功"}
    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        raise AppError(f"修改密码失败: {str(e)}")

@router.post("/api-key", response_model=dict, summary="生成新的 API Key")
async def generate_api_key(
    request: Request,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """为当前用户生成新的 API Key (如果已存在则覆盖)"""
    try:
        new_key = f"sk-{secrets.token_urlsafe(32)}"
        current_user_obj: Any = current_user
        current_user_obj.api_key = new_key

        await create_notification(
            db,
            user_id=current_user.id,
            title="API Key 已更新",
            content="您刚刚生成了新的 API Key，旧的 Key 已失效。",
            type="security",
            target_route="Profile",
            target_id=str(current_user.id),
        )
        await _log_user_activity(
            db,
            current_user.id,
            action="api_key_generated",
            request=request,
            target_type="api_key",
            target_id=str(current_user.id),
            detail="生成新的 API Key",
        )

        await db.commit()
        return {"api_key": new_key}
    except Exception as e:
        await db.rollback()
        raise AppError(f"生成 API Key 失败: {str(e)}")

@router.delete("/api-key", response_model=dict, summary="撤销 API Key")
async def revoke_api_key(
    request: Request,
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """撤销当前用户的 API Key"""
    try:
        current_user_obj: Any = current_user
        current_user_obj.api_key = None
        await _log_user_activity(
            db,
            current_user.id,
            action="api_key_revoked",
            request=request,
            target_type="api_key",
            target_id=str(current_user.id),
            detail="撤销 API Key",
        )
        await db.commit()
        return {"message": "API Key 已成功撤销"}
    except Exception as e:
        await db.rollback()
        raise AppError(f"撤销 API Key 失败: {str(e)}")

