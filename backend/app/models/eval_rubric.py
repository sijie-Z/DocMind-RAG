"""Rubric 评测：加权 yes/no 评分表 + 不可变版本 + 评测运行（竞品对标）。

与既有评测面的分工（刻意不重叠）：

- ``retrieval_eval``（``RetrievalEvalCase`` / ``RetrievalEvalRun``）衡量**检索**
  是否找对文档/条款（recall@k / MRR），输入是 query 与金标文档 id；
- 本模块衡量**答案**是否满足评分表：每条 rubric 项是一个带权重的 yes/no 问题，
  输入是候选答案文本；不涉及召回、不读检索结果、不写检索评测运行；
- ``app.rag.evaluator`` 的 faithfulness/relevancy 是固定维度的连续分评委；
  本模块是**用户可编辑、可版本化**的评分表，判定为离散 yes/no/unsure，
  并支持与人工金标做校准（agreement / 混淆矩阵）。二者不互相替代。

数据模型（三张表）：

- ``eval_rubric``：组织级评分表头（名称/说明/状态/最新版本号）。只归档、
  不物理删除（版本与运行是审计资产）。
- ``eval_rubric_version``：不可变版本行（``payload_json`` + ``payload_sha256``），
  每次编辑追加一行，旧行永不改写；读取时重算哈希，哈希不符视为损坏并拒绝使用。
- ``eval_rubric_run``：一次运行快照，冻结 rubric 版本号、用例集哈希、模式、
  逐条结果与聚合分数、评委模型与失败机器码。

为什么逐条结果用 JSON 列而不是独立表：运行是**只写一次的不可变快照**，读取
路径只有「列表（不含逐条）」与「详情（整行读回）」两种，没有任何按用例/按
问题过滤或聚合的查询需求；独立表只会增加一次 join、一套迁移与级联清理代价，
不带来可查询性收益。评测运行数量受「每组织预算 + 每次最多 50 条用例」约束，
JSON 体积有界。若将来出现「跨运行按问题聚合」的需求，再拆表并回填。

运行状态只有 ``completed`` / ``failed``：运行是同步执行的，失败运行同样保留
（含机器码），用于解释「这次为什么没有分数」，绝不把失败伪装成 0 分。
"""

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
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

# 状态/模式/严格度保存为字符串（不建 MySQL ENUM），写接口在服务层校验。
RUBRIC_STATUSES = ("active", "archived")
"""评分表状态：active 可编辑/可运行；archived 只读终态（不可改、不可跑）。"""

RUBRIC_VERSION_SOURCES = ("manual", "bootstrap")
"""版本来源：manual=用户通过 API 保存；bootstrap=从服务端模板初始化。"""

RUBRIC_STRICTNESS_LEVELS = ("neutral", "strict", "lenient")
"""单项严格度：决定评委 ``unsure`` 如何折算（唯一影响分数的方式）。"""

RUBRIC_RUN_MODES = ("judge", "fake")
"""运行模式：judge=LLM 评委；fake=确定性回放（测试/CI 用，不是模型判定）。"""

RUBRIC_RUN_STATUSES = ("completed", "failed")
"""运行状态：失败运行保留机器码，不产生任何分数。"""


class EvalRubric(Base):
    """组织级评分表头。

    删除策略：**不提供物理删除**，只有归档（``status='archived'``）。评分表、
    版本与运行都是审计资产；归档后不可新增版本、不可发起新运行。
    同一组织内名称唯一（用户按名称选择评分表）。
    """

    __tablename__ = "eval_rubric"
    __table_args__ = (
        Index("ix_eval_rubric_org_created", "organization_id", "created_at"),
        Index("ix_eval_rubric_org_status", "organization_id", "status"),
        UniqueConstraint("organization_id", "name", name="uq_eval_rubric_org_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属组织（租户隔离键）",
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="评分表名称")
    description: Mapped[str | None] = mapped_column(
        String(500), comment="评分表说明（纯文本，不参与判定）"
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
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )


class EvalRubricVersion(Base):
    """评分表不可变版本：每次编辑新增一行，旧行永不改写。

    ``payload_json`` 是校验/规范化后的评分项列表；``payload_sha256`` 是规范化
    JSON 的内容哈希，读取时重算，哈希或项数不符即视为损坏，**拒绝**用于运行。
    ``source`` 区分 manual / bootstrap（服务层初始化）。
    """

    __tablename__ = "eval_rubric_version"
    __table_args__ = (
        UniqueConstraint("rubric_id", "version_no", name="uq_eval_rubric_version_no"),
        Index("ix_eval_rubric_version_rubric_created", "rubric_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    rubric_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_rubric.id"), nullable=False, index=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
        comment="所属组织（与评分表冗余保存，供组织删除保护与跨组织防护）",
    )
    version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="版本号（每个评分表自增）"
    )
    payload_json: Mapped[Any] = mapped_column(JSON, nullable=False, comment="评分项列表")
    payload_sha256: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="payload_json 规范化后的内容哈希"
    )
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="评分项数（有界）")
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default="manual", comment="manual/bootstrap"
    )
    change_note: Mapped[str | None] = mapped_column(String(200), comment="变更说明")
    created_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )


class EvalRubricRun(Base):
    """一次评分表评测运行（组织内可见；跨组织读取一律 404）。

    行是不可变快照：``rubric_version_no`` 指向的版本行本身不可改写，
    ``dataset_hash`` 对用例集（case_id + answer + gold + fake_verdicts，排序稳定）
    取哈希，``results`` 固化逐条判定，``aggregate`` 固化聚合分数与校准报告。
    失败运行（评委不可用/契约违规）同样落一行：``status='failed'`` +
    ``error_code``，``aggregate`` 为空 —— 「测量失败」绝不伪装成 0 分。
    """

    __tablename__ = "eval_rubric_run"
    __table_args__ = (
        Index("ix_eval_rubric_run_org_created", "organization_id", "created_at"),
        Index("ix_eval_rubric_run_rubric_created", "rubric_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属组织（租户隔离键）",
    )
    rubric_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("eval_rubric.id"), nullable=False, comment="评分表 id"
    )
    rubric_version_no: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本次运行使用的不可变版本号"
    )
    dataset_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="用例集输入哈希（排序稳定）"
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False, comment="judge/fake")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="completed/failed")
    aggregate: Mapped[Any | None] = mapped_column(
        JSON, comment="聚合分数 + 逐问题通过率 + 校准报告（失败运行为空）"
    )
    results: Mapped[Any | None] = mapped_column(
        JSON, comment="逐条结果（失败运行可为空；仅组织内授权用户可见）"
    )
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    judge_model: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="评委模型名；fake 模式固定为 fake"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(64), comment="失败机器码（completed 为 NULL）"
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="运行发起人"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="完成时间（成功与失败都记录）"
    )
