"""检索评测测试集（竞品对标 P2）：保存的「query → 期望文档集合」。

一行 = 一个组织内的一条检索评测用例；``expected_document_ids`` 是**文档级**
金标（只认 document_id，不绑条号/文件名），与 ``backend/evals`` 的条款级
golden set 互补：

- 条款级（golden set + CI 门禁）：文件名 + 条号定义位，衡量「找对条款」；
- 文档级（本表，产品化「保存测试集 → 跑评测 → 看指标」）：衡量「找对文档」。

运行评测默认只读本表、结果不落库（不引入第二套判定真源）；删除按组织
隔离，跨组织删除一律 404（不回显存在性）。
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
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="创建人"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )
