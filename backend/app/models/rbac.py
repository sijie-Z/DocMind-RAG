# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - RBAC (Role-Based Access Control) 模型
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Table, UniqueConstraint, Boolean
from sqlalchemy.orm import relationship
from enum import Enum
from sqlalchemy.sql import func

from app.core.database import Base


class PermissionType(str, Enum):
    """定义所有可能的权限类型"""
    # 用户管理
    VIEW_USER_DETAIL = "user:read"
    MANAGE_USER_ROLES = "user:manage_roles"
    DELETE_USER = "user:delete"

    # 组织管理
    VIEW_ORGANIZATION = "organization:read"
    CREATE_ORGANIZATION = "organization:create"
    UPDATE_ORGANIZATION = "organization:update"
    DELETE_ORGANIZATION = "organization:delete"
    MANAGE_ORGANIZATION_MEMBERS = "organization:manage_members"

    # 文档管理
    DOCUMENT_UPLOAD = "document:upload"
    DOCUMENT_READ = "document:read"
    DOCUMENT_UPDATE = "document:update"
    DOCUMENT_DELETE = "document:delete"

    # 知识库管理
    VIEW_KNOWLEDGE_BASE = "knowledge_base:read"
    SEARCH_KNOWLEDGE_BASE = "knowledge_base:search"
    MANAGE_KNOWLEDGE_BASE = "knowledge_base:manage"
    REBUILD_DOCUMENT_INDEX = "knowledge_base:rebuild_index"

    # 提示词管理
    VIEW_PROMPT = "prompt:read"
    CREATE_PROMPT = "prompt:create"
    UPDATE_PROMPT = "prompt:update"
    DELETE_PROMPT = "prompt:delete"
    MANAGE_SYSTEM_PROMPTS = "prompt:manage_system"

    # 系统健康与工具
    VIEW_SYSTEM_HEALTH = "system:health"
    USE_EMBEDDING_SERVICE = "system:use_embedding_service"

    # 其他，根据需要添加
    # ...

# 角色与权限的关联表
role_permission_association = Table(
    'role_permission_association',
    Base.metadata,
    Column('role_id', Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('permissions.id', ondelete='CASCADE'), primary_key=True)
)

# 用户在特定组织下的角色关联表
# 一个用户在一个组织下可以有多个角色，因此需要多对多关系
user_organization_role_association = Table(
    'user_organization_role_association',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('organization_id', Integer, ForeignKey('organizations.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', Integer, ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
    UniqueConstraint('user_id', 'organization_id', 'role_id', name='uq_user_org_role') # 确保一个用户在一个组织中不会重复拥有同一个角色
)

class Permission(Base):
    """权限模型 - 定义系统中可执行的原子操作"""
    __tablename__ = "permissions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False, comment="权限名称，例如 document:create")
    description = Column(String(255), nullable=True, comment="权限描述")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    roles = relationship("Role", secondary=role_permission_association, back_populates="permissions")

    def __repr__(self):
        return f"<Permission(name='{self.name}')>"

class Role(Base):
    """角色模型 - 定义用户在系统或组织中的身份，包含一组权限"""
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False, comment="角色名称，例如 OrgAdmin")
    description = Column(String(255), nullable=True, comment="角色描述")
    is_system_role = Column(Boolean, default=False, comment="是否为系统级别角色 (如SysAdmin)，不受组织限制")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    permissions = relationship(
        "Permission", 
        secondary=role_permission_association, 
        back_populates="roles",
        lazy="selectin"  # <--- 加这个！
        )
    
    # 用户与组织-角色的反向关系
    user_organization_roles = relationship("User", secondary=user_organization_role_association, back_populates="roles_in_organizations")

    def __repr__(self):
        return f"<Role(name='{self.name}')>"
