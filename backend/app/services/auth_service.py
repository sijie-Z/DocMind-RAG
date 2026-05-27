"""
派聪明AI知识库系统 - 认证服务
"""

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.encoders import jsonable_encoder  # 确保有这个
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import RedisTools
from app.models.user import User

logger = logging.getLogger(__name__)

# JWT认证方案
security = HTTPBearer()


class AuthService:
    """认证服务类"""

    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.access_token_expire_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
        self.refresh_token_expire_minutes = settings.REFRESH_TOKEN_EXPIRE_MINUTES

    def create_access_token(self, data: dict, expires_delta: timedelta | None = None) -> str:
        """Create access token with jti for precise revocation."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(UTC) + expires_delta
        else:
            expire = datetime.now(UTC) + timedelta(minutes=self.access_token_expire_minutes)
        to_encode.update({
            "exp": expire,
            "type": "access",
            "iat": datetime.now(UTC),
            "jti": uuid.uuid4().hex,
        })
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, data: dict, expires_delta: timedelta | None = None) -> str:
        """Create refresh token with jti for precise revocation."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(UTC) + expires_delta
        else:
            expire = datetime.now(UTC) + timedelta(minutes=self.refresh_token_expire_minutes)
        to_encode.update({
            "exp": expire,
            "type": "refresh",
            "iat": datetime.now(UTC),
            "jti": uuid.uuid4().hex,
        })
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def verify_token(self, token: str) -> dict[str, Any] | None:
        """验证令牌"""
        try:
            return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
        except jwt.ExpiredSignatureError:
            logger.warning("令牌已过期")
            return None
        except jwt.PyJWTError as e:
            logger.warning(f"令牌验证失败: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"令牌验证异常: {str(e)}")
            return None

    async def get_current_user(
        self,
        credentials: HTTPAuthorizationCredentials = Depends(security),
        db: AsyncSession = Depends(get_db)
    ) -> User:
        """获取当前用户"""
        token = credentials.credentials

        # 验证令牌
        payload = self.verify_token(token)

        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的认证令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 检查令牌是否已被吊销
        if await self.is_token_blacklisted(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="令牌已被吊销",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 检查令牌类型
        if payload.get("type") != "access":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的令牌类型",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 获取用户ID
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="令牌中缺少用户ID",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 从缓存获取用户信息
        cache_key = f"user:{user_id}"
        cached_user = await RedisTools.get_cache(cache_key)

        if cached_user:
            try:
                # 解析缓存的用户数据
                user_data = json.loads(cached_user)

                # 处理日期字段，将字符串转换回 datetime 对象
                for date_field in ['created_at', 'updated_at', 'last_login_at']:
                    if user_data.get(date_field):
                        user_data[date_field] = datetime.fromisoformat(user_data[date_field])

                # 过滤掉 User 模型中不存在的字段
                # 获取 User 模型的所有列名
                user_columns = {c.name for c in User.__table__.columns}
                filtered_data = {k: v for k, v in user_data.items() if k in user_columns}

                user = User(**filtered_data)
                # 将JWT中的权限信息添加到用户对象
                user.token_role = payload.get("role")
                user.token_organization_id = payload.get("organization_id")
                return user
            except Exception as e:
                logger.warning(f"从缓存恢复用户对象失败: {e}，将回退到数据库查询")
                # 如果缓存解析失败，删除该缓存
                await RedisTools.delete_cache(cache_key)

        # 从数据库获取用户信息
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户不存在"
            )

        # 将JWT中的权限信息添加到用户对象
        user.token_role = payload.get("role")
        user.token_organization_id = payload.get("organization_id")

        # 缓存用户信息
        from app.schemas.user import UserInfoResponse
        user_dict = jsonable_encoder(UserInfoResponse.model_validate(user).model_dump())
        user_json_str = json.dumps(user_dict)

        # 2. 存入 Redis (Redis 只吃字符串)
        await RedisTools.set_cache(
            f"user:{user.id}",
            user_json_str,
            expire=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )

        return user

    async def authenticate_user(self, db: AsyncSession, username: str, password: str) -> User | None:
        """验证用户凭据"""
        try:
            logger.info(f"Attempting login for user: {username}")

            # 查询用户
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()

            if user is None:
                logger.warning(f"Login failed: User {username} not found")
                return None

            # 验证密码
            is_valid = self.verify_password(password, user.hashed_password)

            if not is_valid:
                logger.warning(f"Login failed: Invalid password for user {username}")
                return None

            # 检查用户状态
            if not user.is_active:
                logger.warning(f"Login failed: User {username} is inactive")
                return None

            logger.info(f"Login success for user: {username}")
            return user

        except Exception as e:
            logger.exception(f"用户认证失败: {e}")
            raise

    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        if not plain_password or not hashed_password:
            logger.warning("密码或哈希为空")
            return False
        try:
            if isinstance(hashed_password, bytes):
                hashed_password = hashed_password.decode("utf-8")
            return bcrypt.checkpw(
                plain_password.encode("utf-8"),
                hashed_password.encode("utf-8"),
            )
        except Exception as e:
            logger.error(f"密码验证失败: {str(e)}")
            return False

    def require_role(self, required_role: str) -> Callable[..., Any]:
        """
        RBAC角色权限检查装饰器
        
        Args:
            required_role: 需要的角色 ('admin', 'user')
            
        Returns:
            依赖函数，用于FastAPI的Depends
        """
        async def role_checker(current_user: User = Depends(self.get_current_user)):
            if current_user.role != required_role:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"需要{required_role}权限"
                )
            return current_user

        return role_checker

    def require_admin(self) -> Callable[..., Any]:
        """需要管理员权限"""
        return self.require_role("admin")

    def require_user(self) -> Callable[..., Any]:
        """需要普通用户权限"""
        return self.require_role("user")

    def hash_password(self, password: str) -> str:
        """哈希密码"""
        try:
            salt = bcrypt.gensalt()
            hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
            return hashed.decode('utf-8')
        except Exception as e:
            logger.error(f"密码哈希失败: {str(e)}")
            raise

    async def blacklist_token(self, token: str) -> None:
        """Blacklist a token using its jti (JWT ID) for precise revocation."""
        try:
            payload = self.verify_token(token)
            if payload and "exp" in payload:
                exp_timestamp = payload["exp"]
                current_timestamp = datetime.now(UTC).timestamp()
                if exp_timestamp > current_timestamp:
                    ttl = int(exp_timestamp - current_timestamp)
                    jti = payload.get("jti")
                    if jti:
                        blacklist_key = f"blacklist:jti:{jti}"
                    else:
                        blacklist_key = f"blacklist:{hash(token)}"
                    await RedisTools.set_cache(blacklist_key, "1", expire=ttl)
                    logger.info(f"Token blacklisted (jti={jti}), ttl={ttl}s")
        except Exception as e:
            logger.error(f"Token blacklist failed: {e}")

    async def is_token_blacklisted(self, token: str) -> bool:
        """Check if a token is blacklisted by its jti."""
        try:
            payload = self.verify_token(token)
            if payload:
                jti = payload.get("jti")
                if jti:
                    return await RedisTools.exists(f"blacklist:jti:{jti}")
            return False
        except Exception:
            return False

    async def get_user_by_username(self, db: AsyncSession, username: str) -> User | None:
        """根据用户名获取用户"""
        try:
            result = await db.execute(select(User).where(User.username == username))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"根据用户名获取用户失败: {str(e)}")
            return None

    async def get_user_by_email(self, db: AsyncSession, email: str) -> User | None:
        """根据邮箱获取用户"""
        try:
            result = await db.execute(select(User).where(User.email == email))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"根据邮箱获取用户失败: {str(e)}")
            return None

    async def get_user_by_id(self, db: AsyncSession, user_id: int) -> User | None:
        """根据ID获取用户"""
        try:
            result = await db.execute(select(User).where(User.id == user_id))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"根据ID获取用户失败: {str(e)}")
            return None

    async def create_user(
        self,
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        full_name: str | None = None,
        organization_id: int | None = None,
        role: str = "user"
    ) -> User:
        """创建用户 — 不负责 commit，由调用方管理事务"""
        hashed_password = self.hash_password(password)

        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            organization_id=organization_id,
            role=role,
            is_active=True
        )

        db.add(user)
        await db.flush()
        await db.refresh(user)

        logger.info(f"用户创建成功: {username}")
        return user

    async def update_user(
        self,
        db: AsyncSession,
        user_id: int,
        username: str | None = None,
        email: str | None = None,
        full_name: str | None = None,
        organization_id: int | None = None
    ) -> User:
        """更新用户信息 — 不负责 commit，由调用方管理事务"""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("用户不存在")

        if username is not None:
            user.username = username
        if email is not None:
            user.email = email
        if full_name is not None:
            user.full_name = full_name
        if organization_id is not None:
            user.organization_id = organization_id

        user.updated_at = datetime.now(UTC)

        await db.flush()
        await db.refresh(user)

        logger.info(f"用户信息更新成功: {user.username}")
        return user

    async def update_user_password(self, db: AsyncSession, user_id: int, new_password: str) -> User:
        """更新用户密码 — 不负责 commit，由调用方管理事务"""
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if not user:
            raise ValueError("用户不存在")

        hashed_password = self.hash_password(new_password)
        user.hashed_password = hashed_password
        user.updated_at = datetime.now(UTC)

        await db.flush()
        await db.refresh(user)

        logger.info(f"用户密码更新成功: {user.username}")
        return user

# 👇👇👇 关键：创建单例实例
auth_service = AuthService()
