"""评审门控的组织技能库持久层（批次十）。

两张表：

- ``org_skill``：组织级技能头（名称/说明/状态/最新版本号/当前启用版本指针）。
  只归档、不物理删除；同一组织内名称唯一。
- ``org_skill_version``：不可变版本行（结构化 payload + sha256）。每次提议追加
  一行，旧行永不改写；审核状态 ``pending`` / ``approved`` / ``rejected`` 只能
  从 pending 单向迁移。``enabled_version_no`` 是唯一运行时指针，必须指向本技能
  的 approved 版本且哈希校验通过。

安全边界（与 ``app.services.org_skill_service`` 共同保证）：

- ``payload_json`` 只允许结构化触发短语 + 工具名序列，不含参数/自由指令；
  ``description`` 仅供 UI，绝不进入 system prompt；
- 运行时匹配只返回 approved + enabled + hash 校验通过 + 工具仍可见的版本；
- 所有行按 ``organization_id`` 隔离，跨组织一律 404。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

ORG_SKILL_STATUSES = ("active", "archived")
"""技能头状态：active 可提议/审核/启用；archived 只读终态。"""

ORG_SKILL_REVIEW_STATUSES = ("pending", "approved", "rejected")
"""版本审核状态：pending 等待审核；approved 可被启用；rejected 终态。"""

ORG_SKILL_SOURCES = ("run_proposal", "manual")
"""版本来源：run_proposal=从本人完成的运行提议；manual=手工结构化 payload。"""


class OrgSkill(Base):
    """组织级技能头（只归档，不物理删除）。"""

    __tablename__ = "org_skill"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_org_skill_org_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属组织（租户隔离键）",
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="技能名称（组织内唯一）")
    description: Mapped[str | None] = mapped_column(
        String(300), comment="技能说明（仅 UI 展示，不进入 system prompt）"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="active/archived"
    )
    latest_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="最新版本号（0=尚无版本）"
    )
    enabled_version_no: Mapped[int | None] = mapped_column(
        Integer, comment="当前启用版本号；NULL=未启用（唯一运行时指针）"
    )
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="归档时间（只读终态）"
    )
    archived_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )


class OrgSkillVersion(Base):
    """不可变技能版本：提议追加一行，旧行永不改写。"""

    __tablename__ = "org_skill_version"
    __table_args__ = (
        UniqueConstraint("skill_id", "version_no", name="uq_org_skill_version_no"),
        Index("ix_org_skill_version_org_review", "organization_id", "review_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    skill_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("org_skill.id"), nullable=False, comment="所属技能 id"
    )
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属组织（与技能头冗余保存，供组织删除保护与跨组织防护）",
    )
    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="版本号（每个技能自增）"
    )
    payload_json: Mapped[Any] = mapped_column(
        JSON, nullable=False, comment="结构化 payload（trigger_phrases + tool_sequence）"
    )
    payload_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="payload_json 规范化后的内容哈希"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", comment="run_proposal/manual"
    )
    provenance_json: Mapped[Any | None] = mapped_column(
        JSON, comment="来源元数据（如来源任务 id；不存 payload 正文）"
    )
    review_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", comment="pending/approved/rejected"
    )
    reviewed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="审核时间"
    )
    review_note: Mapped[str | None] = mapped_column(String(200), comment="审核备注（仅元数据）")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
