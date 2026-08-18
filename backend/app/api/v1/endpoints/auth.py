"""
派聪明AI知识库系统 - 认证端点
"""

import hashlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from app.models.user import User
from app.models.user_audit import UserLoginSession
from app.schemas.auth import ChangePasswordRequest, UpdateProfileRequest
from app.services.audit_service import audit_service
from app.services.auth_service import auth_service

logger = logging.getLogger(__name__)

router = APIRouter()

# OAuth2配置
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# 请求模型
class LoginRequest(BaseModel):
    username: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: str | None = None
    # 安全加固：禁止注册时自选组织（防越权加入任意组织读取文档），
    # 组织归属由管理员分配。

    @field_validator('password')
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError('密码长度不能少于8位')
        if len(v) > 128:
            raise ValueError('密码长度不能超过128位')
        return v

class RefreshRequest(BaseModel):
    refresh_token: str

class TokenData(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: str | None = None
    user_info: dict

class TokenResponse(BaseModel):
    success: bool = True
    message: str = "操作成功"
    data: TokenData

from app.schemas.user import UserInfoResponse


@router.get("/ensure-demo", response_model=dict)
async def ensure_demo():
    """创建或重置演示账号（仅开发模式开启）"""
    if not settings.ENABLE_ENSURE_DEMO_ENDPOINT:
        raise NotFoundError()
    from app.core.ensure_demo_user import ensure_demo_user
    await ensure_demo_user()
    return {"success": True, "message": "演示账号已就绪"}


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """用户登录 (form-data)"""
    try:
        logger.info(f"Login attempt - username: {form_data.username!r}, ip: {request.client.host if request.client else 'unknown'}")
        user = await auth_service.authenticate_user(
            db, form_data.username, form_data.password
        )

        if not user:
            raise AuthenticationError("用户名或密码错误")

        logger.info("Authentication successful. Proceeding to token generation.")
        # 2. 生成访问令牌
        access_token = auth_service.create_access_token(
            data={
                "sub": user.username,
                "user_id": user.id,
                "role": getattr(user, "role", None) or "user",
                "organization_id": getattr(user, "organization_id", None),
            }
        )
        logger.info("Access token generated.")

        # 安全加固：先创建登录会话，把 session_id 绑定进 refresh token，
        # 使"下线设备"能真正吊销对应 refresh token（会话置失效后刷新被拒）。
        session_id = None
        try:
            token_hash = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
            login_session = UserLoginSession(
                user_id=user.id,
                token_hash=token_hash,
                device_name=request.headers.get("sec-ch-ua-platform") or "Web",
                ip_address=(request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)),
                user_agent=request.headers.get("user-agent"),
                is_active=True,
            )
            db.add(login_session)
            await db.flush()
            session_id = login_session.id
        except Exception as db_err:
            logger.warning(f"Failed to record login session (non-critical): {db_err}")
            await db.rollback()

        # 3. 生成刷新令牌（携带 session_id）
        refresh_data: dict[str, Any] = {"sub": user.username, "user_id": user.id}
        if session_id is not None:
            refresh_data["session_id"] = session_id
        refresh_token = auth_service.create_refresh_token(data=refresh_data)
        logger.info("Refresh token generated.")

        # 5. 构建返回用的 user_info（避免 None 或不可序列化类型导致 500）
        logger.info("Constructing user_info.")
        user_info = {
            "id": user.id,
            "username": user.username,
            "email": user.email or "",
            "full_name": getattr(user, "full_name", None) or None,
            "organization_id": getattr(user, "organization_id", None),
            "role": user.role or "user",
            "preferences": getattr(user, "preferences", None),
        }
        # 确保可 JSON 序列化
        try:
            logger.info("Attempting to JSON serialize user_info.")
            user_json_str = json.dumps(jsonable_encoder(user_info))
            logger.info("user_info JSON serialization successful.")
        except Exception as ser_err:
            logger.warning(f"user_info 序列化失败，使用简化结构: {ser_err}")
            user_info = {k: v for k, v in user_info.items() if v is not None or k in ("id", "username", "email", "role")}
            user_info.setdefault("full_name", None)
            user_info.setdefault("organization_id", None)
            user_info.setdefault("preferences", None)
            user_json_str = json.dumps(user_info)
            logger.warning("user_info simplified and JSON serialization successful.")

        try:
            from app.core.redis import redis_client
            if redis_client:
                logger.info(f"Attempting to cache user info in Redis for user ID: {user.id}")
                await redis_client.setex(
                    f"user:{user.id}",
                    settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                    user_json_str,
                )
                logger.info("User info cached in Redis successfully.")
        except Exception as redis_err:
            logger.warning(f"Redis cache skip (login still success): {redis_err}")

        # 4. 审计登录成功（会话记录已在上方随 token 一起创建）
        try:
            await audit_service.log_activity(
                user_id=user.id,
                action="login",
                target_type="auth",
                target_id=str(user.id),
                detail="用户登录成功",
                request=request,
                db=db
            )
            await db.commit()
        except Exception as db_err:
            # 审计失败不影响登录成功，仅记录日志
            logger.warning(f"Failed to record login audit (non-critical): {db_err}")
            await db.rollback()

        # 6. 返回带 success 的结构
        logger.info("Preparing final successful login response.")
        return {
            "success": True,
            "message": "登录成功",
            "data": {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                "refresh_token": refresh_token,
                "user_info": user_info
            }
        }

    except (AuthenticationError, RateLimitError):
        raise
    except Exception as e:
        logger.exception("登录过程异常")
        raise AppError("登录失败", detail=str(e) if settings.EXPOSE_EXCEPTION_DETAIL else None)

from app.models.notification import Notification


@router.post("/login/json", response_model=TokenResponse)
async def login_json(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Login with JSON body."""
    try:
        logger.info(f"Login (JSON) attempt - username: {body.username!r}")
        user = await auth_service.authenticate_user(db, body.username, body.password)
        if not user:
            raise AuthenticationError("用户名或密码错误")
        access_token = auth_service.create_access_token(
            data={
                "sub": user.username,
                "user_id": user.id,
                "role": getattr(user, "role", None) or "user",
                "organization_id": getattr(user, "organization_id", None),
            }
        )
        # 安全加固：与 form login 一致，创建登录会话并绑定 session_id
        session_id = None
        try:
            token_hash = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
            login_session = UserLoginSession(
                user_id=user.id,
                token_hash=token_hash,
                device_name=request.headers.get("sec-ch-ua-platform") or "Web",
                ip_address=(request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)),
                user_agent=request.headers.get("user-agent"),
                is_active=True,
            )
            db.add(login_session)
            await db.flush()
            session_id = login_session.id
        except Exception as db_err:
            logger.warning(f"Failed to record login session (non-critical): {db_err}")
            await db.rollback()
        refresh_data: dict[str, Any] = {"sub": user.username, "user_id": user.id}
        if session_id is not None:
            refresh_data["session_id"] = session_id
        refresh_token = auth_service.create_refresh_token(data=refresh_data)
        user_info = {
            "id": user.id,
            "username": user.username,
            "email": user.email or "",
            "full_name": getattr(user, "full_name", None) or None,
            "organization_id": getattr(user, "organization_id", None),
            "role": getattr(user, "role", None) or "user",
            "preferences": getattr(user, "preferences", None),
        }
        try:
            user_json_str = json.dumps(jsonable_encoder(user_info))
        except Exception:
            user_info = {k: v for k, v in user_info.items() if k in ("id", "username", "email", "role") or v is not None}
            user_json_str = json.dumps(user_info)
        try:
            from app.core.redis import redis_client
            if redis_client:
                await redis_client.setex(
                    f"user:{user.id}",
                    settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                    user_json_str,
                )
        except Exception as redis_err:
            logger.warning(f"Redis cache skip: {redis_err}")
        return {
            "success": True,
            "message": "登录成功",
            "data": {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                "refresh_token": refresh_token,
                "user_info": user_info,
            },
        }
    except (AuthenticationError, RateLimitError):
        raise
    except Exception as e:
        logger.exception("login_json 异常")
        raise AppError("登录失败", detail=str(e) if settings.EXPOSE_EXCEPTION_DETAIL else None)


@router.post("/register", response_model=TokenResponse)
async def register(
    register_data: RegisterRequest,
    db: AsyncSession = Depends(get_db)
):
    """用户注册"""
    try:
        # 检查用户名是否已存在
        existing_user = await auth_service.get_user_by_username(
            db, register_data.username
        )

        if existing_user:
            raise ConflictError("用户名已存在")

        # 检查邮箱是否已存在
        existing_email = await auth_service.get_user_by_email(
            db, register_data.email
        )

        if existing_email:
            raise ConflictError("邮箱已被使用")

        # 创建新用户 + 欢迎通知
        # 使用 savepoint 是因为前面的查询已开启隐式事务
        async with db.begin_nested():
            user = await auth_service.create_user(
                db,
                username=register_data.username,
                email=register_data.email,
                password=register_data.password,
                full_name=register_data.full_name,
                organization_id=None  # 安全加固：注册用户不归属任何组织，由管理员分配
            )

            welcome_notification = Notification(
                user_id=user.id,
                title="欢迎加入 DocMind 知识库",
                content=f"亲爱的 {user.full_name or user.username}，欢迎使用 DocMind 企业级 RAG 知识库。您可以上传文档、构建知识库并开始智能问答。",
                is_read=False
            )
            db.add(welcome_notification)

        await db.commit()

        # 生成访问令牌 - 包含完整的用户信息和组织标签
        access_token = auth_service.create_access_token(
            data={
                "sub": user.username,
                "user_id": user.id,
                "role": user.role,
                "organization_id": user.organization_id,
                "username": user.username,
                "email": user.email
            }
        )

        # 生成刷新令牌
        refresh_token = auth_service.create_refresh_token(
            data={
                "sub": user.username,
                "user_id": user.id,
                "role": user.role,
                "organization_id": user.organization_id
            }
        )

        return {
            "success": True,
            "message": "注册成功",
            "data": {
                "access_token": access_token,
                "token_type": "bearer",
                "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                "refresh_token": refresh_token,
                "user_info": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "full_name": user.full_name,
                    "organization_id": user.organization_id,
                    "role": user.role
                }
            }
        }

    except ConflictError:
        raise
    except Exception as e:
        logger.error(f"注册失败: {e}", exc_info=True)
        raise AppError("注册失败", detail=str(e) if settings.EXPOSE_EXCEPTION_DETAIL else None)

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    """刷新访问令牌（含轮换与吊销校验）"""
    try:
        # 验证刷新令牌
        payload = auth_service.verify_token(body.refresh_token)

        if not payload or payload.get("type") != "refresh":
            raise AuthenticationError("无效的刷新令牌")

        # 安全加固：已被吊销的 refresh token 不得续期
        if await auth_service.is_token_blacklisted(body.refresh_token):
            raise AuthenticationError("刷新令牌已失效，请重新登录")

        # 安全加固：refresh token 绑定登录会话——"下线设备"后该会话被置失效，
        # 对应 refresh token 的续期必须被拒绝。
        session_id = payload.get("session_id")
        if session_id is not None:
            sess_result = await db.execute(
                select(UserLoginSession).where(UserLoginSession.id == int(session_id))
            )
            login_session = sess_result.scalar_one_or_none()
            if not login_session or not login_session.is_active:
                raise AuthenticationError("登录会话已失效，请重新登录")

        username = payload.get("sub")
        user_id = int(payload.get("user_id", 0))

        # 获取用户信息
        user = await auth_service.get_user_by_id(db, user_id)

        if not user or user.username != username:
            raise AuthenticationError("用户不存在")

        # 安全加固：被禁用用户不得刷新
        if not user.is_active:
            raise AuthenticationError("账号已被禁用")

        # 生成新的访问令牌（与登录 token 的 claims 保持一致）
        new_access_token = auth_service.create_access_token(
            data={
                "sub": username,
                "user_id": user_id,
                "role": user.role,
                "organization_id": user.organization_id,
            }
        )
        # 安全加固：refresh token 轮换——旧 refresh 立即吊销，签发新 refresh（保留 session_id）
        await auth_service.blacklist_token(body.refresh_token)
        new_refresh_data: dict[str, Any] = {"sub": username, "user_id": user_id}
        if session_id is not None:
            new_refresh_data["session_id"] = session_id
        new_refresh_token = auth_service.create_refresh_token(data=new_refresh_data)

        return {
            "success": True,
            "message": "刷新成功",
            "data": {
                "access_token": new_access_token,
                "token_type": "bearer",
                "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                "refresh_token": new_refresh_token,
                "user_info": {
                    "id": user.id,
                    "username": user.username,
                    "email": user.email,
                    "full_name": user.full_name,
                    "organization_id": user.organization_id,
                    "role": user.role
                }
            }
        }

    except AuthenticationError:
        raise
    except Exception as e:
        raise AppError("刷新令牌失败", detail=str(e) if settings.EXPOSE_EXCEPTION_DETAIL else None)

class LogoutRequest(BaseModel):
    refresh_token: str | None = None


@router.post("/logout", response_model=dict)
async def logout(
    body: LogoutRequest | None = None,
    token: str = Depends(oauth2_scheme),
):
    """用户登出：吊销 access token，若携带 refresh_token 一并吊销（防止登出后继续续期）"""
    await auth_service.blacklist_token(token)
    if body is not None and body.refresh_token:
        await auth_service.blacklist_token(body.refresh_token)
    return {"message": "登出成功"}

class UserInfoWrapper(BaseModel):
    success: bool = True
    message: str = "获取成功"
    data: UserInfoResponse

@router.get("/me", response_model=UserInfoWrapper)
async def get_current_user_info(
    current_user: User = Depends(get_current_user)
):
    """获取当前用户信息"""
    user_info = UserInfoResponse(
        id=current_user.id,
        username=current_user.username,
        email=current_user.email,
        full_name=current_user.full_name,
        organization_id=current_user.organization_id,
        role=current_user.role,
        is_active=current_user.is_active,
        created_at=current_user.created_at,
        preferences=current_user.preferences
    )
    return UserInfoWrapper(data=user_info)

@router.put("/me", response_model=UserInfoWrapper)
async def update_current_user(
    body: UpdateProfileRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新当前用户信息"""
    try:
        updated_user = await auth_service.update_user(
            db,
            current_user.id,
            full_name=body.full_name,
            email=body.email
        )
        await db.commit()

        user_info = UserInfoResponse.model_validate(updated_user)
        return UserInfoWrapper(data=user_info)

    except Exception as e:
        logger.error(f"更新用户信息失败: {e}", exc_info=True)
        raise AppError("更新用户信息失败", detail=str(e) if settings.EXPOSE_EXCEPTION_DETAIL else None)

@router.post("/change-password", response_model=dict)
async def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """修改密码"""
    try:
        # 安全加固：从 DB 重新加载用户校验旧密码（Redis 缓存不含 hashed_password，
        # 用缓存对象校验必然失败）
        db_user = await db.get(User, current_user.id)
        if db_user is None:
            raise ValidationError("用户不存在")
        if not auth_service.verify_password(body.old_password, db_user.hashed_password):
            raise ValidationError("旧密码错误")

        # 更新密码
        await auth_service.update_user_password(
            db,
            current_user.id,
            body.new_password
        )
        # 安全加固：改密后使用户缓存失效
        from app.core.redis import RedisTools
        await RedisTools.delete_cache(f"user:{current_user.id}")
        await db.commit()

        return {"message": "密码修改成功"}

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"修改密码失败: {e}", exc_info=True)
        raise AppError("修改密码失败", detail=str(e) if settings.EXPOSE_EXCEPTION_DETAIL else None)

