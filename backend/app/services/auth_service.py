# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 认证服务
"""

import json # 确保有这个
import jwt
import bcrypt
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.encoders import jsonable_encoder # 确保有这个
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import RedisTools
from app.models.user import User
from app.services.organization_service import organization_service

logger = logging.getLogger(__name__)

# Add file handler for debugging (only once)
if not logger.handlers:
    fh = logging.FileHandler("auth_debug.log", encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.setLevel(logging.DEBUG)

# JWT认证方案
security = HTTPBearer()

class AuthService:
    """认证服务类"""
    
    def __init__(self):
        self.secret_key = settings.JWT_SECRET_KEY
        self.algorithm = settings.JWT_ALGORITHM
        self.access_token_expire_minutes = settings.ACCESS_TOKEN_EXPIRE_MINUTES
        self.refresh_token_expire_minutes = settings.REFRESH_TOKEN_EXPIRE_MINUTES
    
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """创建访问令牌"""
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.access_token_expire_minutes)
        
        to_encode.update({
            "exp": expire,
            "type": "access",
            "iat": datetime.utcnow()
        })
        
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def create_refresh_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """创建刷新令牌"""
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=self.refresh_token_expire_minutes)
        
        to_encode.update({
            "exp": expire,
            "type": "refresh",
            "iat": datetime.utcnow()
        })
        
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """验证令牌"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
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
        # 1. 先把对象转成字典，再转成 JSON 字符串
        user_dict = jsonable_encoder(user.to_dict())
        user_json_str = json.dumps(user_dict) 
        
        # 2. 存入 Redis (Redis 只吃字符串)
        await RedisTools.set_cache(
            f"user:{user.id}",
            user_json_str, 
            expire=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        )
        
        return user
    
    async def authenticate_user(self, db: AsyncSession, username: str, password: str) -> Optional[User]:
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
            logger.info(f"User found: {user.username}, verifying password...")
            is_valid = self.verify_password(password, user.hashed_password)  # pyright: ignore[reportArgumentType]
            
            if not is_valid:
                logger.warning(f"Login failed: Invalid password for user {username}")
                # DEBUG: Log hashes (be careful in production!)
                logger.warning(f"DEBUG ONLY - Input pass: {password}")
                logger.warning(f"DEBUG ONLY - Stored hash: {user.hashed_password}")
                return None
            
            # 检查用户状态
            if not user.is_active:  # pyright: ignore[reportGeneralTypeIssues]
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
    
    def require_role(self, required_role: str):
        """
        RBAC角色权限检查装饰器
        
        Args:
            required_role: 需要的角色 ('admin', 'user')
            
        Returns:
            依赖函数，用于FastAPI的Depends
        """
        async def role_checker(current_user: User = Depends(self.get_current_user)):
            if current_user.role != required_role:  # pyright: ignore[reportGeneralTypeIssues]
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"需要{required_role}权限"
                )
            return current_user
        
        return role_checker
    
    def require_admin(self):
        """需要管理员权限"""
        return self.require_role("admin")
    
    def require_user(self):
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
    
    async def blacklist_token(self, token: str):
        """将令牌加入黑名单"""
        try:
            # 解析令牌获取过期时间
            payload = self.verify_token(token)
            
            if payload and "exp" in payload:
                exp_timestamp = payload["exp"]
                current_timestamp = datetime.utcnow().timestamp()
                
                # 计算剩余有效期
                if exp_timestamp > current_timestamp:
                    ttl = int(exp_timestamp - current_timestamp)
                    
                    # 将令牌加入黑名单
                    blacklist_key = f"blacklist:{token}"
                    await RedisTools.set_cache(blacklist_key, "1", expire=ttl)
                    
                    logger.info(f"令牌已加入黑名单，有效期: {ttl}秒")
                    
        except Exception as e:
            logger.error(f"令牌黑名单处理失败: {str(e)}")
    
    async def is_token_blacklisted(self, token: str) -> bool:
        """检查令牌是否在黑名单中"""
        try:
            blacklist_key = f"blacklist:{token}"
            return await RedisTools.exists(blacklist_key)
        except Exception as e:
            logger.error(f"检查令牌黑名单失败: {str(e)}")
            return False
    
    async def get_user_by_username(self, db: AsyncSession, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        try:
            result = await db.execute(select(User).where(User.username == username))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"根据用户名获取用户失败: {str(e)}")
            return None
    
    async def get_user_by_email(self, db: AsyncSession, email: str) -> Optional[User]:
        """根据邮箱获取用户"""
        try:
            result = await db.execute(select(User).where(User.email == email))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"根据邮箱获取用户失败: {str(e)}")
            return None
    
    async def get_user_by_id(self, db: AsyncSession, user_id: int) -> Optional[User]:
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
        full_name: Optional[str] = None,
        organization_id: Optional[int] = None,
        role: str = "user"
    ) -> User:
        """创建用户"""
        try:
            # 哈希密码
            hashed_password = self.hash_password(password)
            
            # 创建用户
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
            await db.commit()
            await db.refresh(user)
            
            # # 自动创建私有组织标签
            # private_org = await organization_service.create_private_organization(db, user)
            
            # # 将用户添加到私有组织
            # await organization_service.add_user_to_organization(db, user, private_org)
            
            # # 更新用户的主组织
            # user.organization_id = private_org.id
            # await db.commit()
            
            logger.info(f"用户创建成功: {username}")
            return user
            
        except Exception as e:
            await db.rollback()
            logger.error(f"用户创建失败: {str(e)}")
            raise
    
    async def update_user(
        self,
        db: AsyncSession,
        user_id: int,
        username: Optional[str] = None,
        email: Optional[str] = None,
        full_name: Optional[str] = None,
        organization_id: Optional[int] = None
    ) -> User:
        """更新用户信息"""
        try:
            # 获取用户
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            
            if not user:
                raise ValueError("用户不存在")
            
            # 更新字段
            if username is not None:
                user.username = username  # pyright: ignore[reportAttributeAccessIssue]
            if email is not None:
                user.email = email  # pyright: ignore[reportAttributeAccessIssue]
            if full_name is not None:
                user.full_name = full_name  # pyright: ignore[reportAttributeAccessIssue]
            if organization_id is not None:
                user.organization_id = organization_id  # pyright: ignore[reportAttributeAccessIssue]
            
            user.updated_at = datetime.utcnow()  # pyright: ignore[reportAttributeAccessIssue]
            
            await db.commit()
            await db.refresh(user)
            
            logger.info(f"用户信息更新成功: {user.username}")
            return user
            
        except Exception as e:
            await db.rollback()
            logger.error(f"用户信息更新失败: {str(e)}")
            raise
    
    async def update_user_password(self, db: AsyncSession, user_id: int, new_password: str) -> User:
        """更新用户密码"""
        try:
            # 获取用户
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            
            if not user:
                raise ValueError("用户不存在")
            
            # 哈希新密码
            hashed_password = self.hash_password(new_password)
            
            # 更新密码
            user.hashed_password = hashed_password  # pyright: ignore[reportAttributeAccessIssue]
            user.updated_at = datetime.utcnow()  # pyright: ignore[reportAttributeAccessIssue]
            
            await db.commit()
            await db.refresh(user)
            
            logger.info(f"用户密码更新成功: {user.username}")
            return user
            
        except Exception as e:
            await db.rollback()
            logger.error(f"用户密码更新失败: {str(e)}")
            raise

# 👇👇👇 关键：创建单例实例
auth_service = AuthService()
