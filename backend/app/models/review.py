"""核验数据对象：文档核验身份 + 核验案件 / 核验项 / 证据快照。

- ``review_document_meta``：文档核验身份（角色/版本族/版本序/内容哈希），
  与 ``Document`` 核心表分开，每篇文档最多一行；删除文档时由知识库统一
  删除链清理。
- ``review_case`` / ``review_item`` / ``review_evidence``（合同核验场景
  Batch 2）：案件引用三份文档的 id 与快照信息，**故意不建到 documents 的
  外键** —— 证据快照必须在文档重建/删除后仍可读，删除策略由 Batch 5 的
  生命周期保护单独定；案件/核验项之间用外键级联保证行一致性。

所有状态/角色保存为字符串（不建 MySQL ENUM），写接口在服务层校验。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

# 允许的文档角色；写接口在服务层校验，DB 保存字符串（不建 MySQL ENUM，
# 避免后续角色扩展需要 ALTER TABLE）。
DOC_ROLES = ("policy", "template", "contract")

# 核验案件状态机：blocked → ready → in_review → completed。
# completed 只表示「所有固定项已由人决策且核验单已固化」，不是法律结论。
REVIEW_CASE_STATUSES = ("blocked", "ready", "in_review", "completed")

# 固定核验项（第一版只做这 4 类，不做通用条款类型体系）。
REVIEW_ITEM_KEYS = (
    "payment_term",
    "start_condition",
    "acceptance_invoice",
    "exception",
)

# 核验项状态；pending/insufficient/parse_failed 不能算完成。
REVIEW_ITEM_STATUSES = (
    "pending",
    "verified",
    "needs_human",
    "not_applicable",
    "insufficient",
    "parse_failed",
)

# V1↔V2 差异类型（Batch 3 对齐时写入，建案时为空）。
REVIEW_CHANGE_TYPES = (
    "unchanged",
    "modified",
    "added",
    "removed",
    "moved",
    "renumbered",
    "unidentified",
)

# 证据侧：制度/模板、基准版（V1）、修订版（V2）。
REVIEW_SIDES = ("policy", "base", "revised")

# 证据角色：候选（建案时自动抓取）与人工选择的四类角色。
REVIEW_EVIDENCE_ROLES = (
    "candidate",
    "main",
    "condition",
    "exception",
    "cross_ref",
)


class ReviewDocumentMeta(Base):
    """文档核验身份：谁在哪个版本族/版本序上、以哪个内容哈希注册。"""

    __tablename__ = "review_document_meta"
    __table_args__ = (
        # 同组织同角色同族同版本/同族版本序各只能注册一次；family/version 为
        # NULL 的制度/模板不参与唯一索引（服务层仅在同时给出时做重复检查）。
        UniqueConstraint(
            "organization_id",
            "doc_role",
            "family_key",
            "version_label",
            name="uq_review_document_meta_identity",
        ),
        UniqueConstraint(
            "organization_id",
            "doc_role",
            "family_key",
            "version_seq",
            name="uq_review_document_meta_version_seq",
        ),
        Index(
            "ix_review_document_meta_family",
            "organization_id",
            "doc_role",
            "family_key",
            "version_seq",
        ),
    )

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), primary_key=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    doc_role: Mapped[str] = mapped_column(String(16), nullable=False)
    family_key: Mapped[str | None] = mapped_column(String(128))
    version_label: Mapped[str | None] = mapped_column(String(64))
    version_seq: Mapped[int | None] = mapped_column(Integer)
    supersedes_document_id: Mapped[str | None] = mapped_column(String(36))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    registered_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="注册时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )


class ReviewCase(Base):
    """核验案件：一份制度/模板 + 合同 V1 + V2 的核验工作单元。

    ``policy_document_id`` / ``base_document_id`` / ``revised_document_id``
    保存的是建案时固化的身份引用，故意不加到 documents 的外键：证据快照与
    案件必须在文档重建/删除后仍然可读（Batch 5 再定删除/归档保护策略）。
    文档是否存在由服务层在响应中显式标注，绝不静默当作仍然有效。
    """

    __tablename__ = "review_case"
    __table_args__ = (
        Index("ix_review_case_org_created", "organization_id", "created_at"),
        Index("ix_review_case_org_status", "organization_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    base_document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revised_document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    checklist_version: Mapped[str] = mapped_column(String(16), nullable=False, default="v1")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="blocked")
    # 建案时为什么 blocked（side:reason 列表）；ready 时为空数组。
    blocking_reasons: Mapped[Any | None] = mapped_column(JSON, comment="阻断原因码列表")
    # 三份文档的 readiness/版本/哈希快照（建案时固化，不随后续文档变化漂移）。
    readiness_snapshot: Mapped[Any | None] = mapped_column(JSON, comment="建案时文档快照")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )


class ReviewItem(Base):
    """固定核验项：payment_term / start_condition / acceptance_invoice / exception。"""

    __tablename__ = "review_item"
    __table_args__ = (UniqueConstraint("case_id", "item_key", name="uq_review_item_case_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_case.id"), nullable=False, index=True
    )
    item_key: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    # Batch 3 对齐结果；建案时为 NULL，绝不预填「无差异」。
    change_type: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    # 抽取的文本事实（期限值/单位/起算事件/条件/例外），每项带
    # identified|ambiguous|not_found；未识别不填猜测值。
    facts: Mapped[Any | None] = mapped_column(JSON)
    decided_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )


class ReviewEvidence(Base):
    """证据快照：建案/人工选择时把 parser 原文固化到本表。

    行内保存 quote 全文、quote_sha256、内容哈希与定位（chunk_index /
    clause / section / page / 字符区间），因此文档 rebuild 后快照仍然可读；
    ``chunk_id_at_capture`` 只用于比对「实时正文是否已经变化」，不建外键。
    ``document_id`` 同样不建外键（文档删除后证据仍需可审计）。
    """

    __tablename__ = "review_evidence"
    __table_args__ = (
        UniqueConstraint(
            "item_id",
            "side",
            "chunk_id_at_capture",
            name="uq_review_evidence_item_chunk",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_case.id"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_item.id"), nullable=False, index=True
    )
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="candidate")
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    document_id: Mapped[str] = mapped_column(String(36), nullable=False)
    doc_role: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_version_label: Mapped[str | None] = mapped_column(String(64))
    doc_version_seq: Mapped[int | None] = mapped_column(Integer)
    document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_id_at_capture: Mapped[str] = mapped_column(String(36), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    clause_no: Mapped[Any | None] = mapped_column(JSON, comment="条款号列表（候选线索）")
    section_no: Mapped[Any | None] = mapped_column(JSON, comment="章节号列表（候选线索）")
    page_number: Mapped[int | None] = mapped_column(Integer)
    char_start: Mapped[int | None] = mapped_column(Integer)
    char_end: Mapped[int | None] = mapped_column(Integer)
    quote_text: Mapped[str] = mapped_column(Text, nullable=False)
    quote_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    text_provenance: Mapped[str] = mapped_column(
        String(32), nullable=False, default="parser_original"
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="快照时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="选择状态更新时间"
    )


class ReviewExport(Base):
    """核验单快照：导出后不可变；决策变化只能新增行（旧行留档）。"""

    __tablename__ = "review_export"
    __table_args__ = (Index("ix_review_export_case_created", "case_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("review_case.id"), nullable=False)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[Any] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    needs_human_count: Mapped[int] = mapped_column(Integer, nullable=False)
    insufficient_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="导出时间"
    )
