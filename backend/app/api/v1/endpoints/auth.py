"""
派聪明AI知识库系统 - 认证端点
"""

import json
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import RedisTools
from app.models.user import User
from app.models.user_audit import UserLoginSession, UserActivityLog
from app.services.auth_service import AuthService
from app.services.audit_service import audit_service
from app.core.security import get_current_user

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
    full_name: Optional[str] = None
    organization_id: Optional[int] = None

class RefreshRequest(BaseModel):
    refresh_token: str

class TokenData(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_token: Optional[str] = None
    user_info: dict

class TokenResponse(BaseModel):
    success: bool = True
    message: str = "操作成功"
    data: TokenData

class UserInfoResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    nickname: Optional[str] = None
    phone: Optional[str] = None
    avatar: Optional[str] = None
    bio: Optional[str] = None
    organization_id: Optional[int] = None
    role: str
    is_active: Optional[bool] = True
    created_at: Optional[datetime] = None
    preferences: Optional[str] = None

# 服务实例
auth_service = AuthService()


@router.get("/ensure-demo")
async def ensure_demo():
    """创建或重置演示账号（仅开发模式开启）"""
    if not settings.ENABLE_ENSURE_DEMO_ENDPOINT:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found"
        )
    try:
        from app.core.ensure_demo_user import ensure_demo_user
        await ensure_demo_user()
        return {"success": True, "message": "演示账号已就绪", "data": {"username": "admin", "password": "123456"}}
    except Exception as e:
        import traceback
        logger.exception("ensure-demo 失败")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ensure-demo 失败: {type(e).__name__}: {str(e)}" if settings.EXPOSE_EXCEPTION_DETAIL else "ensure-demo 失败",
        )


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """用户登录"""
    try:
        logger.info(f"Login attempt - username: {form_data.username!r}, len(password): {len(form_data.password or '')}")
        # 1. 验证用户凭据
        user = await auth_service.authenticate_user(
            db, form_data.username, form_data.password
        )
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
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
        
        # 3. 生成刷新令牌
        refresh_token = auth_service.create_refresh_token(
            data={"sub": user.username, "user_id": user.id}
        )
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
            if redis_client is not None:
                logger.info(f"Attempting to cache user info in Redis for user ID: {user.id}")
                await redis_client.setex(
                    f"user:{user.id}",
                    settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                    user_json_str,
                )
                logger.info("User info cached in Redis successfully.")
        except Exception as redis_err:
            logger.warning(f"Redis cache skip (login still success): {redis_err}")

        # 4. 创建登录会话记录（允许失败，不影响登录）
        try:
            token_hash = hashlib.sha256(access_token.encode("utf-8")).hexdigest()
            db.add(UserLoginSession(
                user_id=user.id,
                token_hash=token_hash,
                device_name=request.headers.get("sec-ch-ua-platform") or "Web",
                ip_address=(request.headers.get("x-forwarded-for") or (request.client.host if request.client else None)),
                user_agent=request.headers.get("user-agent"),
                is_active=True,
            ))
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
            # 登录会话记录失败不影响登录成功，仅记录日志
            logger.warning(f"Failed to record login session (non-critical): {db_err}")
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
        
    except HTTPException:
        raise
    except BaseException as e:
        import traceback
        err_msg = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()
        logger.exception("登录过程异常")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_msg if settings.EXPOSE_EXCEPTION_DETAIL else "登录失败",
        )

from app.models.notification import Notification


@router.post("/login/json", response_model=TokenResponse)
async def login_json(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    使用 JSON 体的登录接口（与 /login 逻辑一致，用于排查 Form 与 JSON 差异）。
    请求体: {"username": "admin", "password": "123456"}
    """
    try:
        logger.info(f"Login (JSON) attempt - username: {body.username!r}, len(password): {len(body.password or '')}")
        user = await auth_service.authenticate_user(db, body.username, body.password)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
                headers={"WWW-Authenticate": "Bearer"},
            )
        access_token = auth_service.create_access_token(
            data={
                "sub": user.username,
                "user_id": user.id,
                "role": getattr(user, "role", None) or "user",
                "organization_id": getattr(user, "organization_id", None),
            }
        )
        refresh_token = auth_service.create_refresh_token(
            data={"sub": user.username, "user_id": user.id}
        )
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
            if redis_client is not None:
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
    except HTTPException:
        raise
    except BaseException as e:
        import traceback
        err_msg = f"{type(e).__name__}: {str(e)}"
        traceback.print_exc()
        logger.exception("login_json 异常")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=err_msg if settings.EXPOSE_EXCEPTION_DETAIL else "登录失败"
        )


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
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在"
            )
        
        # 检查邮箱是否已存在
        existing_email = await auth_service.get_user_by_email(
            db, register_data.email
        )
        
        if existing_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="邮箱已被使用"
            )
        
        # 创建新用户
        user = await auth_service.create_user(
            db,
            username=register_data.username,
            email=register_data.email,
            password=register_data.password,
            full_name=register_data.full_name,
            organization_id=register_data.organization_id
        )
        
        # 创建欢迎通知
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
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"注册失败: {str(e)}" if settings.EXPOSE_EXCEPTION_DETAIL else "注册失败"
        )

@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db)
):
    """刷新访问令牌"""
    try:
        # 验证刷新令牌
        payload = auth_service.verify_token(body.refresh_token)
        
        if not payload or payload.get("type") != "refresh":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的刷新令牌"
            )
        
        username = payload.get("sub")
        user_id = payload.get("user_id")
        
        # 获取用户信息
        user = await auth_service.get_user_by_id(db, user_id)  # pyright: ignore[reportArgumentType]
        
        if not user or user.username != username:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户不存在"
            )
        
        # 生成新的访问令牌
        new_access_token = auth_service.create_access_token(
            data={"sub": username, "user_id": user_id}
        )
        
        return {
            "success": True,
            "message": "刷新成功",
            "data": {
                "access_token": new_access_token,
                "token_type": "bearer",
                "expires_in": settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                "refresh_token": body.refresh_token,
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

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"刷新令牌失败: {str(e)}" if settings.EXPOSE_EXCEPTION_DETAIL else "刷新令牌失败"
        )

@router.post("/logout")
async def logout(
    token: str = Depends(oauth2_scheme)
):
    """用户登出"""
    try:
        # 将令牌加入黑名单
        await auth_service.blacklist_token(token)
        
        return {"message": "登出成功"}
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"登出失败: {str(e)}" if settings.EXPOSE_EXCEPTION_DETAIL else "登出失败"
        )

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
    full_name: Optional[str] = None,
    email: Optional[EmailStr] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新当前用户信息"""
    try:
        # 更新用户信息
        updated_user = await auth_service.update_user(
            db,
            current_user.id,
            full_name=full_name,
            email=email
        )
        
        user_info = UserInfoResponse(
            id=updated_user.id,
            username=updated_user.username,
            email=updated_user.email,
            full_name=updated_user.full_name,
            nickname=updated_user.full_name,
            phone=updated_user.phone,
            avatar=updated_user.avatar_url,
            bio=updated_user.bio,
            organization_id=updated_user.organization_id,
            role=updated_user.role,
            is_active=updated_user.is_active,
            created_at=updated_user.created_at
        )
        return UserInfoWrapper(data=user_info)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新用户信息失败: {str(e)}" if settings.EXPOSE_EXCEPTION_DETAIL else "更新用户信息失败"
        )

@router.post("/change-password")
async def change_password(
    old_password: str,
    new_password: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """修改密码"""
    try:
        # 验证旧密码
        if not auth_service.verify_password(old_password, current_user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="旧密码错误"
            )
        
        # 更新密码
        await auth_service.update_user_password(
            db,
            current_user.id,
            new_password
        )
        
        return {"message": "密码修改成功"}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"修改密码失败: {str(e)}" if settings.EXPOSE_EXCEPTION_DETAIL else "修改密码失败"
        )
        
