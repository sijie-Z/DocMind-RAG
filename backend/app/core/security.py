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
from app.models.rbac import (
    ORG_ADMIN_ROLE_NAME,
    PermissionType,
    Role,
    user_organization_role_association,
)
from app.models.user import User
from app.services.auth_service import auth_service
from app.services.permission_service import permission_service

if TYPE_CHECKING:
    from app.models.document import Document

logger = logging.getLogger(__name__)


async def get_current_user(
    current_user: User = Depends(auth_service.get_current_user), db: AsyncSession = Depends(get_db)
) -> User:
    return current_user


def require_scope(scope: str):
    """API Key scope 依赖（issue #480 §5.1）。

    JWT 用户没有 ``api_key_scopes`` 属性（``None``）→ 视为全量，沿用既有 RBAC；
    ``dm_`` key 必须显式包含所需 scope，否则 403 ``AUTHORIZATION_ERROR``。
    多个 scope 可叠加为多个依赖。
    """

    async def _checker(current_user: User = Depends(get_current_user)) -> User:
        scopes = getattr(current_user, "api_key_scopes", None)
        # 只有真实 API Key 认证会挂 list/tuple/set；缺失/其它类型（含测试替身）
        # 一律视为 JWT 用户 = 全量，避免把替身对象误判成受限 key。
        if isinstance(scopes, (list, tuple, set, frozenset)) and scope not in scopes:
            # AppError 走统一错误信封（error_code=AUTHORIZATION_ERROR）。
            raise AuthorizationError(f"API Key 缺少所需 scope: {scope}")
        return current_user

    return _checker


async def require_jwt_user(current_user: User = Depends(get_current_user)) -> User:
    """API Key 管理端点专用门禁：``dm_`` key 一律 403，只允许 JWT 会话（issue #496）。

    API Key 是「程序凭据」，管理 key 本身必须回到人（JWT）的会话里操作——
    否则一把泄露的 key 就能自行铸造/吊销其它 key。判定与 ``require_scope`` 同源：
    ``api_key_id`` 或任意容器类型的 ``api_key_scopes`` 只会在
    ``api_key_service.authenticate`` 成功时挂到 User 上；JWT 路径两者都不存在，
    纵深防御地同时检查两个标记，避免将来新增认证路径只挂其一时放行。
    """
    scopes = getattr(current_user, "api_key_scopes", None)
    if getattr(current_user, "api_key_id", None) is not None or isinstance(
        scopes, (list, tuple, set, frozenset)
    ):
        raise AuthorizationError("API Key 不能管理 API Key，请使用 JWT 登录会话操作")
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


def get_bound_api_key_org_id(current_user: User) -> int | None:
    """Return a concrete org-bound API key's org, never a mock sentinel.

    生产 API key id 是 UUID 字符串；精确 int/str 均可作为具体 id，空字符串、
    bool、MagicMock 等替身一律不是有效标记。key.org 必须是精确 int。
    """
    api_key_id = getattr(current_user, "api_key_id", None)
    key_org_id = getattr(current_user, "token_organization_id", None)
    if type(api_key_id) not in (str, int) or api_key_id == "":
        return None
    if type(key_org_id) is not int:
        return None
    return key_org_id


def get_user_org_id(current_user: User) -> int | None:
    """Resolve the effective organization for the current user.

    非空 org-bound API Key 始终使用认证时已挂载并校验的 key.org_id，即使用户
    后来切换 primary org 也不漂移。JWT 与 org-unbound key 保持原有 primary-first
    行为；JWT 的 token 回退只接受具体 int，MagicMock 不得成为组织上下文。
    """
    bound_key_org_id = get_bound_api_key_org_id(current_user)
    if bound_key_org_id is not None:
        return bound_key_org_id
    token_org_id = getattr(current_user, "token_organization_id", None)
    if type(token_org_id) is not int:
        token_org_id = None
    return current_user.organization_id or token_org_id


async def resolve_existing_organization_id(db: AsyncSession) -> int | None:
    """Return the id of the smallest real organization in the DB, or None.

    专供「无组织的超级管理员」解析一个**真实存在**的组织作为上下文。
    `organizations.id` 是自增主键，任何写死的编号都可能不存在
    （本库唯一组织的 id 是 3，1 号并不存在），因此必须回源数据库解析，
    而不是把某个编号硬编码进代码（issue #168）。
    """
    result = await db.execute(select(Organization.id).order_by(Organization.id).limit(1))
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
        raise AuthorizationError("系统当前不存在任何组织，无法确定组织上下文；请先创建组织后重试")
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


def is_platform_admin(user: User) -> bool:
    """平台级管理员：只认显式 ``is_superuser``。

    W0-1（ADR-001 D3）：``users.role == "admin"`` **不再**映射到全局 SysAdmin，
    也不得在任何端点里作为平台管理员判据。``PlatformAdminScope`` 抽象留给
    C1；本波次先收敛到唯一事实来源 ``is_superuser``。
    """
    return bool(getattr(user, "is_superuser", False))


def coerce_org_id(value: object) -> int | None:
    """把 path/query/form/JSON 里的组织 ID 归一化为 int；非法值 → None。

    非数字（例如把 ``document_id`` 误当组织参数的路径变量）一律 fail closed：
    不会带着任意字符串去查组织角色关联。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


async def is_org_admin(db: AsyncSession, user: User, organization_id: object) -> bool:
    """调用者是否为 ``organization_id`` 的组织管理员（统一判定入口）。

    - 平台超管旁路（``is_platform_admin``）；
    - 其余用户只认 ``user_organization_role_association`` 中该组织的
      ``OrgAdmin`` 角色；``users.role`` 不参与判定；
    - ``organization_id`` 非法/缺失 → False（fail closed）。
    """
    if is_platform_admin(user):
        return True
    org_id = coerce_org_id(organization_id)
    user_id = getattr(user, "id", None)
    if org_id is None or user_id is None:
        return False
    stmt = (
        select(user_organization_role_association.c.user_id)
        .join(
            Role,
            Role.id == user_organization_role_association.c.role_id,
        )
        .where(
            user_organization_role_association.c.user_id == user_id,
            user_organization_role_association.c.organization_id == org_id,
            Role.name == ORG_ADMIN_ROLE_NAME,
        )
    )
    return (await db.execute(stmt)).first() is not None


async def require_org_admin(db: AsyncSession, user: User, organization_id: object) -> None:
    """要求调用者是目标组织的管理员；否则 403（超管旁路）。"""
    if not await is_org_admin(db, user, organization_id):
        raise AuthorizationError("需要组织管理员权限")


def permission_required(
    required_permissions: list[PermissionType], organization_id_param: str = None
):
    async def check_permissions(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ):
        # 1. 上帝模式（优先判断超管）
        # `current_user` 由 `auth_service.get_current_user` 每次请求回源 DB 得到，
        # 本身就是最新状态 —— 不必、也不该在这里再查一次。那次二次查询是
        # 「缓存快照 vs DB」两套真相的来源之一：它只补救了超管位，其余字段
        # （is_active / role / organization_id）仍然可能是快照里的旧值。
        if current_user.is_superuser:
            return True

        # 2. 提取组织 ID
        # W0-1：组织角色权限只对「该组织」生效——参数来自 path/query/form/JSON
        # 时先归一化为 int；未显式给出组织上下文（如组织列表、用户管理）时，
        # 回退到调用者已校验过的有效组织，让 per-org 角色在其本组织内生效。
        # 非法值（如把 document_id 当组织参数）按「缺失」处理并同样回退到
        # 调用者本组织——绝不把任意字符串当组织 ID 查库；资源归属仍由端点
        # 自身的 get_document_for_user / _ensure_org_membership 二次校验。
        organization_id = None
        if organization_id_param:
            raw_org = request.path_params.get(organization_id_param)
            if raw_org is None:
                if request.method == "GET":
                    raw_org = request.query_params.get(organization_id_param)
                elif request.method == "POST":
                    try:
                        form = await request.form()
                        raw_org = form.get(organization_id_param)
                    except Exception:
                        pass
                    if raw_org is None:
                        try:
                            payload = await request.json()
                            if isinstance(payload, dict):
                                raw_org = payload.get(organization_id_param)
                        except Exception:
                            pass
            if raw_org is not None:
                organization_id = coerce_org_id(raw_org)
                if organization_id is None:
                    # 显式提供但非法的组织 ID：fail closed，不当作“未提供”回退。
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="组织上下文非法",
                    )

        if organization_id is None:
            organization_id = coerce_org_id(get_user_org_id(current_user))

        # 3. 获取用户权限集合
        # ✅ 修正：传递 user 对象，而不是 user.id
        user_permissions = await permission_service.get_user_permissions(
            db, current_user, organization_id
        )

        # 4. 校验权限名 (对比字符串)
        for perm in required_permissions:
            if perm.value not in user_permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=f"用户无此权限: {perm.value}"
                )
        return True

    return check_permissions
