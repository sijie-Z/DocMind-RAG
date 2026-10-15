"""
DocMind AI知识库系统 - 安全工具
"""

# backend/app/core/security.py
import logging
from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.exceptions import AuthorizationError, NotFoundError
from app.models.organization import Organization
from app.models.rbac import PermissionType
from app.models.user import User
from app.services.auth_service import auth_service
from app.services.permission_service import permission_service

if TYPE_CHECKING:
    from app.models.document import Document

logger = logging.getLogger(__name__)


async def get_current_user(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
) -> User:
    return current_user


_optional_bearer = HTTPBearer(auto_error=False)


async def optional_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    """Return the current user if a valid bearer token was provided, else None."""
    if credentials is None:
        return None
    try:
        return await auth_service.get_current_user(credentials=credentials, db=db)
    except HTTPException:
        return None


def get_user_org_id(current_user: User) -> int | None:
    """Resolve the effective organization for the current user."""
    return current_user.organization_id or getattr(
        current_user, "token_organization_id", None
    )


async def resolve_existing_organization_id(db: AsyncSession) -> int | None:
    """Return the id of the smallest real organization in the DB, or None.

    专供「无组织的超级管理员」解析一个**真实存在**的组织作为上下文。
    `organizations.id` 是自增主键，任何写死的编号都可能不存在
    （本库唯一组织的 id 是 3，1 号并不存在），因此必须回源数据库解析，
    而不是把某个编号硬编码进代码（issue #168）。
    """
    result = await db.execute(
        select(Organization.id).order_by(Organization.id).limit(1)
    )
    return result.scalar_one_or_none()


async def require_organization(current_user: User, db: AsyncSession) -> int:
    """Resolve the caller's organization, failing closed when it is absent.

    S3 安全加固：此前多处使用 `current_user.organization_id or 1` 兜底，
    会把无组织用户静默归入 1 号组织，使其可读写其他租户的数据。
    现在无组织一律拒绝（403）。

    超级管理员是全局角色，无组织时仍需一个组织上下文（issue #168）：
    不再硬编码兜底到 1 号组织（`organizations.id` 是自增主键，1 号未必存在），
    而是解析库中**真实存在**的第一个组织，并把解析结果记入日志；
    一个组织都不存在时明确报错（403）——前置条件不满足，不是内部错误，
    绝不能把不存在的编号写进外键、等到写库时才炸成 500。
    """
    org_id = get_user_org_id(current_user)
    if org_id is not None:
        return int(org_id)
    if getattr(current_user, "is_superuser", False):
        fallback_org_id = await resolve_existing_organization_id(db)
        if fallback_org_id is not None:
            logger.info(
                f"超级管理员 {current_user.id} 无组织上下文，"
                f"已解析到库中真实存在的组织 {fallback_org_id}（issue #168）"
            )
            return int(fallback_org_id)
        logger.error(
            f"超级管理员 {current_user.id} 无组织上下文，且库中不存在任何组织，"
            "无法解析组织上下文，拒绝访问"
        )
        raise AuthorizationError(
            "系统当前不存在任何组织，无法确定组织上下文；请先创建组织后重试"
        )
    raise AuthorizationError("当前用户未加入任何组织，无权访问组织数据")


async def get_document_for_user(
    db: AsyncSession,
    current_user: User,
    document_id: str,
) -> "Document":
    """Load a document and enforce org-level access in one place.

    抛 `AppError` 子类（`NotFoundError` / `AuthorizationError`）而**不是** `HTTPException`：
    这是应用层语义错误，会被 `app/main.py:218` 的 `AppError` handler 转成正确的
    404/403。调用方本来就都在 `except (AppError, NotFoundError, AuthorizationError, ...)`
    里 `raise` 放行；抛 `HTTPException` 则会绕过它们、落进各自的 `except Exception`
    被包装成 `AppError("...失败")` → **500**（见 issue #90）。
    """
    from app.models.document import Document

    document = await db.get(Document, document_id)
    if not document:
        raise NotFoundError("文档不存在")

    # S3 安全加固：fail closed —— 无组织用户不再兜底到 1 号组织；
    # 非超管（含全局 role=admin 的租户管理员）只能访问本组织的文档。
    # 组织经由 get_user_org_id() 解析，因此兼容 token_organization_id 回退。
    if current_user.is_superuser:
        return document
    org_id = get_user_org_id(current_user)
    if org_id is None or document.organization_id != org_id:
        raise AuthorizationError("无权访问该文档")
    return document


def permission_required(required_permissions: list[PermissionType], organization_id_param: str = None):
    async def check_permissions(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ):
        # 1. 上帝模式（优先判断超管）
        # `current_user` 由 `auth_service.get_current_user` 每次请求回源 DB 得到，
        # 本身就是最新状态 —— 不必、也不该在这里再查一次。那次二次查询是
        # 「缓存快照 vs DB」两套真相的来源之一：它只补救了超管位，其余字段
        # （is_active / role / organization_id）仍然可能是快照里的旧值。
        if current_user.is_superuser:
            return True

        # 2. 提取组织 ID
        organization_id = None
        if organization_id_param:
            organization_id = request.path_params.get(organization_id_param)
            if organization_id is None:
                if request.method == "GET":
                    organization_id = request.query_params.get(organization_id_param)
                elif request.method == "POST":
                    try:
                        form = await request.form()
                        organization_id = form.get(organization_id_param)
                    except Exception:
                        pass
                    if organization_id is None:
                        try:
                            payload = await request.json()
                            if isinstance(payload, dict):
                                organization_id = payload.get(organization_id_param)
                        except Exception:
                            pass

        # 3. 获取用户权限集合
        # ✅ 修正：传递 user 对象，而不是 user.id
        user_permissions = await permission_service.get_user_permissions(db, current_user, organization_id)

        # 4. 校验权限名 (对比字符串)
        for perm in required_permissions:
            if perm.value not in user_permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"用户无此权限: {perm.value}"
                )
        return True
    return check_permissions
