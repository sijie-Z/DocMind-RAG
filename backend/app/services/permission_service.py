"""
DocMind AI知识库系统 - 权限服务
"""

import logging

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.exceptions import AuthenticationError
from app.models.rbac import (
    ORG_ADMIN_ROLE_NAME,
    Permission,
    PermissionType,
    Role,
    user_organization_role_association,
)
from app.models.user import User

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# W0-1（ADR-001 D3）：组织管理员角色的权限集 —— 只覆盖本组织内的管理动作。
# 平台级权限（建/删组织、系统健康、系统提示词、删除用户等）故意不在此列：
# 平台级只认显式 is_superuser。
ORG_ADMIN_PERMISSION_NAMES = [
    PermissionType.VIEW_USER_DETAIL.value,
    PermissionType.MANAGE_USER_ROLES.value,
    PermissionType.VIEW_ORGANIZATION.value,
    PermissionType.UPDATE_ORGANIZATION.value,
    PermissionType.MANAGE_ORGANIZATION_MEMBERS.value,
    PermissionType.DOCUMENT_UPLOAD.value,
    PermissionType.DOCUMENT_READ.value,
    PermissionType.DOCUMENT_UPDATE.value,
    PermissionType.DOCUMENT_DELETE.value,
    PermissionType.VIEW_KNOWLEDGE_BASE.value,
    PermissionType.SEARCH_KNOWLEDGE_BASE.value,
    PermissionType.MANAGE_KNOWLEDGE_BASE.value,
    PermissionType.REBUILD_DOCUMENT_INDEX.value,
    PermissionType.VIEW_PROMPT.value,
    PermissionType.CREATE_PROMPT.value,
    PermissionType.UPDATE_PROMPT.value,
    PermissionType.DELETE_PROMPT.value,
]


class PermissionService:
    """权限服务类 - 提供RBAC核心逻辑"""

    async def get_user_permissions(
        self, db: AsyncSession, user: User, organization_id: int | None = None
    ) -> set[str]:
        """获取用户拥有的所有权限名称集合"""
        # 确保传入的是对象而不是ID
        if isinstance(user, int):
            user = await db.get(User, user)

        if user is None:
            # 纵深防御，两个来源都要挡住：
            #   1. 调用方直接把 None 传进来；
            #   2. 上面那次 `db.get` 查不到（用户已被删除）—— 传入 int 的分支。
            # 此前这两种情况都会一路走到 `user.is_superuser` 抛 AttributeError，
            # 被上游的 `except Exception` 包成 500；但「认证主体不存在」是 401，
            # 不是服务器内部错误（issue #82）。
            # 抛 `AuthenticationError`（AppError 子类）而不是 `HTTPException`：
            # `app.main` 的 AppError handler 会给 401 补上 RFC 9110 要求的
            # `WWW-Authenticate: Bearer`，与其它认证失败响应保持同一形状。
            raise AuthenticationError("认证主体不存在，无法解析权限")

        permissions_set = set()

        # 1. 超级管理员特权：直接返回所有已知权限
        if user.is_superuser:
            result = await db.execute(select(Permission.name))
            return set(result.scalars().all())

        # 2. 获取当前用户对应的系统级角色权限
        # W0-1（ADR-001 D3）：取消 `users.role == "admin"` → SysAdmin 的隐式
        # 映射。平台管理员只认显式 is_superuser（上面的特权分支）；其他用户
        # 一律只有 User 系统角色，额外的管理权限只能来自 per-org 角色关联。
        system_role_names = ["User"]
        system_roles_query = (
            select(Role)
            .where(and_(Role.is_system_role, Role.name.in_(system_role_names)))
            .options(selectinload(Role.permissions))
        )
        system_roles_result = await db.execute(system_roles_query)
        for role in system_roles_result.scalars().all():
            for perm in role.permissions:
                permissions_set.add(perm.name)

        # 3. 获取组织级角色权限
        if organization_id is not None:
            # path/query/form 传来的组织 ID 可能是字符串；非数字一律忽略
            # （fail closed，不拿任意字符串去查角色关联）。
            try:
                organization_id = int(organization_id)
            except (TypeError, ValueError):
                organization_id = None
        if organization_id is not None:
            user_org_roles_query = (
                select(Role)
                .join(
                    user_organization_role_association,
                    Role.id == user_organization_role_association.c.role_id,
                )
                .where(
                    and_(
                        user_organization_role_association.c.user_id == user.id,
                        user_organization_role_association.c.organization_id == organization_id,
                        # W0-1：系统级角色（SysAdmin/User）只能作为全局角色解析，
                        # 绝不允许通过组织角色关联表在某个组织内复活（否则等价于
                        # 恢复全局管理员）。
                        Role.is_system_role.is_(False),
                    )
                )
                .options(selectinload(Role.permissions))
            )

            user_org_roles_result = await db.execute(user_org_roles_query)
            for role in user_org_roles_result.scalars().all():
                for perm in role.permissions:
                    permissions_set.add(perm.name)

        return permissions_set

    async def get_or_create_org_admin_role(self, db: AsyncSession) -> Role:
        """返回 OrgAdmin 角色（不存在则创建），并补齐其权限关联。

        W0-1：组织管理员的唯一权威是 ``user_organization_role_association``
        里的 OrgAdmin 角色；启动初始化、迁移脚本与组织成员角色端点共用本方法。
        只 flush，不 commit——事务边界由调用方决定。
        """
        result = await db.execute(
            select(Role)
            .where(Role.name == ORG_ADMIN_ROLE_NAME)
            .options(selectinload(Role.permissions))
        )
        role = result.scalar_one_or_none()
        if role is None:
            role = Role(
                name=ORG_ADMIN_ROLE_NAME,
                description="组织管理员（组织级，仅限本组织）",
                is_system_role=False,
            )
            db.add(role)
            await db.flush()

        current = {perm.name for perm in role.permissions}
        missing = [name for name in ORG_ADMIN_PERMISSION_NAMES if name not in current]
        if missing:
            perms_result = await db.execute(select(Permission).where(Permission.name.in_(missing)))
            for perm in perms_result.scalars().all():
                role.permissions.append(perm)
        await db.flush()
        return role

    async def set_org_admin(
        self,
        db: AsyncSession,
        user_id: int,
        organization_id: int | None,
        enabled: bool,
    ) -> None:
        """授予/撤销用户在某组织的 OrgAdmin 角色（只 flush，不 commit）。"""
        if organization_id is None:
            return
        role = await self.get_or_create_org_admin_role(db)
        if enabled:
            existing = await db.execute(
                select(user_organization_role_association.c.user_id).where(
                    user_organization_role_association.c.user_id == user_id,
                    user_organization_role_association.c.organization_id == organization_id,
                    user_organization_role_association.c.role_id == role.id,
                )
            )
            if existing.first() is None:
                await db.execute(
                    user_organization_role_association.insert().values(
                        user_id=user_id,
                        organization_id=organization_id,
                        role_id=role.id,
                    )
                )
        else:
            await db.execute(
                user_organization_role_association.delete().where(
                    user_organization_role_association.c.user_id == user_id,
                    user_organization_role_association.c.organization_id == organization_id,
                    user_organization_role_association.c.role_id == role.id,
                )
            )
        await db.flush()

    async def initialize_default_permissions_and_roles(
        self, db: AsyncSession | None = None
    ) -> None:
        """初始化基础权限和角色数据。

        ``db`` 可选：启动路径不传（自建 AsyncSessionLocal）；测试/脚本可传入
        自己的会话（W0-1 矩阵测试与迁移种子测试复用同一份种子定义）。
        """
        if db is not None:
            await self._initialize_default_permissions_and_roles(db)
            return
        async with AsyncSessionLocal() as own_db:
            await self._initialize_default_permissions_and_roles(own_db)

    async def _initialize_default_permissions_and_roles(self, db: AsyncSession) -> None:
        logger.debug("开始初始化默认权限和角色...")

        # 1. 定义核心权限名 (必须与代码中 PermissionType 枚举的值完全一致)
        default_permissions_data = [
            {"name": PermissionType.VIEW_USER_DETAIL.value, "description": "查看用户详情"},
            {"name": PermissionType.MANAGE_USER_ROLES.value, "description": "管理用户角色"},
            {"name": PermissionType.DELETE_USER.value, "description": "删除用户"},
            {"name": PermissionType.VIEW_ORGANIZATION.value, "description": "查看组织"},
            {"name": PermissionType.CREATE_ORGANIZATION.value, "description": "创建组织"},
            {"name": PermissionType.UPDATE_ORGANIZATION.value, "description": "更新组织"},
            {"name": PermissionType.DELETE_ORGANIZATION.value, "description": "删除组织"},
            {
                "name": PermissionType.MANAGE_ORGANIZATION_MEMBERS.value,
                "description": "管理组织成员",
            },
            {"name": PermissionType.DOCUMENT_UPLOAD.value, "description": "上传文档"},
            {"name": PermissionType.DOCUMENT_READ.value, "description": "查看文档"},
            {"name": PermissionType.DOCUMENT_UPDATE.value, "description": "更新文档"},
            {"name": PermissionType.DOCUMENT_DELETE.value, "description": "删除文档"},
            {"name": PermissionType.VIEW_KNOWLEDGE_BASE.value, "description": "查看知识库"},
            {"name": PermissionType.SEARCH_KNOWLEDGE_BASE.value, "description": "检索知识库"},
            {"name": PermissionType.MANAGE_KNOWLEDGE_BASE.value, "description": "管理知识库"},
            {"name": PermissionType.REBUILD_DOCUMENT_INDEX.value, "description": "重建知识库索引"},
            {"name": PermissionType.VIEW_PROMPT.value, "description": "查看提示词"},
            {"name": PermissionType.CREATE_PROMPT.value, "description": "创建提示词"},
            {"name": PermissionType.UPDATE_PROMPT.value, "description": "更新提示词"},
            {"name": PermissionType.DELETE_PROMPT.value, "description": "删除提示词"},
            {"name": PermissionType.MANAGE_SYSTEM_PROMPTS.value, "description": "管理系统提示词"},
            {"name": PermissionType.VIEW_SYSTEM_HEALTH.value, "description": "查看系统健康"},
            {"name": PermissionType.USE_EMBEDDING_SERVICE.value, "description": "使用向量服务"},
        ]

        # 写入权限表
        for p_data in default_permissions_data:
            result = await db.execute(select(Permission).where(Permission.name == p_data["name"]))
            if not result.scalar_one_or_none():
                db.add(Permission(**p_data))
        await db.commit()

        # 加载所有权限对象供角色关联
        result = await db.execute(select(Permission))
        all_perms_map = {p.name: p for p in result.scalars().all()}

        # 2. 定义角色及其拥有的权限
        default_roles_data = [
            {
                "name": "SysAdmin",
                "description": "系统管理员",
                "is_system_role": True,
                "permissions": list(all_perms_map.keys()),  # 拥有所有权限
            },
            {
                "name": "User",
                "description": "普通用户",
                "is_system_role": True,
                "permissions": [
                    PermissionType.DOCUMENT_READ.value,
                    PermissionType.DOCUMENT_UPLOAD.value,
                    PermissionType.DOCUMENT_DELETE.value,
                    PermissionType.VIEW_KNOWLEDGE_BASE.value,
                    PermissionType.SEARCH_KNOWLEDGE_BASE.value,
                    PermissionType.VIEW_PROMPT.value,
                ],
            },
            # W0-1（ADR-001 D3）：组织管理员是 per-org 角色——只有
            # user_organization_role_association 关联成立时、且只在该组织内生效。
            {
                "name": ORG_ADMIN_ROLE_NAME,
                "description": "组织管理员（组织级，仅限本组织）",
                "is_system_role": False,
                "permissions": [
                    name for name in ORG_ADMIN_PERMISSION_NAMES if name in all_perms_map
                ],
            },
        ]

        # 写入角色并关联权限
        for r_data in default_roles_data:
            stmt = (
                select(Role)
                .where(Role.name == r_data["name"])
                .options(selectinload(Role.permissions))
            )
            result = await db.execute(stmt)
            role = result.scalar_one_or_none()

            if not role:
                role = Role(
                    name=r_data["name"],
                    description=r_data["description"],
                    is_system_role=r_data["is_system_role"],
                )
                db.add(role)
                await db.flush()

            await db.refresh(role, ["permissions"])

            # 同步权限关联
            current_perms = {p.name for p in role.permissions}
            for p_name in r_data["permissions"]:
                if p_name in all_perms_map and p_name not in current_perms:
                    role.permissions.append(all_perms_map[p_name])

        await db.commit()
        logger.info("默认权限和角色初始化完成。")


permission_service = PermissionService()
