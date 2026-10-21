"""组织级运行设定（D5，B4 四层设定骨架）。

一行 = 一个组织的设定覆盖集。所有列可空：``NULL`` 表示「未覆盖，回退平台
默认」，与 A2 的「无主行不猜归属」同一纪律——绝不把缺省值写成显式值，
否则「生效值 / 来源」两层信息会丢失。

最小集（本期）：
- ``default_provider`` / ``default_model``：默认模型 / Provider 引用；
- ``retrieval_top_k``：检索条数（知识库搜索与 RAG 聊天共用；未设置时各消费
  点保持既有默认：搜索 10、聊天文档 8 / 通用 5）；
- ``allow_privacy_mode``：是否允许成员开启隐私模式（NULL/True = 允许）；
- ``monthly_token_quota``：组织月度 token 配额（NULL = 不限额）；
- ``tool_whitelist``：工具白名单 JSON 数组（NULL = 不限制）。

运行期消费落地节奏：``retrieval_top_k``、``allow_privacy_mode``、
``default_model`` 由 B4 接线；配额展示与工具白名单的强制执行由后续
SettingsService（C4）承接。
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class OrganizationSettings(Base):
    """组织设定覆盖集（每个组织至多一行）。"""

    __tablename__ = "organization_settings"
    __table_args__ = (UniqueConstraint("organization_id", name="uq_organization_settings_org"),)

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    organization_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属组织（租户根）；随组织删除级联清理",
    )
    default_provider: Mapped[str | None] = mapped_column(
        String(32), comment="默认 Provider 引用；NULL=平台默认"
    )
    default_model: Mapped[str | None] = mapped_column(
        String(100), comment="默认模型；NULL=平台默认"
    )
    retrieval_top_k: Mapped[int | None] = mapped_column(
        Integer, comment="检索 top_k；NULL=各消费点既有默认"
    )
    allow_privacy_mode: Mapped[bool | None] = mapped_column(
        Boolean, comment="是否允许成员开启隐私模式；NULL/True=允许"
    )
    monthly_token_quota: Mapped[int | None] = mapped_column(
        Integer, comment="月度 token 配额；NULL=不限额"
    )
    tool_whitelist: Mapped[str | None] = mapped_column(
        Text, comment="工具白名单 JSON 数组；NULL=不限制"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )
