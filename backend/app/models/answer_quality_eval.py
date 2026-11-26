"""答案质量评测运行历史（LLM-as-judge 的 faithfulness/relevancy/context precision）。

与既有评测面的分工（刻意不重叠）：

- ``app.rag.evaluator`` 是**评分核心**：固定三个连续指标 + 评委提示词。本模块
  只负责把一次运行**固化**下来，不复制任何评分/提示词逻辑；
- ``retrieval_eval`` 衡量检索是否找对文档（recall@k / MRR）；本模块衡量候选
  答案的生成质量，输入是 question/answer/context，不读检索评测表；
- ``eval_rubric`` 是用户可编辑的加权 yes/no 评分表（离散判定 + 校准）；本模块
  是固定维度的连续分评委。两者都复用「不可变运行 + 显式失败」的产品形态，
  但判定对象、输入与指标不同，刻意不共用运行表。

隐私边界：运行行只保存聚合/逐条指标、有界评审理由、评委模型与提示词版本，
以及来源的哈希与脱敏预览。**原始 query/answer/context 文本绝不落库**。

失败语义：评委不可用 → ``status='failed'`` + ``error_code``，``aggregate`` /
``results`` 为 NULL。绝不把「测量失败」写成 0 分。

幂等键（迁移 043 的 ``uq_answer_quality_eval_run_config``）：同一组织/用户/
输入哈希/评委模型/提示词版本/模式/状态下至多一行。成功运行重复提交是幂等
重放；失败后再跑同一配置成功可新增 completed 行（允许恢复），再次失败则回读
既有 failed 行。
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
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

ANSWER_QUALITY_MODES = ("judge", "fake")
"""运行模式：judge=LLM 评委；fake=确定性回放（测试/CI，不调用模型）。"""

ANSWER_QUALITY_STATUSES = ("completed", "failed")
"""运行状态：失败运行保留机器码，不产生任何分数。"""


class AnswerQualityEvalRun(Base):
    """一次答案质量评测运行（组织内可见；跨组织读取一律 404）。

    行是不可变快照：``dataset_hash`` 对输入用例排序后取稳定哈希，
    ``results`` 固化逐条指标/理由/来源哈希，``aggregate`` 固化均值。
    失败运行同样落一行：``status='failed'`` + ``error_code``，分数列为空。
    """

    __tablename__ = "answer_quality_eval_run"
    __table_args__ = (
        Index("ix_answer_quality_eval_run_org_created", "organization_id", "created_at"),
        Index(
            "ix_answer_quality_eval_run_org_user_created",
            "organization_id",
            "user_id",
            "created_at",
        ),
        Index("ix_answer_quality_eval_run_org_status", "organization_id", "status"),
        UniqueConstraint(
            "organization_id",
            "user_id",
            "dataset_hash",
            "judge_model",
            "prompt_version",
            "mode",
            "status",
            name="uq_answer_quality_eval_run_config",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        comment="所属组织（租户隔离键）",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        comment="运行归属用户（列表/趋势的用户过滤键）",
    )
    dataset_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        comment="评测输入哈希（case_id+问题+答案+上下文+回放分数，排序稳定）",
    )
    judge_model: Mapped[str] = mapped_column(
        String(128), nullable=False, comment="评委模型名；fake 模式固定为 fake"
    )
    prompt_version: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="评委提示词版本（评分口径可追溯）"
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False, comment="judge/fake")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="completed/failed")
    case_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    aggregate: Mapped[Any | None] = mapped_column(
        JSON, comment="聚合指标（失败运行为空，绝不写 0 分）"
    )
    results: Mapped[Any | None] = mapped_column(
        JSON, comment="逐条指标/理由/来源哈希（失败运行为空；不含原始文本）"
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
