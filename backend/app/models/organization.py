"""
派聪明AI知识库系统 - 组织模型
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship
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

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(String(500))
    color: Mapped[str] = mapped_column(String(20), default="#18a058")
    is_private: Mapped[bool] = mapped_column(Boolean, default=False)

    parent_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"))
    level: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    # 关联
    parent: Mapped[Optional["Organization"]] = relationship("Organization", remote_side=[id], back_populates="children")
    children: Mapped[list["Organization"]] = relationship("Organization", back_populates="parent", cascade="all, delete-orphan")

    owner: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="owned_organizations",
        foreign_keys=[owner_id],
        overlaps="owned_organizations"
    )

    users: Mapped[list["User"]] = relationship(
        "User",
        back_populates="organization",
        foreign_keys="User.organization_id"
    )

    members: Mapped[list["User"]] = relationship(
        "User",
        secondary=user_organization,
        back_populates="organizations",
        lazy="selectin"
    )

    documents: Mapped[list["Document"]] = relationship("Document", back_populates="organization", lazy="selectin")
    chat_sessions: Mapped[list["ChatSession"]] = relationship("ChatSession", back_populates="organization", lazy="selectin")

    user_organization_roles: Mapped[list["Role"]] = relationship(
        "Role",
        secondary="user_organization_role_association",
        overlaps="roles_in_organizations",
        viewonly=True,
        lazy="selectin"
    )

    def __repr__(self):
        return f"<Organization(id={self.id}, name='{self.name}', is_private={self.is_private})>"
