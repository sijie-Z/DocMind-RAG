"""
DocMind AI知识库系统 - 组织服务
"""

import logging
from typing import Any, cast

from sqlalchemy import and_, delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import is_platform_admin
from app.exceptions import (
    AppError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ServiceUnavailableError,
)
from app.models.agent_task import AgentTask
from app.models.chat import ChatSession
from app.models.document import Document
from app.models.knowledge_job import KnowledgeProcessingJob
from app.models.organization import Organization, user_organization
from app.models.organization_settings import OrganizationSettings
from app.models.prompt import PromptTemplate
from app.models.rbac import ORG_ADMIN_ROLE_NAME, Role, user_organization_role_association
from app.models.scheduled_task import ScheduledTask
from app.models.token_usage import TokenUsageRecord
from app.models.user import User
from app.models.workflow import Workflow

logger = logging.getLogger(__name__)


class OrganizationService:
    """组织服务类 - 提供企业级组织架构管理功能"""

    async def get_organization_tree(self, db: AsyncSession, user: User) -> list[dict]:
        """
        获取组织树结构

        Args:
            db: 数据库会话
            user: 当前用户对象 (User)

        Returns:
            树形结构的组织列表

        Raises:
            AuthenticationError: `user` 为 None —— 认证主体不存在，可见范围无从判定（issue #82）。
        """
        # 认证主体不存在 → 401。原先直接 `user.is_superuser` 会 AttributeError，
        # 而下面的 `except Exception` 会把它吞成「空树」，于是**认证失败被伪装成
        # 一次成功的空查询**。注意这个判断必须在 `try` 之外：放进 try 里，
        # 同样会被那个兜底 except 吞掉，等于没抛。
        if user is None:
            logger.warning("get_organization_tree 收到 user=None，拒绝返回组织树")
            raise AuthenticationError()

        try:
            # 1. 获取所有组织
            # 这里可以利用刚才设置的超管特权
            if is_platform_admin(user):
                # 超管看到完整的树
                stmt = (
                    select(Organization)
                    .options(selectinload(Organization.children))
                    .order_by(Organization.level, Organization.sort_order)
                )
            else:
                # W0-2：组织树是跨租户信息面——非平台管理员的可见集合只能由
                # **关系**推导（成员表 / 组织角色关联 / 主组织），绝不按
                # `is_private` 等组织属性枚举其他租户。可见集合为空 = 无组织
                # 上下文，fail closed（403），不用空树冒充成功。
                visible_ids = await self.resolve_visible_organization_ids(db, user)
                if not visible_ids:
                    raise AuthorizationError("当前账号无组织上下文，无法查看组织树")
                visible_result = await db.execute(
                    select(Organization)
                    .where(Organization.id.in_(sorted(visible_ids)))
                    .order_by(Organization.level, Organization.sort_order, Organization.id)
                )
                # 只在这批可见节点内构建森林：父节点不可见时该节点作为根，
                # 不向上泄露不可见组织（含其名称）。
                return self._build_visible_forest(visible_result.scalars().all())

            result = await db.execute(stmt)
            all_orgs = result.scalars().all()

            # 2. 构建树形结构 (后续逻辑不变)
            org_map = {org.id: self._org_to_dict(org) for org in all_orgs}
            tree = []

            for org in all_orgs:
                org_dict = org_map[org.id]
                if org.parent_id is None:
                    tree.append(org_dict)
                else:
                    parent = org_map.get(org.parent_id)
                    if parent:
                        if "children" not in parent:
                            parent["children"] = []
                        parent["children"].append(org_dict)

            return tree

        except AppError:
            # W0-2：无组织 403 等业务异常必须原样冒泡，不能被下面的
            # `except Exception` 降级成 503。
            raise
        except Exception as e:
            # issue #301（批 C）：查询失败不再回落空树 —— 空树在端点层会被
            # 包装成 {"success": true, "data": []} 的 200，像「组织全被删」。
            # 组织树无降级语义（结构本身即全部交付物）：显式 503 冒泡，
            # 组织管理页（organizations/index.vue）已有「加载失败 + 重试」错误态
            # 与 X-Silent-Error 静默头，正好承接此语义。
            logger.error(f"获取组织树失败: {str(e)}")
            raise ServiceUnavailableError("组织架构查询失败，请稍后重试") from e

    def _build_visible_forest(self, orgs: list[Organization]) -> list[dict]:
        """W0-2：只在这批可见节点内构建森林。

        父节点不在可见集合时，该节点作为根返回——不得为了“树形完整”把
        不可见的父组织补进来（那会把其他租户的组织名泄露给调用方）。
        """
        org_map = {org.id: self._org_to_dict(org) for org in orgs}
        tree: list[dict] = []
        for org in orgs:
            node = org_map[org.id]
            parent = org_map.get(org.parent_id)
            if parent is None:
                # 父组织不可见：连 parent_id 也不返回——不可见组织的 ID 同样
                # 属于其元数据；该节点直接作为森林的根。
                node["parent_id"] = None
                tree.append(node)
            else:
                parent["children"].append(node)
        return tree

    async def resolve_visible_organization_ids(
        self, db: AsyncSession, user: User
    ) -> set[int] | None:
        """W0-2：返回调用者可见的组织 ID 集合；平台管理员返回 ``None`` 哨兵。

        非平台管理员的可见集合是**关系**的并集，不是组织属性：
        - ``user_organization`` 成员关系（成员表是权威来源）；
        - ``user_organization_role_association`` 组织角色关联（per-org 角色，
          例如 OrgAdmin；即使成员行缺失，被授予组织角色也说明存在关系）；
        - ``users.organization_id`` 主组织兜底（历史用户可能没有成员行，
          与 ``GET /users/me/organizations`` 的兼容口径一致；只增不减，
          不影响“无组织 → 空集合 → 403”的判定）。

        绝不使用 ``Organization.is_private`` 之类的组织属性做人可见性过滤。
        """
        if getattr(user, "is_superuser", False):
            return None

        visible: set[int] = set()
        user_id = getattr(user, "id", None)
        if user_id is not None:
            member_rows = await db.execute(
                select(user_organization.c.organization_id).where(
                    user_organization.c.user_id == user_id
                )
            )
            visible.update(
                int(org_id) for org_id in member_rows.scalars().all() if org_id is not None
            )
            role_rows = await db.execute(
                select(user_organization_role_association.c.organization_id).where(
                    user_organization_role_association.c.user_id == user_id
                )
            )
            visible.update(
                int(org_id) for org_id in role_rows.scalars().all() if org_id is not None
            )

        primary_org_id = getattr(user, "organization_id", None)
        if primary_org_id is not None:
            visible.add(int(primary_org_id))
        return visible

    async def resolve_managed_organization_ids(self, db: AsyncSession, user: User) -> set[int]:
        """W0-2：返回调用者以 ``OrgAdmin`` 角色**管理**的组织 ID 集合。

        与可见性集合不同：组织聚合（org-summary）只对管理层开放，普通成员
        即便可见本组织也不得读取聚合数据（调用方在空集合时 403）。
        """
        user_id = getattr(user, "id", None)
        if user_id is None:
            return set()
        rows = await db.execute(
            select(user_organization_role_association.c.organization_id)
            .join(
                Role,
                Role.id == user_organization_role_association.c.role_id,
            )
            .where(
                user_organization_role_association.c.user_id == user_id,
                Role.name == ORG_ADMIN_ROLE_NAME,
            )
        )
        return {int(org_id) for org_id in rows.scalars().all() if org_id is not None}

    async def update_organization(
        self, db: AsyncSession, org_id: int, data: dict[str, Any]
    ) -> Organization | None:
        """更新组织信息，自动处理层级变更 — 不负责 commit，由调用方管理事务"""
        result = await db.execute(select(Organization).where(Organization.id == org_id))
        org = result.scalar_one_or_none()
        if not org:
            return None

        old_parent_id = org.parent_id
        new_parent_id = data.get("parent_id")

        if "parent_id" in data and new_parent_id != old_parent_id:
            if new_parent_id == org_id:
                raise ValueError("不能将组织设置为自己的子组织")

            level: int = 1
            if new_parent_id:
                parent_result = await db.execute(
                    select(Organization).where(Organization.id == new_parent_id)
                )
                parent = parent_result.scalar_one_or_none()
                if parent:
                    level = int(cast(int, parent.level)) + 1

            org.level = int(level)
            await self._update_children_levels(db, org)

        for field, value in data.items():
            if hasattr(org, field):
                setattr(org, field, value)

        await db.flush()
        await db.refresh(org)
        return org

    async def _update_children_levels(self, db: AsyncSession, parent_org: Organization):
        """递归更新子组织的层级"""
        result = await db.execute(
            select(Organization).where(Organization.parent_id == parent_org.id)
        )
        children = result.scalars().all()

        for child in children:
            child.level = int(cast(int, parent_org.level)) + 1  # type: ignore
            await self._update_children_levels(db, child)

    async def batch_delete_organizations(self, db: AsyncSession, org_ids: list[int]) -> bool:
        """批量删除组织及其关联数据。

        A2 P0：级联检查（ConflictError → 409）必须向上冒泡，不能被
        `except Exception` 吞成 False（端点会误报「批量删除失败」的 500/200）。
        """
        try:
            for org_id in org_ids:
                await self.delete_organization_thoroughly(db, org_id)
            return True
        except AppError:
            raise
        except Exception as e:
            logger.error(f"批量删除组织失败: {str(e)}")
            return False

    def _org_to_dict(self, org: Organization) -> dict:
        """将组织对象转换为字典，并包含基础统计信息"""
        return {
            "id": org.id,
            "name": org.name,
            "description": org.description,
            "color": org.color,
            "is_private": org.is_private,
            "parent_id": org.parent_id,
            "level": org.level,
            "sort_order": org.sort_order,
            "created_at": org.created_at.isoformat() if org.created_at else None,
            "children": [],
        }

    async def create_organization(
        self, db: AsyncSession, data: dict, owner_id: int
    ) -> Organization:
        """创建新组织 — 不负责 commit，由调用方管理事务"""
        level = 1
        if data.get("parent_id"):
            parent_result = await db.execute(
                select(Organization).where(Organization.id == data["parent_id"])
            )
            parent = parent_result.scalar_one_or_none()
            if parent:
                level = int(cast(int, parent.level)) + 1

        organization = Organization(
            name=data["name"],
            description=data.get("description"),
            color=data.get("color", "#18a058"),
            parent_id=data.get("parent_id"),
            level=level,
            sort_order=data.get("sort_order", 0),
            owner_id=owner_id,
            is_private=data.get("is_private", False),
        )

        db.add(organization)
        await db.flush()
        await db.refresh(organization)

        # 自动将所有者添加为成员
        user_result = await db.execute(select(User).where(User.id == owner_id))
        user = user_result.scalar_one_or_none()
        if user:
            await self.add_user_to_organization(db, user, organization)

        return organization

    async def get_org_stats(self, db: AsyncSession, org_id: int) -> dict:
        """获取组织统计数据"""
        try:
            # 统计成员数
            member_count_result = await db.execute(
                select(func.count(user_organization.c.user_id)).where(
                    user_organization.c.organization_id == org_id
                )
            )
            member_count = member_count_result.scalar() or 0

            # 统计文档数
            doc_count_result = await db.execute(
                select(func.count(Document.id)).where(Document.organization_id == org_id)
            )
            doc_count = doc_count_result.scalar() or 0

            return {"member_count": member_count, "document_count": doc_count}
        except Exception as e:
            # issue #301（批 C）：逐一组织统计失败不再回落 0/0 —— 「0 成员 0 文档」
            # 是对一个具体组织的具体事实陈述，与「查不了」必须可分。
            logger.error(f"获取组织统计失败: {str(e)}")
            raise ServiceUnavailableError("组织统计查询失败，请稍后重试") from e

    # 删除组织前必须为空的「工作数据」集合：这些表都以 organization_id 外键
    # 引用 organizations，裸删会触发 MySQL 1451，也意味着组织里还有未迁走的数据。
    _ATTACHED_DATA_MODELS: tuple[tuple[str, type], ...] = (
        ("文档", Document),
        ("会话", ChatSession),
        ("Agent 任务", AgentTask),
        ("定时任务", ScheduledTask),
        ("知识库处理任务", KnowledgeProcessingJob),
        ("工作流", Workflow),
        ("提示词模板", PromptTemplate),
        ("用量记录", TokenUsageRecord),
    )

    async def _assert_org_has_no_attached_data(self, db: AsyncSession, org_ids: list[int]) -> None:
        """A2 P0：级联检查——存在挂载数据时 409 并提示转移，绝不裸删到 1451。"""
        attached: list[str] = []
        for label, model in self._ATTACHED_DATA_MODELS:
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(model)
                    .where(model.organization_id.in_(org_ids))
                )
            ).scalar() or 0
            if count:
                attached.append(f"{label} {count} 条")
        if attached:
            raise ConflictError(
                "组织下仍有未转移的数据（"
                + "、".join(attached)
                + "），不能直接删除。请先将这些数据转移到其他组织后再重试。"
            )

    async def delete_organization_thoroughly(self, db: AsyncSession, org_id: int) -> bool:
        """彻底删除组织及其所有子组织 — 不负责 commit，由调用方管理事务。

        A2 P0：先做级联检查（文档/会话/任务等工作数据存在 → 409），
        再把成员用户的 organization_id 摘除（避免 users 外键 1451），
        最后删除成员关系与组织行。
        """
        orgs_to_delete = await self._get_all_children_ids(db, org_id)
        orgs_to_delete.append(org_id)

        await self._assert_org_has_no_attached_data(db, orgs_to_delete)

        # 成员用户不随组织删除：先摘除其主组织引用，否则 users.organization_id
        # 的外键会让删除组织失败（1451）。
        await db.execute(
            update(User)
            .where(User.organization_id.in_(orgs_to_delete))
            .values(organization_id=None)
        )
        await db.execute(
            delete(user_organization).where(user_organization.c.organization_id.in_(orgs_to_delete))
        )
        # A2 P0（评审 M-1）：角色关联表在模型里声明 ON DELETE CASCADE，但 alembic
        # 005 建表 DDL 没有该 ondelete（外键实际是 RESTRICT）。不能依赖 DB 级联：
        # 显式清理，保证迁移建库与 create_all 建库都不会在删组织时 1451。
        await db.execute(
            delete(user_organization_role_association).where(
                user_organization_role_association.c.organization_id.in_(orgs_to_delete)
            )
        )
        # B4：组织设置是组织的配置附属，不属于「需转移的工作数据」；显式清理，
        # 不依赖 FK ondelete（迁移建库与 create_all 建库的 DDL 可能不同）。
        # 旧 schema 冒烟测试（只建迁移相关表）可能还没有该表——先在同一连接内
        # 探测再删（不能用 inspect：它会另开连接并回滚未提交事务）。
        dialect = db.get_bind().dialect.name
        if dialect == "mysql":
            _exists_sql = (
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'organization_settings'"
            )
        elif dialect == "sqlite":
            _exists_sql = (
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name = 'organization_settings'"
            )
        else:
            _exists_sql = None
        if _exists_sql and (await db.execute(text(_exists_sql))).scalar():
            await db.execute(
                delete(OrganizationSettings).where(
                    OrganizationSettings.organization_id.in_(orgs_to_delete)
                )
            )
        await db.execute(delete(Organization).where(Organization.id.in_(orgs_to_delete)))
        await db.flush()
        return True

    async def _get_all_children_ids(self, db: AsyncSession, parent_id: int) -> list[int]:
        """递归获取所有子组织ID"""
        result = await db.execute(
            select(Organization.id).where(Organization.parent_id == parent_id)
        )
        children_ids = list(result.scalars().all())

        all_ids = []
        for child_id in children_ids:
            all_ids.append(child_id)
            sub_ids = await self._get_all_children_ids(db, child_id)
            all_ids.extend(sub_ids)

        return all_ids

    async def create_private_organization(self, db: AsyncSession, user: User) -> Organization:
        """为用户创建私有组织标签 — 不负责 commit，由调用方管理事务"""
        org_name = f"PRIVATE_{user.username}"

        existing = await self.get_organization_by_name(db, org_name)
        if existing:
            return existing

        organization = Organization(
            name=org_name,
            description=f"{user.username} 的私有组织",
            is_private=True,
            owner_id=user.id,
        )

        db.add(organization)
        await db.flush()
        await db.refresh(organization)

        await self.add_user_to_organization(db, user, organization)

        logger.info(f"为用户 {user.username} 创建私有组织: {org_name}")
        return organization

    async def get_organization_by_name(self, db: AsyncSession, name: str) -> Organization | None:
        """根据名称获取组织"""
        try:
            result = await db.execute(select(Organization).where(Organization.name == name))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"根据名称获取组织失败: {str(e)}")
            return None

    async def add_user_to_organization(
        self, db: AsyncSession, user: User, organization: Organization
    ) -> bool:
        """添加用户到组织 — 不负责 commit，由调用方管理事务"""
        result = await db.execute(
            select(user_organization).where(
                and_(
                    user_organization.c.user_id == user.id,
                    user_organization.c.organization_id == organization.id,
                )
            )
        )
        if result.scalar_one_or_none():
            return False

        await db.execute(
            user_organization.insert().values(user_id=user.id, organization_id=organization.id)
        )
        await db.flush()
        logger.info(f"将用户 {user.username} 添加到组织 {organization.name}")
        return True

    async def get_user_organizations(self, db: AsyncSession, user: User) -> list[Organization]:
        """获取用户的所有组织"""
        try:
            # 这里的 user.organizations 需要在 User 模型中正确配置 secondary 关系
            result = await db.execute(
                select(Organization)
                .join(user_organization)
                .where(user_organization.c.user_id == user.id)
            )
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"获取用户组织失败: {str(e)}")
            return []

    async def get_organization_members(self, db: AsyncSession, organization_id: int) -> list[User]:
        """获取组织的所有成员"""
        try:
            result = await db.execute(
                select(User)
                .join(user_organization)
                .where(user_organization.c.organization_id == organization_id)
            )
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"获取组织成员失败: {str(e)}")
            return []


# 创建服务实例
organization_service = OrganizationService()
