# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 组织模型
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Table
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base

user_organization = Table(
    'user_organization',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('organization_id', Integer, ForeignKey('organizations.id'), primary_key=True),
    Column('created_at', DateTime, default=func.now())
)

class Organization(Base):
    """组织模型"""
    __tablename__ = "organizations"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False)
    description = Column(String(500))
    color = Column(String(20), default="#18a058")
    is_private = Column(Boolean, default=False)
    
    parent_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    level = Column(Integer, default=1)
    sort_order = Column(Integer, default=0)
    
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    
    # 关联
    # 自关联，实现树形结构
    parent = relationship("Organization", remote_side=[id], back_populates="children")
    children = relationship("Organization", back_populates="parent", cascade="all, delete-orphan")
    
    # 👇👇👇 关键点：必须加 foreign_keys
    owner = relationship(
        "User", 
        back_populates="owned_organizations",
        foreign_keys=[owner_id],
        overlaps="owned_organizations" # 消除警告
    )
    
    # 👇👇👇 关键点：添加 users 关系并指定 foreign_keys
    users = relationship(
        "User",
        back_populates="organization",
        foreign_keys="User.organization_id"
    )

    members = relationship(
        "User",
        secondary=user_organization,
        back_populates="organizations",
        lazy="selectin" # 异步加载必须加这个
    )
    
    documents = relationship("Document", back_populates="organization", lazy="selectin")
    chat_sessions = relationship("ChatSession", back_populates="organization", lazy="selectin")

    user_organization_roles = relationship(
        "Role",
        secondary="user_organization_role_association",
        overlaps="roles_in_organizations",
        viewonly=True, # 如果只是用来查询，设为 viewonly 更安全
        lazy="selectin"
    )
    
    def __repr__(self):
        return f"<Organization(id={self.id}, name='{self.name}', is_private={self.is_private})>"
        