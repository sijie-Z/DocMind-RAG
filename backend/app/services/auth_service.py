"""
DocMind AI知识库系统 - 认证服务
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.redis import RedisTools
from app.exceptions import AccountLockedError, ServiceUnavailableError
from app.models.organization import user_organization
from app.models.user import User

logger = logging.getLogger(__name__)

# ── 登录暴力破解防护 ─────────────────────────────────────────────
MAX_LOGIN_FAILURES = 5  # 连续失败次数阈值
LOGIN_LOCKOUT_SECONDS = 900  # 锁定窗口（15 分钟）
# 预计算一个 dummy bcrypt 哈希：用户不存在时也执行一次真实 bcrypt 校验，
# 消除"用户不存在 vs 密码错误"的时序差异，防止用户枚举。
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode("utf-8")

# ── 统一认证失败响应 ─────────────────────────────────────────────
# 「用户不存在」与「账号已被禁用」必须**不可区分**：OWASP Authentication Cheat Sheet 要求
# 应用对这两种情况返回同一个 generic 响应，否则构成 discrepancy factor —— 攻击者拿一个
# 过期 token 轮询即可区分「该 id 存在过但被封」与「从未存在」。
# 区分只发生在服务端日志里（logger.warning 的措辞），绝不进响应体。
# 上面的 _DUMMY_HASH 是同一原则在登录侧（时序）的实现。
_AUTH_FAILED_DETAIL = "认证失败"


def _auth_failed() -> HTTPException:
    """认证失败：用户不存在 / 账号被禁用 / 主体无法建立，三者共用的唯一响应。

    RFC 9110 规定 401 **MUST** 携带 `WWW-Authenticate`。注意 `app/main.py` 的
    HTTPException handler 原先丢弃了 `exc.headers`，使这里设置的响应头到不了客户端 ——
    已在同一次修复中改正。
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=_AUTH_FAILED_DETAIL,
        headers={"WWW-Authenticate": "Bearer"},
    )


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
        to_encode.update(
            {
                "exp": expire,
                "type": "access",
                "iat": datetime.now(UTC),
                "jti": uuid.uuid4().hex,
            }
        )
        return jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)

    def create_refresh_token(self, data: dict, expires_delta: timedelta | None = None) -> str:
        """Create refresh token with jti for precise revocation."""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.now(UTC) + expires_delta
        else:
            expire = datetime.now(UTC) + timedelta(minutes=self.refresh_token_expire_minutes)
        to_encode.update(
            {
                "exp": expire,
                "type": "refresh",
                "iat": datetime.now(UTC),
                "jti": uuid.uuid4().hex,
            }
        )
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
        db: AsyncSession = Depends(get_db),
    ) -> User:
        """获取当前用户"""
        token = credentials.credentials

        # API Key（issue #480 §5.1）：``dm_`` 前缀走独立分支；JWT 路径行为不变。
        # 旧 ``users.api_key``（``sk-`` 明文）没有认证消费者，继续拒绝。
        if token.startswith("dm_"):
            from app.services.api_key_service import api_key_service

            api_user = await api_key_service.authenticate(token, db)
            if api_user is None:
                raise _auth_failed()
            return api_user

        # 验证令牌
        payload = self.verify_token(token)

        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的认证令牌",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 检查令牌是否已被吊销（issue #291 fail-closed）：Redis 不可用时
        # `is_token_blacklisted` 抛 `ServiceUnavailableError` → 503，不降级放行。
        # 用 503 而不是 401：前端对 401 会清 token 跳登录页，而此时重新登录
        # 后的每个请求同样过不了这一步——只会制造 token 清空风暴。
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

        # ── 身份权威只有一个：数据库 ──────────────────────────────────
        # 这里刻意**不读** Redis。`user:{id}` 存的是 User 对象的**历史快照**，
        # 一旦它参与身份判定，就等于把「用户是谁」这个问题的答案冻结在 TTL
        # （默认 24h）之前：用户被删除/禁用/降权之后，只要缓存还在命中，旧身份
        # 就继续放行。清缓存是「尽力而为」的补救——漏删一处、删早一步（在
        # `db.commit()` 之前执行）、或并发回填，任何一条都能让快照复活，
        # 而这类竞态无法用测试穷举。所以正确的做法不是「记得删缓存」，
        # 而是让缓存根本不参与判定（issue #82）。
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user is None:
            # 服务端日志保留区分（user_not_found），响应体与「账号被禁用」完全不区分
            logger.warning(f"user_not_found: 令牌指向的用户 {user_id} 不存在")
            raise _auth_failed()

        # 安全加固：禁用账号即使 token 未过期也不得访问
        if not user.is_active:
            logger.warning(f"user_inactive: 用户 {user_id} 已被禁用，拒绝访问")
            raise _auth_failed()

        # 将JWT中的权限信息添加到用户对象
        user.token_role = payload.get("role")
        token_org_id = payload.get("organization_id")

        # A2 P0（租户边界）：token 中的 organization_id 只是**回退值**（core/security.py
        # 的 get_user_org_id 在用户主组织为空时使用它）。回退必须回源成员表校验：
        # 用户被移出组织后主组织字段清空、成员关系删除，旧 token 里的组织 claim
        # 立即失效（403），不等待 token 过期。主组织非空时以 DB 为准，不依赖成员表
        # （历史用户未必都有 user_organization 行，避免过度收紧）。
        if token_org_id is not None and user.organization_id is None:
            member_result = await db.execute(
                select(user_organization.c.user_id).where(
                    user_organization.c.user_id == user.id,
                    user_organization.c.organization_id == token_org_id,
                )
            )
            if member_result.first() is None:
                logger.warning(
                    f"token_org_membership_missing: 用户 {user_id} 的令牌组织 "
                    f"{token_org_id} 已无成员关系，拒绝访问"
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="您已不在该组织中，请重新登录",
                )
        user.token_organization_id = token_org_id

        return user

    async def _check_lockout(self, username: str) -> None:
        """检查登录失败锁定状态；Redis 不可用时跳过（全局限流仍在）。"""
        try:
            from app.core.redis import redis_client

            if not redis_client:
                return
            key = f"login:fail:{username}"
            count = await redis_client.get(key)
            if count and int(count) >= MAX_LOGIN_FAILURES:
                raise AccountLockedError(
                    message=f"登录尝试次数过多，请 {LOGIN_LOCKOUT_SECONDS // 60} 分钟后再试"
                )
        except AccountLockedError:
            raise
        except Exception as e:
            logger.warning(f"Lockout check failed (skip): {e}")

    async def _record_login_failure(self, username: str) -> None:
        """记录一次登录失败；达到阈值后抛出 AccountLockedError。"""
        try:
            from app.core.redis import redis_client

            if not redis_client:
                return
            key = f"login:fail:{username}"
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, LOGIN_LOCKOUT_SECONDS)
            if count >= MAX_LOGIN_FAILURES:
                raise AccountLockedError(
                    message=f"登录尝试次数过多，请 {LOGIN_LOCKOUT_SECONDS // 60} 分钟后再试"
                )
        except AccountLockedError:
            raise
        except Exception as e:
            logger.warning(f"Record login failure failed (skip): {e}")

    async def _clear_login_failures(self, username: str) -> None:
        """登录成功后清除失败计数。"""
        try:
            from app.core.redis import redis_client

            if redis_client:
                await redis_client.delete(f"login:fail:{username}")
        except Exception as e:
            logger.warning(f"Clear login failures failed (skip): {e}")

    async def authenticate_user(
        self, db: AsyncSession, username: str, password: str
    ) -> User | None:
        """验证用户凭据（含失败锁定与时序侧信道防护）"""
        try:
            # 安全加固：失败锁定检查
            await self._check_lockout(username)
            logger.info(f"Attempting login for user: {username}")

            # 查询用户
            result = await db.execute(select(User).where(User.username == username))
            user = result.scalar_one_or_none()

            if user is None:
                # 安全加固：用户不存在时也执行一次 bcrypt 校验，消除时序差异（防用户枚举）。
                # bcrypt 是 CPU 密集的同步调用，放入线程池避免阻塞事件循环（issue #500）。
                await asyncio.to_thread(self.verify_password, password, _DUMMY_HASH)
                await self._record_login_failure(username)
                logger.warning(f"Login failed: User {username} not found")
                return None

            # 验证密码（bcrypt 在线程池执行，issue #500）
            is_valid = await asyncio.to_thread(self.verify_password, password, user.hashed_password)

            if not is_valid:
                await self._record_login_failure(username)
                logger.warning(f"Login failed: Invalid password for user {username}")
                return None

            # 检查用户状态
            if not user.is_active:
                logger.warning(f"Login failed: User {username} is inactive")
                return None

            # 安全加固：登录成功清除失败计数
            await self._clear_login_failures(username)
            logger.info(f"Login success for user: {username}")
            return user

        except AccountLockedError:
            raise
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
        RBAC角色权限检查装饰器（遗留 API）。

        W0-1（ADR-001 D3）：``admin`` 不再映射到 ``users.role`` —— 平台管理员
        只认显式 ``is_superuser``；组织管理员应使用 ``core.security`` 的
        ``require_org_admin``（per-org 组织角色）。保留本方法仅为兼容既有
        调用方与测试，不得再用它构建新的管理员门禁。
        """

        async def role_checker(current_user: User = Depends(self.get_current_user)):
            if required_role == "admin":
                if not bool(getattr(current_user, "is_superuser", False)):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="需要平台管理员权限",
                    )
                return current_user
            if (current_user.role or "") != required_role:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=f"需要{required_role}权限"
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
            hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
            return hashed.decode("utf-8")
        except Exception as e:
            logger.error(f"密码哈希失败: {str(e)}")
            raise

    async def blacklist_token(self, token: str) -> None:
        """Blacklist a token using its jti (JWT ID) for precise revocation.

        写入失败**如实上抛** `ServiceUnavailableError`（issue #291）：此前整个
        异常被吞掉，调用方以为吊销成功——登出端点照常回 200「登出成功」、
        refresh 轮换默默放行旧 token，而它们实际都没进黑名单。

        过期/无效 token 保持 no-op：本地验签失败说明该 token 不可能再通过
        `get_current_user`/refresh/WS 的任何一处校验，没有可吊销的东西——
        这不是失败，调用方不应据此报错误。
        """
        payload = self.verify_token(token)
        if not payload or "exp" not in payload:
            return
        exp_timestamp = payload["exp"]
        current_timestamp = datetime.now(UTC).timestamp()
        if exp_timestamp <= current_timestamp:
            return
        ttl = int(exp_timestamp - current_timestamp)
        jti = payload.get("jti")
        if jti:
            blacklist_key = f"blacklist:jti:{jti}"
        else:
            blacklist_key = f"blacklist:{hash(token)}"
        try:
            await RedisTools.set_cache(blacklist_key, "1", expire=ttl)
        except Exception as e:
            logger.error(f"Token blacklist failed: {e}")
            raise ServiceUnavailableError(
                "服务端令牌吊销失败",
                detail="吊销服务（Redis）暂不可用，令牌未能加入吊销名单，请稍后重试",
            ) from e
        logger.info(f"Token blacklisted (jti={jti}), ttl={ttl}s")

    async def is_token_blacklisted(self, token: str) -> bool:
        """Check if a token is blacklisted by its jti.

        查询失败**如实上抛** `ServiceUnavailableError`（issue #291），fail-closed：
        此前 `except Exception: return False` 把「Redis 查不了」归成「不在黑名单」，
        与「未吊销」同形——吊销检查形同虚设，已登出/已轮换的 token 在 Redis
        故障期间全部照常放行。判不了就不放行。

        无效/过期/无 jti 的 token 返回 False（不可能是吊销对象）。
        """
        payload = self.verify_token(token)
        if not payload:
            return False
        jti = payload.get("jti")
        if not jti:
            return False
        try:
            return await RedisTools.exists(f"blacklist:jti:{jti}")
        except Exception as e:
            logger.error(f"Token blacklist check failed: {e}")
            raise ServiceUnavailableError(
                "令牌吊销状态查询失败",
                detail="吊销服务（Redis）暂不可用，无法核验令牌，请稍后重试",
            ) from e

    async def get_user_by_username(self, db: AsyncSession, username: str) -> User | None:
        """根据用户名获取用户。

        查无此人返回 None；**查询本身失败时异常冒泡**（issue #103 第 2 条，
        与 `get_user_by_id` 同族）：`except Exception: return None` 会把
        「DB 故障」伪装成「查不到」，调用方无从区分。username 列有唯一约束，
        「查不到」永远不会导致重复写入，无需用吞异常来兜底。
        """
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_user_by_email(self, db: AsyncSession, email: str) -> User | None:
        """根据邮箱获取用户。

        查无此人返回 None；**查询本身失败时异常冒泡**（issue #103 第 2 条，
        与 `get_user_by_id` 同族）。email 列有唯一约束，理由同上。
        """
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_user_by_id(self, db: AsyncSession, user_id: int) -> User | None:
        """根据ID获取用户。

        查无此人返回 None；**查询本身失败时异常冒泡**（issue #103 第 2 条）。
        此前的 `except Exception: return None` 会把 DB 故障伪装成「用户不存在」，
        经 /auth/refresh 映射成 401 —— 前端收到后清 token 跳登录页，用户看到
        「登录已过期」，真实原因是数据库挂了。`scalar_one_or_none()` 对「查无
        此人」本来就走 None，语义天然可区分，异常必须留给 5xx。
        """
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def create_user(
        self,
        db: AsyncSession,
        username: str,
        email: str,
        password: str,
        full_name: str | None = None,
        organization_id: int | None = None,
        role: str = "user",
    ) -> User:
        """创建用户 — 不负责 commit，由调用方管理事务"""
        # bcrypt 在线程池执行，避免注册路径阻塞事件循环（issue #500）
        hashed_password = await asyncio.to_thread(self.hash_password, password)

        user = User(
            username=username,
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            organization_id=organization_id,
            role=role,
            is_active=True,
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
        organization_id: int | None = None,
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

        # bcrypt 在线程池执行，避免改密路径阻塞事件循环（issue #500）
        hashed_password = await asyncio.to_thread(self.hash_password, new_password)
        user.hashed_password = hashed_password
        user.updated_at = datetime.now(UTC)

        await db.flush()
        await db.refresh(user)

        logger.info(f"用户密码更新成功: {user.username}")
        return user


# 👇👇👇 关键：创建单例实例
auth_service = AuthService()
