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
from sqlalchemy.dialects import mysql
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
    案件必须在文档重建/删除后仍然可读。删除保护与归档出口见 Batch 5/7
    （活跃案件阻止文档删除；归档案件不再阻止删除）。
    文档是否存在由服务层在响应中显式标注，绝不静默当作仍然有效。
    """

    __tablename__ = "review_case"
    __table_args__ = (
        Index("ix_review_case_org_created", "organization_id", "created_at"),
        Index("ix_review_case_org_status", "organization_id", "status"),
        # 归档出口（Batch 7）：默认列表只看活跃案件；归档案件不再阻止文档删除。
        Index("ix_review_case_org_archived", "organization_id", "archived_at"),
        # 删除守卫按三份文档引用反查案件（OR 三列），单列索引让 index merge 可用。
        Index("ix_review_case_policy_document_id", "policy_document_id"),
        Index("ix_review_case_base_document_id", "base_document_id"),
        Index("ix_review_case_revised_document_id", "revised_document_id"),
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
    # 建案时固化的规则/playbook 快照；旧案件为 NULL，读取时回落当前 checklist。
    rule_snapshot_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("review_rule_set.id"), index=True, comment="规则快照 ID"
    )
    # 模板库（批量五）：建案时选用的模板与版本，仅作**溯源指针**。
    # 规则内容已在 rule_snapshot_id 冻结；模板/版本后续归档或新增版本都不
    # 影响本案件。指针可为 NULL（旧案件、进程级 checklist 建案）。
    # 故意不建外键：模板是可选溯源信息，不得反过来阻止模板生命周期操作。
    template_id: Mapped[str | None] = mapped_column(
        String(36), index=True, comment="建案时选用的模板 id（溯源用，可空）"
    )
    template_version_no: Mapped[int | None] = mapped_column(
        Integer, comment="建案时选用的模板版本号（溯源用，可空）"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="blocked")
    # 建案时为什么 blocked（side:reason 列表）；ready 时为空数组。
    blocking_reasons: Mapped[Any | None] = mapped_column(JSON, comment="阻断原因码列表")
    # 三份文档的 readiness/版本/哈希快照（建案时固化，不随后续文档变化漂移）。
    readiness_snapshot: Mapped[Any | None] = mapped_column(JSON, comment="建案时文档快照")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    # 归档出口（Batch 7）：只读终态。归档后不可再决策/分析/导出，
    # 且不再阻止其引用文档的物理删除；案件与导出快照仍可读。
    archived_at: Mapped[datetime | None] = mapped_column(
        # 与迁移 024 的 mysql.DATETIME(fsp=6) 对齐（同 ReviewExport.created_at 先例）；
        # 否则 check_schema 的 MySQL 列类型门禁会报 datetime vs datetime(6) 漂移。
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
        comment="归档时间（只读终态）",
    )
    archived_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), comment="归档人"
    )
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
    # 竞品对标：风险等级与可解释引用；纯派生，不影响判定/哈希/过期原因。
    severity: Mapped[str | None] = mapped_column(String(16), comment="风险等级 high/medium/low")
    explanation: Mapped[Any | None] = mapped_column(
        JSON, comment="可解释引用 rule_id/evidence_id/reason_code"
    )
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
    # 口径：change_type == unidentified（文本事实无法判定）的项数，不是 status。
    unidentified_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
        server_default=func.now(),
        comment="导出时间（微秒精度，保证同秒导出排序稳定）",
    )


class ReviewRuleSet(Base):
    """合同审查规则/playbook 版本快照：建案时固化，供旧案件审计复现。

    与 doc/证据快照同口径：快照行不可变，规则配置变更只新增行；案件通过
    ``rule_snapshot_id`` 引用。组织列用于租户隔离，旧数据可为 NULL。
    """

    __tablename__ = "review_rule_set"
    __table_args__ = (
        Index("ix_review_rule_set_org_created", "organization_id", "created_at"),
        UniqueConstraint("organization_id", "payload_sha256", name="uq_review_rule_set_org_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), comment="所属组织（旧数据可空）"
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False, comment="规则版本标签")
    payload_json: Mapped[Any] = mapped_column(JSON, nullable=False, comment="规则快照 JSON")
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False, comment="快照内容哈希")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="发布时间"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )


REVIEW_TEMPLATE_STATUSES = ("active", "archived")
"""模板生命周期：active 可建版本/可被建案选择；archived 只读、不可新建版本、
不可再被新案件选择（历史案件已固化到规则快照，不受影响）。"""


class ReviewTemplate(Base):
    """组织级合同审查模板/playbook（用户可编辑库的头表）。

    模板只是「可编辑规则的容器」：真正的规则内容冻结在
    ``review_template_version`` 行里，版本行本身不可变（编辑 = 新增版本）。
    建案时只能选择某个版本，并继续把该版本内容固化为不可变的
    ``ReviewRuleSet`` 快照 —— 模板/版本的后续编辑不会改写任何历史案件。

    删除策略：**不提供物理删除**，只有归档（``status='archived'``）。模板与
    版本是审计资产，且案件快照派生自它们；归档后不再参与建案选择。
    """

    __tablename__ = "review_template"
    __table_args__ = (
        Index("ix_review_template_org_created", "organization_id", "created_at"),
        Index("ix_review_template_org_status", "organization_id", "status"),
        # 同一组织内模板名唯一：用户按名字选择模板，重名会让选择语义含糊。
        UniqueConstraint("organization_id", "name", name="uq_review_template_org_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, comment="所属组织（租户隔离键）"
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="模板名称")
    description: Mapped[str | None] = mapped_column(
        String(500), comment="模板说明（纯文本，不作为规则参与判定）"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", comment="active/archived"
    )
    latest_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="最新版本号（0=尚无版本）"
    )
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    updated_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="归档时间（只读终态）"
    )
    archived_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"))
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )


class ReviewTemplateVersion(Base):
    """模板版本：每次编辑新增一行，行内容不可变（与 ReviewRuleSet 同口径）。

    ``items_json`` 是校验/规范化后的规则项列表（key/keywords/label/configurable），
    写入时同时固化 ``payload_sha256``；读取时重算哈希，哈希不符视为损坏，
    不参与建案（绝不把被篡改的规则当作可执行内容）。
    ``source`` 区分模板版本的内容来源：
    - ``manual``：用户通过 API 保存；
    - ``bootstrap``：从进程级 checklist（默认 4 项 + ``REVIEW_CHECKLIST_JSON``）
      初始化的版本，供存量部署平滑迁移。
    """

    __tablename__ = "review_template_version"
    __table_args__ = (
        UniqueConstraint("template_id", "version_no", name="uq_review_template_version_no"),
        Index("ix_review_template_version_template_created", "template_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("review_template.id"), nullable=False, index=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
        comment="所属组织（与模板冗余保存，供组织删除保护与跨组织防护）",
    )
    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="版本号（每个模板自增）"
    )
    version_label: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="展示用版本标签（如 v1）"
    )
    items_json: Mapped[Any] = mapped_column(JSON, nullable=False, comment="规则项列表")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="规则项数（有界）")
    payload_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="items_json 规范化后的内容哈希"
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", comment="manual/bootstrap"
    )
    change_note: Mapped[str | None] = mapped_column(String(200), comment="变更说明")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
