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
默认模型（RAG 聊天）；**月度 token 配额与工具白名单的强制执行**由本服务
（批次十四 C4 运行策略）承接：配额在各 LLM 入口开流前检查（超管旁路并
审计），白名单经 ``AgentConfig.tool_whitelist`` 注入 schema 与执行层。
"""

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.exceptions import QuotaExceededError
from app.models.organization_settings import OrganizationSettings
from app.models.token_usage import TokenUsageRecord
from app.services.audit_service import audit_service

logger = logging.getLogger(__name__)


def _as_integer_scalar(value: Any) -> int | None:
    """DB 标量 → int；只接受真实 int 或**整数值 Decimal**。

    MySQL 的 ``SUM()`` / ``COALESCE(SUM(), 0)`` 经 aiomysql 返回 ``Decimal``
    （本机 MySQL 8 实测），SQLite 返回 int——两者都必须参与配额判定，否则生产
    配额永不触发。bool / float / 非整数 Decimal / 其它类型一律返回 ``None``，
    由调用方按读取失败（fail-open + warning）处理；绝不 ``int(value)`` 静默
    转换任意类型。
    """
    if type(value) is int:
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return None
        if value != value.to_integral_value():
            return None
        return int(value)
    return None


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
    "retrieval_top_k": SettingSpec("retrieval_top_k", lambda: settings.TOP_K_RESULTS, False),
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

    async def allow_privacy_mode(self, db: AsyncSession, organization_id: int | None) -> bool:
        """组织是否允许成员开启隐私模式；未设置 = 允许（保持既有默认）。"""
        row = await self.get_row(db, organization_id)
        if row is None or row.allow_privacy_mode is None:
            return True
        return bool(row.allow_privacy_mode)

    async def get_effective_model(self, db: AsyncSession, organization_id: int | None) -> str:
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
            True if row is None or row.allow_privacy_mode is None else bool(row.allow_privacy_mode)
        )
        model = row.default_model if row is not None and row.default_model else _platform_model()
        return RuntimePolicy(
            retrieval_top_k=(max(1, min(int(top_k), 50)) if top_k is not None else None),
            allow_privacy_mode=privacy,
            default_model=model,
        )

    async def get_monthly_usage(self, db: AsyncSession, organization_id: int | None) -> int:
        """本月已用 token（input + output）；管理端展示 + 配额判定共用。"""
        if organization_id is None:
            return 0
        month_start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        stmt = select(
            func.coalesce(
                func.sum(TokenUsageRecord.input_tokens + TokenUsageRecord.output_tokens),
                0,
            )
        ).where(
            TokenUsageRecord.organization_id == int(organization_id),
            TokenUsageRecord.created_at >= month_start,
        )
        value = (await db.execute(stmt)).scalar()
        if value is None:
            return 0
        # 值级闸门（与 AgentConfig #319 同族）：MySQL 返回 Decimal、SQLite 返回
        # int；只接受真实 int / 整数值 Decimal。其它类型（替身/驱动漂移）不得
        # 被 int() 静默转成 1 而误判配额；如实按 0 处理并留 warning。
        coerced = _as_integer_scalar(value)
        if coerced is None:
            # CodeQL：DB 标量类型（派生值）按敏感数据处理——完全固定文案、无参数。
            logger.warning("组织月度用量读取值类型漂移，按 0 处理")
            return 0
        return max(0, coerced)

    async def get_tool_whitelist(
        self, db: AsyncSession, organization_id: int | None
    ) -> list[str] | None:
        """组织工具白名单（规范名，``None`` = 不限制）。

        解析失败按「未设置」处理并留 warning（与 ``parse_tool_whitelist`` 同一
        口径）；NULL / 空数组语义严格区分：``None`` 不限制，``[]`` = 无工具。
        """
        row = await self.get_row(db, organization_id)
        raw = getattr(row, "tool_whitelist", None) if row is not None else None
        return self.parse_tool_whitelist(raw)

    @staticmethod
    def merge_tool_whitelists(
        organization: list[str] | None, caller: list[str] | None
    ) -> list[str] | None:
        """合并组织白名单与调用方白名单：结果**永不比任一方更宽**。

        - 组织 None（未限制）→ 保留调用方现有名单（调用方更严格）；
        - 调用方 None（未限制）→ 采用组织名单（组织是唯一限制）；
        - 两者都非 None → 交集（按调用方顺序，规范名精确匹配）；
        - 任一方为 ``[]`` → 交集为空（显式「无工具」不得被另一方放宽）。
        """
        if organization is None:
            return caller
        if caller is None:
            return organization
        allowed = set(organization)
        return [name for name in caller if name in allowed]

    # ── 运行期策略强制执行 ──────────────────────────────────────────────

    @staticmethod
    def _next_month_start_utc(now: datetime | None = None) -> datetime:
        """下月 1 日 00:00 UTC（配额重置时点）。"""
        current = now or datetime.now(UTC)
        if current.month == 12:
            return datetime(current.year + 1, 1, 1, tzinfo=UTC)
        return datetime(current.year, current.month + 1, 1, tzinfo=UTC)

    async def ensure_monthly_quota(
        self,
        db: AsyncSession,
        organization_id: int | None,
        *,
        user: Any = None,
        request: Any = None,
    ) -> None:
        """入口级月度 token 配额检查（批次十四 C4）。

        语义（与计划书一致）：
        - 无组织上下文 / 配额 NULL → 放行（旧行为不变：不限额）；
        - 本月 ``used >= quota`` → ``QuotaExceededError``（429，
          ``ORG_TOKEN_QUOTA_EXCEEDED``，detail 带 used/quota/reset_at）；
        - 显式 ``user.is_superuser`` → 放行，并写审计
          ``org_token_quota_bypass``（审计失败只 warning，不反噬请求）；
        - **读取失败 fail-open**：管理面策略库抖动不得把正常聊天打死；配额
          按 NULL（不限额）处理并留 warning 供观测。

        v1 **不预扣**：并发请求只在进入时各读一次 SUM，允许短暂超限（计划书
        明确接受）。本方法必须在任何 LLM 调用 / SSE 首帧之前调用。
        """
        if organization_id is None:
            return
        try:
            row = await self.get_row(db, organization_id)
            raw_quota = getattr(row, "monthly_token_quota", None) if row is not None else None
            if raw_quota is None:
                return
            # 值级闸门：接受真实 int 与整数值 Decimal（MySQL 驱动口径）；
            # bool / float / 非整数 Decimal 等一律按未设置 fail-open，绝不
            # int() 静默转换成 1 而误判「已超限」。
            quota = _as_integer_scalar(raw_quota)
            if quota is None:
                # CodeQL：同 get_monthly_usage——完全固定文案、无参数。
                logger.warning("组织月度配额值类型漂移，按未设置处理")
                return
            used = await self.get_monthly_usage(db, organization_id)
        except Exception:  # noqa: BLE001 — fail-open 是显式策略
            # CodeQL：异常消息可能含查询值/凭据——完全固定文案、无参数。
            logger.warning("组织月度配额读取失败（fail-open，按不限额处理）")
            return

        if used < quota:
            return

        if user is not None and bool(getattr(user, "is_superuser", False)):
            await self._audit_quota_bypass(
                organization_id=organization_id,
                user=user,
                used=used,
                quota=quota,
                request=request,
            )
            return

        raise QuotaExceededError(
            detail={
                "used": used,
                "quota": quota,
                "reset_at": self._next_month_start_utc().isoformat(),
            }
        )

    @staticmethod
    async def _audit_quota_bypass(
        *,
        organization_id: int,
        user: Any,
        used: int,
        quota: int,
        request: Any = None,
    ) -> None:
        """超管旁路审计（best-effort：审计失败只 warning）。"""
        try:
            await audit_service.log_activity(
                int(getattr(user, "id", 0) or 0),
                "org_token_quota_bypass",
                target_type="organization_settings",
                target_id=str(organization_id),
                detail=json.dumps(
                    {"organization_id": int(organization_id), "used": used, "quota": quota},
                    ensure_ascii=False,
                ),
                request=request,
            )
        except Exception:  # noqa: BLE001 — 审计是旁路，不反噬请求
            # CodeQL：不记录异常消息/对象——完全固定文案、无参数。
            logger.warning("超管配额旁路审计写入失败")

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
        known_updates = {key: value for key, value in updates.items() if key in SETTING_SPECS}
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
