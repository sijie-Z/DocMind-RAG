"""Agent 工件（artifact）持久层 —— 版本化的可预览/可下载产出物。

背景：此前 Agent 的工具结果只是 SSE 文本，前端靠 ``utils/artifact.ts`` 的
文本嗅探猜类型；落库的 ``agent_tool_results.result_summary`` 又截断到 500
字符，生成的文件既不可列出、不可回看，也无法跨轮复用。Hermes Agent 一类
竞品提供「版本化工件卡 + 沙箱预览」，本表是这一能力的存储侧。

**为什么是单表（而不是 artifact + artifact_version 两张表）**：
工件是**内容寻址且不可变**的——每次修订都是一份新内容（新 sha256、新对象名），
「版本」只是同一内容链上的一个新行，不存在「同一行被改写」的更新路径。
用 ``replaces_artifact_id`` 自引用把行串成版本链，可以：

- 链头（无其他行指向它）即最新版，读取最新版不需要 JOIN 历史表；
- 任一历史版本仍是完整、可直接预览/下载的行（与合同核验的不可变快照同口径）；
- 版本链上的行天然共享 ``conversation_id`` / ``task_id``，列表按作用域过滤
  后再在服务层组装链，索引面最简单。

独立版本表会把上述所有列复制一遍、再引入一层外键与 JOIN，却换不来任何
额外语义——因此不采用。

**生命周期**：本层**没有删除路径**（工件是审计/复用资产）。``conversation_id``
与 ``task_id`` 刻意**不建外键**：会话/任务行可能被清理，而工件必须继续可读
（与 ``review_evidence.evidence_id`` 的先例一致）。组织列与用户列建外键，
用于租户隔离与组织删除保护。

``status``：v1 只有 ``ready`` —— 对象先写入内容寻址的确定性对象名、成功后才
插入行，不存在「行指向缺失内容」的窗口；列保留给未来的异步/分段写入态，
读接口一律以 ``ready`` 为可读前提。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

# 允许的工件类型。kind 决定预览方式与默认 MIME/扩展名；未知值在服务层拒绝，
# DB 只存字符串（不建 MySQL ENUM，避免扩展类型需要 ALTER TABLE）。
ARTIFACT_KINDS = ("text", "markdown", "json", "diff", "code", "html", "svg")

# 工件状态：v1 只落 ready（见模块头）；读取/下载只放行 ready。
ARTIFACT_STATUSES = ("ready",)


class AgentArtifact(Base):
    """一个 Agent 工件版本（一行 = 一份不可变内容）。"""

    __tablename__ = "agent_artifact"
    __table_args__ = (
        # 同一工具调用的重复落账（resume 重放/并发写）由 DB 唯一约束兜底。
        # MySQL/SQLite 的唯一约束都允许多个 NULL，未带 task/call 的工件不受限。
        UniqueConstraint("task_id", "source_call_id", name="uq_agent_artifact_task_call"),
        Index("ix_agent_artifact_org_created", "organization_id", "created_at"),
        Index("ix_agent_artifact_org_sha256", "organization_id", "content_sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
        comment="所属组织（租户隔离键）",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        comment="创建者（读取按用户隔离）",
    )
    # 会话/任务是**溯源信息**，不建外键：它们可能被清理，工件必须继续可读。
    conversation_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True, comment="来源会话 id（可空，无外键）"
    )
    task_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True, comment="来源 Agent 任务 id（可空，无外键）"
    )
    agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="text/markdown/json/diff/code/html/svg"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="展示标题")
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="内容 SHA-256（内容寻址）"
    )
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="内容字节数")
    storage_object_name: Mapped[str | None] = mapped_column(
        String(512), nullable=True, comment="MinIO 对象名（确定性、内容寻址）"
    )
    source_tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, comment="版本号（链内自增，从 1 起）"
    )
    replaces_artifact_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("agent_artifact.id"),
        nullable=True,
        index=True,
        comment="被本版本取代的上一版（版本链）",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ready", comment="ready（v1 唯一状态）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )
