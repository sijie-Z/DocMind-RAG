# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 用户模型
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.core.database import Base

class User(Base):
    """用户模型"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=True)
    
    # 组织关联
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    
    # 👇👇👇 关键点：必须加 foreign_keys
    organization = relationship(
        "Organization", 
        back_populates="users",
        foreign_keys=[organization_id],
        lazy="selectin"
    )
    
    # 用户角色和状态
    role = Column(String(20), default="user", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superuser = Column(Boolean, default=False, nullable=False)
    
    # 时间戳
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    
    # 用户配置
    avatar_url = Column(String(500), nullable=True)
    phone = Column(String(20), nullable=True)
    department = Column(String(100), nullable=True)
    position = Column(String(100), nullable=True)
    preferences = Column(Text, nullable=True)
    
    # API Key
    api_key = Column(String(100), unique=True, index=True, nullable=True)
    
    # 关联关系
    documents = relationship("Document", back_populates="uploader", lazy="selectin")
    chat_sessions = relationship("ChatSession", back_populates="user", lazy="selectin")
    
    # 👇👇👇 关键点：这里也要指定 foreign_keys
    owned_organizations = relationship(
        "Organization", 
        back_populates="owner",
        foreign_keys="Organization.owner_id",
        overlaps="owner" # 消除警告
    )
    
    organizations = relationship(
        "Organization",
        secondary="user_organization",
        back_populates="members",
        lazy="selectin"
    )

    roles_in_organizations = relationship(
        "Role",
        secondary="user_organization_role_association",
        overlaps="user_organization_roles", # 消除警告
        lazy="selectin"
    )
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "organization_id": self.organization_id,
            "role": self.role,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "created_at": self.created_at.isoformat() if self.created_at else None,  # pyright: ignore[reportGeneralTypeIssues]
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,  # pyright: ignore[reportGeneralTypeIssues]
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,  # pyright: ignore[reportGeneralTypeIssues]
            "avatar_url": self.avatar_url,
            "phone": self.phone,
            "department": self.department,
            "position": self.position,
            "preferences": self.preferences
        }
    
    def __repr__(self):
        return f"<User(id={self.id}, username='{self.username}', email='{self.email}')>"
        