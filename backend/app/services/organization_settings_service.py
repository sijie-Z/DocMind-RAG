"""组织设置服务（B4 / D5）。

单一职责：把「组织覆盖值 → 生效值」的解析集中在一处，避免每个消费点各写
一套 NULL 回退逻辑。所有读取方法都接受 ``organization_id=None``（无组织上下文）
并回退默认值——与 A2 的 fail-closed 边界一致：调用方必须先解析出组织，这里
只负责取值，不替调用方猜归属。

平台默认值的单一真源：
- Provider / 模型：跟随进程级 Settings（ENABLE_LOCAL_LLM 时取本地模型）；
- 检索 top_k：跟随 settings.TOP_K_RESULTS（搜索层默认 10；聊天层未设置时
  由调用方传入自己的既有默认 8 / 5，见 ``resolve_retrieval_top_k`` 的 fallback）；
- 隐私：默认允许（保持既有 privacyMode 默认 True 的行为）；
- 配额：默认 NULL（不限额）；工具白名单：默认 NULL（不限制）。

本期真实消费点：检索 top_k（知识库搜索 + RAG 聊天）、隐私策略（聊天）、
默认模型（RAG 聊天）。配额与工具白名单的强制执行由 C4 的 SettingsService
承接，本服务先把「生效值 / 来源 / 是否需重启」如实暴露给管理端。
"""
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.organization_settings import OrganizationSettings
from app.models.token_usage import TokenUsageRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SettingSpec:
    """一个设定项的元数据（管理端「生效值 / 来源 / 是否需重启」的数据源）。"""

    key: str
    default: Callable[[], Any]
    requires_restart: bool


def _platform_provider() -> str:
    return "local" if settings.ENABLE_LOCAL_LLM else "deepseek"


def _platform_model() -> str:
    return settings.LOCAL_LLM_MODEL if settings.ENABLE_LOCAL_LLM else settings.DEEPSEEK_MODEL


SETTING_SPECS: dict[str, SettingSpec] = {
    "default_provider": SettingSpec("default_provider", _platform_provider, True),
    # 默认模型按请求读取并透传给 LLM 调用，无需重启。
    "default_model": SettingSpec("default_model", _platform_model, False),
    "retrieval_top_k": SettingSpec(
        "retrieval_top_k", lambda: settings.TOP_K_RESULTS, False
    ),
    "allow_privacy_mode": SettingSpec("allow_privacy_mode", lambda: True, False),
    "monthly_token_quota": SettingSpec("monthly_token_quota", lambda: None, False),
    "tool_whitelist": SettingSpec("tool_whitelist", lambda: None, False),
}


@dataclass(frozen=True)
class RuntimePolicy:
    """一次请求内需要的一组运行策略（单次查询解析，避免多次回表）。"""

    retrieval_top_k: int | None
    allow_privacy_mode: bool
    default_model: str


class OrganizationSettingsService:
    # ── 读取 ──────────────────────────────────────────────────────────

    async def get_row(
        self, db: AsyncSession, organization_id: int | None
    ) -> OrganizationSettings | None:
        if organization_id is None:
            return None
        stmt = select(OrganizationSettings).where(
            OrganizationSettings.organization_id == int(organization_id)
        )
        return (await db.execute(stmt)).scalars().first()

    async def get_effective_settings(
        self, db: AsyncSession, organization_id: int | None
    ) -> dict[str, dict[str, Any]]:
        """返回每个设定项的 ``{value, source, requires_restart}``。

        ``source`` 只有两个取值：``organization``（组织覆盖）与 ``default``
        （平台默认）。显式写入的 False / 0 / [] 都算组织覆盖。
        """
        row = await self.get_row(db, organization_id)
        out: dict[str, dict[str, Any]] = {}
        for key, spec in SETTING_SPECS.items():
            raw = getattr(row, key, None) if row is not None else None
            if key == "tool_whitelist":
                value = self.parse_tool_whitelist(raw)
            else:
                value = raw if raw is not None else spec.default()
            out[key] = {
                "value": value,
                "source": "organization" if raw is not None else "default",
                "requires_restart": spec.requires_restart,
            }
        return out

    async def resolve_retrieval_top_k(
        self, db: AsyncSession, organization_id: int | None, fallback: int
    ) -> int:
        """检索 top_k 的真实消费入口。

        组织未设置（或行不存在 / 无组织上下文）时返回调用方既有默认值，
        绝不改变既有默认语义。防御性钳制在 [1, 50]（与请求 schema 同界）。
        """
        row = await self.get_row(db, organization_id)
        value = getattr(row, "retrieval_top_k", None) if row is not None else None
        if value is None:
            return int(fallback)
        return max(1, min(int(value), 50))

    async def allow_privacy_mode(
        self, db: AsyncSession, organization_id: int | None
    ) -> bool:
        """组织是否允许成员开启隐私模式；未设置 = 允许（保持既有默认）。"""
        row = await self.get_row(db, organization_id)
        if row is None or row.allow_privacy_mode is None:
            return True
        return bool(row.allow_privacy_mode)

    async def get_effective_model(
        self, db: AsyncSession, organization_id: int | None
    ) -> str:
        row = await self.get_row(db, organization_id)
        if row is not None and row.default_model:
            return row.default_model
        return _platform_model()

    async def get_runtime_policy(
        self, db: AsyncSession, organization_id: int | None
    ) -> RuntimePolicy:
        """一次查询解析聊天链路需要的全部设定。"""
        row = await self.get_row(db, organization_id)
        top_k = getattr(row, "retrieval_top_k", None) if row is not None else None
        privacy = (
            True
            if row is None or row.allow_privacy_mode is None
            else bool(row.allow_privacy_mode)
        )
        model = row.default_model if row is not None and row.default_model else _platform_model()
        return RuntimePolicy(
            retrieval_top_k=(
                max(1, min(int(top_k), 50)) if top_k is not None else None
            ),
            allow_privacy_mode=privacy,
            default_model=model,
        )

    async def get_monthly_usage(
        self, db: AsyncSession, organization_id: int | None
    ) -> int:
        """本月已用 token（input + output）；仅用于管理端展示配额进度。"""
        if organization_id is None:
            return 0
        month_start = datetime.now(UTC).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        stmt = select(
            func.coalesce(
                func.sum(
                    TokenUsageRecord.input_tokens + TokenUsageRecord.output_tokens
                ),
                0,
            )
        ).where(
            TokenUsageRecord.organization_id == int(organization_id),
            TokenUsageRecord.created_at >= month_start,
        )
        return int((await db.execute(stmt)).scalar() or 0)

    # ── 序列化 / 解析 ─────────────────────────────────────────────────

    @staticmethod
    def parse_tool_whitelist(raw: str | None) -> list[str] | None:
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("organization_settings.tool_whitelist 不是合法 JSON，按未设置处理")
            return None
        if not isinstance(data, list):
            return None
        return [item for item in data if isinstance(item, str)]

    @staticmethod
    def serialize_tool_whitelist(values: list[str] | None) -> str | None:
        if values is None:
            return None
        return json.dumps(values, ensure_ascii=False)

    # ── 写入 ──────────────────────────────────────────────────────────

    async def update_settings(
        self,
        db: AsyncSession,
        organization_id: int,
        updates: dict[str, Any],
    ) -> tuple[OrganizationSettings | None, dict[str, dict[str, Any]]]:
        """应用部分更新（键必须属于 SETTING_SPECS）。

        返回 (行, 变更集)。变更集只包含真正变化的键，供审计使用；
        值语义：``None`` = 清除覆盖（回退平台默认），``[]`` = 显式「无工具」
        （区别于未设置）。空更新不会创建空行。
        """
        known_updates = {
            key: value for key, value in updates.items() if key in SETTING_SPECS
        }
        if not known_updates:
            return await self.get_row(db, organization_id), {}

        row = await self.get_row(db, organization_id)
        if row is None:
            # 显式 null = 清除覆盖；行本就不存在时是无操作，不凭空建一行。
            if all(value is None for value in known_updates.values()):
                return None, {}
            row = OrganizationSettings(organization_id=organization_id)
            try:
                # 并发首次写入：唯一约束冲突时回滚到 savepoint 后重读对手行，
                # 不把正常竞态打成 500，也不丢外层事务里的其它写入。
                async with db.begin_nested():
                    db.add(row)
                    await db.flush()
            except IntegrityError:
                row = await self.get_row(db, organization_id)
                if row is None:
                    raise

        changes: dict[str, dict[str, Any]] = {}
        for key, value in known_updates.items():
            if key == "tool_whitelist":
                new_value = self.serialize_tool_whitelist(value)
            else:
                new_value = value
            old_value = getattr(row, key)
            if old_value == new_value:
                continue
            changes[key] = {"old": old_value, "new": new_value}
            setattr(row, key, new_value)

        await db.commit()
        await db.refresh(row)
        return row, changes


organization_settings_service = OrganizationSettingsService()
