"""检索评测测试集与运行历史（竞品对标 P2 / 下一批）。

一行用例 = 一个组织内的一条「query → 期望文档集合」；一行运行 = 显式持久化
的一次评测快照：

- 条款级（golden set + CI 门禁）：文件名 + 条号定义位，衡量「找对条款」；
- 文档级（本表，产品化「保存测试集 → 跑评测 → 看指标」）：衡量「找对文档」。

运行评测默认只读本表、结果不落库；只有显式 persist 才写 retrieval_eval_run
（不引入第二套判定真源）；删除按组织隔离，跨组织删除一律 404（不回显存在性）。

运行历史按组织隔离；逐条结果（含 query 原文，仅本组织授权用户可见）与指标、
case_ids + 用例集哈希一起固化，用于回归与趋势对比。case_set_hash 对用例
id + 金标集合 + query 取稳定哈希（排序、定分隔符），与 top_k 一起写入导出
文件：哈希相同即评测输入集合相同，跨运行指标可比。审计 detail 只记
org/user/条数与字段名，绝不写 query 或用例名。

去重（bad-case 捕获闭环）：组织内「同 query + 同金标集合」只保留一行。
``dedup_key`` 对该二元组取 sha256（query 去首尾空白、金标按集合排序），
``(organization_id, dedup_key)`` 唯一索引是 DB 层兜底；服务层先查再插，
并发撞唯一索引时回读既有行（幂等），绝不静默产生重复行。迁移 038 前已存在
的旧行 ``dedup_key`` 为 NULL（多行 NULL 不违反唯一索引），即历史重复不在
迁移里被强删；它们只是不再参与去重，新建行始终带键。
"""

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class RetrievalEvalCase(Base):
    """一条保存的检索评测用例（组织内可见，创建人审计用）。"""

    __tablename__ = "retrieval_eval_case"
    __table_args__ = (
        # 列表/运行/删除都按组织过滤并按创建时间取序；复合索引覆盖三者。
        Index("ix_retrieval_eval_case_org_created", "organization_id", "created_at"),
        # 组织内「同 query + 同金标集合」去重：dedup_key 为 NULL 的旧行不受约束
        # （MySQL/SQLite 唯一索引允许多行 NULL），新建行总带键。
        Index(
            "uq_retrieval_eval_case_org_dedup",
            "organization_id",
            "dedup_key",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属组织（租户隔离键）",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="用例名称")
    query: Mapped[str] = mapped_column(Text, nullable=False, comment="检索 query")
    expected_document_ids: Mapped[Any] = mapped_column(
        JSON, nullable=False, comment="期望命中的文档 id 列表（文档级金标）"
    )
    tags: Mapped[Any | None] = mapped_column(JSON, comment="标签列表")
    dedup_key: Mapped[str | None] = mapped_column(
        String(64),
        comment="组织内去重键：sha256(query 去首尾空白 + 金标集合排序)；历史行可为 NULL",
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="创建人"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )


class RetrievalEvalRun(Base):
    """一次持久化评测运行（组织内可见；跨组织读取一律 404）。"""

    __tablename__ = "retrieval_eval_run"
    __table_args__ = (Index("ix_retrieval_eval_run_org_created", "organization_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属组织（租户隔离键）",
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="运行发起人"
    )
    top_k: Mapped[int] = mapped_column(Integer, nullable=False, comment="本次运行检索深度")
    case_ids: Mapped[Any] = mapped_column(
        JSON, nullable=False, comment="本次运行的用例 id（用例顺序）"
    )
    case_scope: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="用例范围：all=全量 / subset=显式子集"
    )
    case_set_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="用例集合输入哈希（id+query+金标，排序稳定）"
    )
    counts: Mapped[Any] = mapped_column(
        JSON, nullable=False, comment="计数：cases/evaluated/errors"
    )
    metrics: Mapped[Any] = mapped_column(
        JSON, nullable=False, comment="指标：recall_at_k/mrr/document_hit_at_k/hit_at_k"
    )
    error_case_ids: Mapped[Any] = mapped_column(JSON, nullable=False, comment="失败用例 id 列表")
    results: Mapped[Any] = mapped_column(
        JSON, nullable=False, comment="逐条结果（仅组织内授权用户可见）"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
