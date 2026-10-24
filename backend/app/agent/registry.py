"""Tool Registry v2 — self-registering tool system with hooks and unified results.

Architecture:
    ToolEntry                     — metadata + handler for one tool
    ToolResult / ToolError        — unified return structure
    pre_hooks / post_hooks        — pluggable pipeline (auth, error mapping)

Backward compatible:
    @register_tool  decorator    — unchanged signature (new fields are optional)
    tool_registry.execute()      — still returns str
    New: tool_registry.execute_detailed()  — returns ToolResult

Usage:
    from app.agent.registry import register_tool, tool_registry, ToolResult

    @register_tool(
        name="feishu_table_query",
        description="查询飞书多维表格记录",
        parameters={...},
        output_schema={           # new v2 field
            "type": "object",
            "properties": {
                "records": {"type": "array", "items": {"type": "object"}},
                "total": {"type": "integer"},
            },
        },
        auth_handler="feishu_tenant_token",
        rate_limit=10,            # max 10 calls/second
        timeout=30.0,
    )
    async def feishu_table_query(app_token: str, table_id: str, **ctx) -> ToolResult | str:
        ...
        return ToolResult.success(data={"records": [...], "total": 42})
"""

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any, Literal

from app.agent.schema_validation import validate_against_schema

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 1.  Unified Return Types
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolError:
    """Structured error information returned by a tool.

    code: one of 27 ToolErrorType literals (§13.3 agent-reliability-design.md)
    """

    # ── Union of all known error types ──────────────────────────────────
    ToolErrorType = Literal[
        "timeout",
        # issue #206：配置的沙箱不可用（auto/docker 而 Docker 不可达）——fail-closed，
        # 绝不静默降级为进程内执行。
        "sandbox_unavailable",
        "rate_limited",
        "api_error",
        "connection_error",
        "not_found",
        "permission_denied",
        "validation_error",
        "auth_expired",
        # 以下三个此前只被赋值、未入 Literal（issue #242 第 5 条：契约漂移）
        "auth_expired_refreshable",
        "auth_required",
        "hook_error",
        "budget_exceeded",
        "ambiguous_input",
        "unreachable",
        "interrupted",
        "permanent_transport_error",
        "empty_response",
        "idle_timeout",
        "serialization_error",
        "context_length_exceeded",
        "max_tokens_truncation",
        "payload_too_large",
        "image_processing_error",
        "doom_loop_detected",
        "invalid_configuration",
        "unknown",  # fallback — never test against this, always classify
    ]

    code: ToolErrorType  # restructured — must be one of above
    message: str  # human-readable (Chinese preferred)
    raw: Any = None  # original exception / API error body for debugging

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "raw": str(self.raw)[:500] if self.raw else None,
        }


@dataclass
class ToolMeta:
    """Execution metadata attached to every tool call."""

    latency_ms: float = 0.0
    tool_name: str = ""
    retry_count: int = 0
    # issue #206：代码执行类工具的 resolved sandbox（"ast" / "docker"）。
    # 由工具自行写入（execute_python），executor 原样透传到 SSE tool_result 的
    # `sandbox` 字段——部署侧可据此确认实际执行面。
    sandbox: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "latency_ms": round(self.latency_ms, 1),
            "tool_name": self.tool_name,
            "retry_count": self.retry_count,
        }
        if self.sandbox:
            d["sandbox"] = self.sandbox
        return d


@dataclass
class ToolResult:
    """Unified return structure for all tools.

    Two construction helpers:
        ToolResult.success(data=...)   → success=True
        ToolResult.error(code, msg)    → success=False, error populated

    When converted to str for backward-compatible callers:
        - success → json.dumps(data) if data else "OK"
        - failure → "Error: [{code}] {message}"
    """

    success: bool
    data: Any = None
    error: ToolError | None = None
    meta: ToolMeta = field(default_factory=ToolMeta)

    @classmethod
    def ok(cls, data: Any = None, meta: ToolMeta | None = None) -> "ToolResult":
        """Factory: successful result."""
        return cls(success=True, data=data, meta=meta or ToolMeta())

    @classmethod
    def fail(
        cls, code: str, message: str, raw: Any = None, meta: ToolMeta | None = None
    ) -> "ToolResult":
        """Factory: failed result with structured error."""
        return cls(
            success=False,
            error=ToolError(code=code, message=message, raw=raw),
            meta=meta or ToolMeta(),
        )

    def __str__(self) -> str:
        """Backward-compatible string representation for old callers.

        The executor checks `result.startswith("Error:")` to detect failures,
        so we preserve that contract exactly.
        """
        if not self.success and self.error:
            return f"Error: [{self.error.code}] {self.error.message}"
        if isinstance(self.data, str):
            return self.data
        try:
            return json.dumps(self.data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(self.data) if self.data is not None else "OK"

    def to_dict(self) -> dict[str, Any]:
        """Full serialisation for logging / observability."""
        return {
            "success": self.success,
            "data": str(self.data)[:2000] if self.data is not None else None,
            "error": self.error.to_dict() if self.error else None,
            "meta": self.meta.to_dict(),
        }


# ──────────────────────────────────────────────────────────────────────────────
# 2.  Tool aliases（issue #509：单一别名映射）
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ToolAlias:
    """旧工具名 → 规范工具名（可携带默认参数）。

    安全契约（P0）：schema **只发规范名**；executor / skills / approval / ledger /
    SSE 在判定或记录之前都先经 ``canonicalize_tool_call``。旧名因此不会绕过风险
    分级、审批门、terminal 强制审批、受限白名单或禁用清单——审批看到的是规范名
    （如 ``utility``），执行的是同一规范 handler，别名不会把 high 工具伪装成 low。
    """

    target: str
    default_arguments: dict[str, Any] = field(default_factory=dict)


# 单一事实源：第二步「语义同族合并」后保留的旧名兼容入口。
# 新增条目必须同时确认：目标工具已注册、默认参数与旧工具语义一致。
TOOL_ALIASES: dict[str, ToolAlias] = {
    "vector_search": ToolAlias("search_knowledge_base", {"mode": "vector"}),
    "fetch_webpage": ToolAlias("web_search", {"action": "fetch"}),
    "list_skills": ToolAlias("load_skill_reference"),
    "summarize_document": ToolAlias("extract_insights", {"mode": "summary"}),
    "calculate": ToolAlias("utility", {"operation": "calculate"}),
    "format_converter": ToolAlias("utility", {"operation": "format_converter"}),
    "get_current_time": ToolAlias("utility", {"operation": "get_current_time"}),
    "detect_language": ToolAlias("utility", {"operation": "detect_language"}),
    "analyze_data": ToolAlias("utility", {"operation": "analyze_data"}),
}


def canonical_tool_name(name: str) -> str:
    """别名 → 规范名（非别名原样返回）。无参数场景（风险/白名单/tag 判定）使用。"""
    alias = TOOL_ALIASES.get(str(name or ""))
    return alias.target if alias is not None else str(name or "")


def canonicalize_tool_call(
    name: str, arguments: dict[str, Any] | None
) -> tuple[str, dict[str, Any]]:
    """把一次工具调用规范化为 ``(规范名, 合并默认参数后的参数)``。

    显式参数优先于别名默认参数，但显式 ``None`` 不覆盖别名受控默认值
    （旧客户端把可选字段序列化为 null 时，不得把 ``mode=vector`` 静默降级成
    默认 hybrid，也不得把 ``action=fetch`` 变成未知 action）。
    """
    args = dict(arguments) if isinstance(arguments, dict) else {}
    alias = TOOL_ALIASES.get(str(name or ""))
    if alias is None:
        return str(name or ""), args
    merged = dict(alias.default_arguments)
    for key, value in args.items():
        if value is None and key in merged:
            continue
        merged[key] = value
    return alias.target, merged


def extended_surface_enabled(config: Any) -> bool:
    """默认常驻面之外的「扩展工具」是否注入（issue #509 第二步）。

    语义：``tool_tags`` 为 None/空 = 默认常驻档（只注入 10–12 个核心工具）；
    显式非空 tool_tags（受限上下文 / 操作者显式选择）= 解锁对应 tag 的扩展工具
    （最终仍受 tag 交集、disabled_tools 与其它 enabled_predicate 约束）。
    这是显式 opt-in，不改变 tag 过滤本身；空列表不视为 opt-in。
    """
    return bool(getattr(config, "tool_tags", None))


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Tool Entry
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class ToolEntry:
    """A registered tool with its metadata and handler.

    v2 additions (all optional, backward compatible):
        output_schema    — declare what the tool returns (JSON Schema)
        auth_handler     — name of the auth strategy ("feishu_tenant_token", "api_key", ...)
        rate_limit       — max calls per second (None = no limit)
        timeout          — execution timeout in seconds (default 30.0)
        category         — business domain ("feishu", "dingtalk", "search", ...)
        semantic         — behavioural semantics for Agent planning (see below)

    v2.3 surface gating（issue #509）:
        hidden              — 已合并/隐藏的旧名：保留注册与 handler（不删工具），
                              但**永不进 schema**；旧名经 TOOL_ALIASES 路由到规范名。
        superseded_by       — 隐藏条目的规范替代名（manifest 展示用）。
        enabled_predicate   — ``enabled_predicate(config) -> bool``：按运行配置/环境
                              判定是否注入 schema（终端/飞书/委派/扩展面）。谓词抛异常
                              按不可用（False）处理——门控失败必须 fail-closed。
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, str | ToolResult]]
    tags: list[str] = field(default_factory=list)
    requires_auth: bool = False

    # ── v2 fields ──
    output_schema: dict[str, Any] | None = None
    auth_handler: str | None = None  # "feishu_tenant_token" | "dingtalk_token" | "api_key" | None
    rate_limit: int | None = None  # max QPS
    timeout: float = 30.0

    # ── v2.1 semantic fields ──
    category: str = ""  # business domain for tool grouping
    # ── v2.2 risk declaration（HITL 审批门的判定输入，见 app/agent/approval.py）──
    # 工具作者显式声明的风险等级："low" | "medium" | "high"；"" = 未声明
    # （由 approval.TOOL_RISK_LEVELS 集中判定表 + 语义兜底补齐）。
    risk_level: str = ""
    semantic: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "read_only",  # read_only | mutating | long_running
            "idempotent": True,  # safe to retry without side effects
            "retry_safe": True,  # can auto-retry on transient errors
            "consistency": "eventual",  # strong | eventual
            "parallel_safe": True,  # safe to call concurrently
        }
    )

    # ── v2.3 surface gating（issue #509）──
    hidden: bool = False
    superseded_by: str = ""
    enabled_predicate: Callable[[Any], bool] | None = None

    def is_enabled(self, config: Any) -> bool:
        """该工具在给定配置下是否应注入 schema（隐藏/谓词/异常 → False）。"""
        if self.hidden:
            return False
        predicate = self.enabled_predicate
        if predicate is None:
            return True
        try:
            return bool(predicate(config))
        except Exception as e:  # noqa: BLE001 — 门控失败必须 fail-closed
            logger.warning(
                "工具 %s 的 enabled_predicate 执行失败（按不可用处理）: %s", self.name, e
            )
            return False

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling schema."""
        schema: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self._build_description(),
                "parameters": self.parameters,
            },
        }
        return schema

    def _build_description(self) -> str:
        """Augment description with execution hints for the LLM."""
        parts = [self.description]
        if self.output_schema:
            # Briefly hint at return structure
            props = list(self.output_schema.get("properties", {}).keys())
            if props:
                parts.append(f"返回字段: {', '.join(props[:5])}")
        if self.timeout and self.timeout < 60:
            parts.append(f"(超时{int(self.timeout)}s)")
        return " | ".join(parts)

    def to_manifest(self) -> dict[str, Any]:
        """Full manifest for registry introspection / admin UI."""
        return {
            "name": self.name,
            "description": self.description,
            "tags": self.tags,
            "category": self.category,
            "semantic": self.semantic,
            "risk_level": self.risk_level,
            "requires_auth": self.requires_auth,
            "auth_handler": self.auth_handler,
            "rate_limit": self.rate_limit,
            "timeout": self.timeout,
            "parameters": self.parameters,
            "output_schema": self.output_schema,
            # issue #509：隐藏/合并条目对管理面可见（可解释"为什么 schema 里没有"）。
            "hidden": self.hidden,
            "superseded_by": self.superseded_by,
            "gated_by_predicate": self.enabled_predicate is not None,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 4.  Hook Types
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class HookContext:
    """Context passed to pre/post hooks for a single tool execution."""

    tool_name: str
    arguments: dict[str, Any]
    entry: ToolEntry
    # Extra context forwarded from execute() caller (org_id, user_id, etc.)
    extra: dict[str, Any] = field(default_factory=dict)


# Pre-hook:  (HookContext) -> None | raise
#   Raise ToolAbortError to short-circuit with a specific error.
#   Mutate HookContext.arguments / HookContext.extra to inject auth tokens, etc.
PreHook = Callable[[HookContext], Coroutine[Any, Any, None]]

# Post-hook: (ToolResult, HookContext) -> None
#   Mutate ToolResult to remap errors, enrich metadata, log.
PostHook = Callable[[ToolResult, HookContext], Coroutine[Any, Any, None]]


class ToolAbortError(Exception):
    """Raised by a pre-hook to abort tool execution with a structured error."""

    def __init__(self, code: str, message: str, raw: Any = None):
        self.code = code
        self.message = message
        self.raw = raw
        super().__init__(f"[{code}] {message}")


# ──────────────────────────────────────────────────────────────────────────────
# 5.  The Registry
# ──────────────────────────────────────────────────────────────────────────────


class ToolRegistry:
    """Central registry of all available tools with pluggable hook pipeline.

    Built-in hooks (registered by default):
        - auth:          enforces `requires_auth` declarations
        - error_mapper:  maps common exception types to ToolError codes

    Additional hooks can be added via register_pre_hook / register_post_hook.

    #204：进程内 5000 条 ring buffer 已删除。它没有任何读者，耐久性由
    `tool_ledger`（DB 持久化，PR #422）承担——不做第二套持久化。
    """

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}
        # Hook pipelines
        self._pre_hooks: list[PreHook] = []
        self._post_hooks: list[PostHook] = []

        # Register built-in hooks
        self._pre_hooks.append(self._builtin_auth_hook)
        self._post_hooks.append(self._builtin_error_mapper_hook)

    # ── Registration ──────────────────────────────────────────────────────

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Coroutine[Any, Any, str | ToolResult]],
        tags: list[str] | None = None,
        requires_auth: bool = False,
        # v2 optional fields
        output_schema: dict[str, Any] | None = None,
        auth_handler: str | None = None,
        rate_limit: int | None = None,
        timeout: float = 30.0,
        # v2.1 semantic fields
        category: str = "",
        semantic: dict[str, Any] | None = None,
        # v2.2 risk declaration（HITL：审批门判定输入）
        risk_level: str = "",
        # v2.3 surface gating（issue #509）
        hidden: bool = False,
        superseded_by: str = "",
        enabled_predicate: Callable[[Any], bool] | None = None,
    ) -> ToolEntry:
        """Register a tool. All v2/v2.1 params are optional — zero migration needed."""
        resolved_semantic = (
            semantic
            if semantic is not None
            else {
                "type": "read_only",
                "idempotent": True,
                "retry_safe": True,
                "consistency": "eventual",
                "parallel_safe": True,
            }
        )
        entry = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            tags=tags or [],
            requires_auth=requires_auth,
            # v2
            output_schema=output_schema,
            auth_handler=auth_handler,
            rate_limit=rate_limit,
            timeout=timeout,
            # v2.1
            category=category,
            semantic=resolved_semantic,
            # v2.2
            risk_level=risk_level,
            # v2.3
            hidden=hidden,
            superseded_by=superseded_by,
            enabled_predicate=enabled_predicate,
        )
        self._tools[name] = entry
        logger.info(
            "Registered tool: %s%s",
            name,
            f" [auth={auth_handler}, qps={rate_limit}]" if auth_handler or rate_limit else "",
        )
        return entry

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def unregister(self, name: str) -> bool:
        """移除一个已注册工具（动态工具生命周期管理，目前消费者：MCP 客户端）。

        静态工具（@register_tool 装饰器注册）不依赖本方法；MCP 管理器在
        server 断开 / tools/list 变更时用它对动态工具做精确下线。

        Returns:
            True 表示该工具存在且已移除；False 表示本就不存在（幂等）。
        """
        removed = self._tools.pop(name, None)
        if removed is not None:
            logger.info("Unregistered tool: %s", name)
        return removed is not None

    def list_tools(
        self, tags: list[str] | None = None, *, include_hidden: bool = False
    ) -> list[ToolEntry]:
        """按 tag 过滤列出工具（默认不含 hidden 旧名；管理面可显式包含）。"""
        tools = [t for t in self._tools.values() if include_hidden or not t.hidden]
        if not tags:
            return tools
        return [t for t in tools if any(tag in t.tags for tag in tags)]

    @staticmethod
    def _disabled_names(config: Any) -> set[str]:
        """schema 过滤口径：只认规范名。

        issue #509 评审修正：旧名（calculate / vector_search ...）只用于**旧名
        调用**的阻断（executor 按 origin_name 判定），不得把整个规范工具从
        schema 里隐藏——否则升级前关过某一能力的用户会连带失去整个伞形
        （例如 disabled_tools=["calculate"] 不该隐藏 utility 的其余 operation）。
        """
        return set(getattr(config, "disabled_tools", None) or [])

    def list_active_tools(self, config: Any) -> list[ToolEntry]:
        """默认注入面（issue #509 单一事实源）：tag ∩ 配置禁用 ∩ enabled_predicate。

        四个消费方（loop / executor / planner / ``/agent/tools``）都走这里或
        ``build_tool_schemas``，不再各自复制「过滤 disabled_tools」的逻辑。
        """
        tags = getattr(config, "tool_tags", None)
        disabled = self._disabled_names(config)
        active: list[ToolEntry] = []
        for entry in self._tools.values():
            if entry.hidden:
                continue
            if tags and not any(tag in entry.tags for tag in tags):
                continue
            if entry.name in disabled:
                continue
            if not entry.is_enabled(config):
                continue
            active.append(entry)
        return active

    def build_tool_schemas(self, config: Any) -> list[dict[str, Any]]:
        """序列化本配置下的工具 schema（单一入口；只发规范名）。"""
        return [entry.to_openai_schema() for entry in self.list_active_tools(config)]

    def to_openai_tools(
        self, tags: list[str] | None = None, *, include_hidden: bool = False
    ) -> list[dict[str, Any]]:
        """Export tools as OpenAI function-calling definitions.

        向后兼容的**无配置**导出（默认不含 hidden）。运行时注入面请用
        ``build_tool_schemas(config)``——它还会应用 enabled_predicate 与禁用清单。
        """
        return [t.to_openai_schema() for t in self.list_tools(tags, include_hidden=include_hidden)]

    def get_manifest(self, *, include_hidden: bool = False) -> list[dict[str, Any]]:
        """Full manifest of registered tools (for admin UI / debugging).

        issue #509：默认不含 hidden 旧名（对外目录只呈现规范工具面）；排障时用
        ``include_hidden=True`` 查看保留注册的合并条目及其 ``superseded_by``。
        """
        return [t.to_manifest() for t in self._tools.values() if include_hidden or not t.hidden]

    # ── Hook Management ───────────────────────────────────────────────────

    def register_pre_hook(self, hook: PreHook, *, at_beginning: bool = False) -> None:
        """Register a pre-execution hook.

        Args:
            hook:  async (HookContext) -> None. Raise ToolAbortError to abort.
            at_beginning: If True, insert at front of pipeline (runs before built-in hooks).
        """
        if at_beginning:
            self._pre_hooks.insert(0, hook)
        else:
            self._pre_hooks.append(hook)

    def register_post_hook(self, hook: PostHook, *, at_beginning: bool = False) -> None:
        """Register a post-execution hook.

        Args:
            hook:  async (ToolResult, HookContext) -> None.
            at_beginning: If True, insert at front of pipeline (runs before built-in hooks).
        """
        if at_beginning:
            self._post_hooks.insert(0, hook)
        else:
            self._post_hooks.append(hook)

    # ── Execution ─────────────────────────────────────────────────────────

    async def execute(self, name: str, arguments: dict[str, Any], **context) -> str:
        """Execute a tool by name. Returns a string (backward compatible).

        This is the primary entry point used by the Executor. For structured
        access, use execute_detailed() instead.

        Returns:
            On success: the tool's output as a string.
            On failure: "Error: [{code}] {message}" — preserves the existing
                        executor contract (executor.py checks .startswith("Error:")).

        Args:
            name:        Tool name.
            arguments:   Tool arguments.
            **context:   Extra context (organization_id, user_id, etc.) forwarded
                         to the handler and hooks.
        """
        result = await self.execute_detailed(name, arguments, **context)
        return str(result)

    async def _check_tool_rate_limit(self, tool_name: str, user_id: Any, limit: int) -> bool:
        """Enforce per-user per-tool rate limit (QPS) via Redis; fail-open without Redis.

        Returns True when the call is allowed.
        """
        try:
            from app.core.redis import redis_client

            if not redis_client:
                return True
            key = f"agent:tool_rl:{tool_name}:{user_id or 'anon'}"
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, 60)
            return int(count) <= max(1, int(limit))
        except Exception:
            return True

    async def execute_detailed(self, name: str, arguments: dict[str, Any], **context) -> ToolResult:
        """Execute a tool by name. Returns a ToolResult with full structured data.

        Use this when you need the unified result type (success, data, error, meta).

        issue #509：入口先 canonicalize——旧名（TOOL_ALIASES）路由到规范工具并合并
        默认参数。这是防御纵深：executor 已在更早的判定点规范化，但直接调用方
        （skills / 历史技能序列 / 旧客户端）同样不得绕过规范名语义。
        """
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            # 非对象参数（list/str/...）不得被静默清空后照常执行：审批看到的是
            # 原始参数，实际执行必须是同一组参数（完整性）。
            return ToolResult.fail(
                code="serialization_error",
                message=f"工具 '{name}' 的 arguments 必须是 JSON 对象，实际为 {type(arguments).__name__}",
            )
        name, arguments = canonicalize_tool_call(name, arguments)
        entry = self._tools.get(name)
        if not entry:
            # 对外提示只列规范可见工具（hidden 旧名仍在注册表，但不作为建议项）。
            available = ", ".join(t.name for t in self.list_tools())
            return ToolResult.fail(
                code="not_found",
                message=f"未知工具 '{name}'。可用工具: {available}",
            )

        # Build hook context
        hook_ctx = HookContext(
            tool_name=name,
            arguments=arguments,
            entry=entry,
            extra=context,
        )

        # ── Langfuse trace ──
        lf = None
        span = None
        try:
            from app.agent.observability import get_langfuse

            lf = get_langfuse()
            if lf:
                span = lf.span(
                    name=f"tool:{name}",
                    input={"args": arguments, "tags": entry.tags},
                )
        except Exception:
            pass

        start = time.perf_counter()

        # ── Pre-hook pipeline ──
        try:
            for hook in self._pre_hooks:
                await hook(hook_ctx)
        except ToolAbortError as e:
            elapsed = (time.perf_counter() - start) * 1000
            result = ToolResult.fail(
                code=e.code,
                message=e.message,
                raw=e.raw,
                meta=ToolMeta(latency_ms=elapsed, tool_name=name),
            )
            await self._run_post_hooks(result, hook_ctx)
            if span:
                span.end(output=result.to_dict()["error"], level="WARNING")
            return result
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            result = ToolResult.fail(
                code="hook_error",
                message=f"前置钩子执行失败: {e}",
                raw=e,
                meta=ToolMeta(latency_ms=elapsed, tool_name=name),
            )
            await self._run_post_hooks(result, hook_ctx)
            if span:
                span.end(output=str(e)[:500], level="ERROR")
            return result

        # ── Execute handler ──
        try:
            sig = inspect.signature(entry.handler)
            handler_params = list(sig.parameters.keys())
            ctx_kwargs = {k: v for k, v in context.items() if k in handler_params}
            # Merge any context that hooks injected (e.g. auth tokens)
            ctx_kwargs.update(
                {
                    k: v
                    for k, v in hook_ctx.extra.items()
                    if k in handler_params and k not in ctx_kwargs
                }
            )

            # 安全加固：强制执行工具的 rate_limit 与 timeout 元数据
            # （此前只记录不打钩，工具调用链没有真正的限速/超时兜底）
            if entry.rate_limit and entry.rate_limit > 0:
                allowed = await self._check_tool_rate_limit(
                    name, context.get("user_id"), entry.rate_limit
                )
                if not allowed:
                    elapsed = (time.perf_counter() - start) * 1000
                    result = ToolResult.fail(
                        code="rate_limited",
                        message=f"工具 {name} 调用过于频繁，请稍后再试",
                        meta=ToolMeta(latency_ms=elapsed, tool_name=name),
                    )
                    await self._run_post_hooks(result, hook_ctx)
                    if span:
                        span.end(output=result.to_dict()["error"], level="WARNING")
                    return result

            raw_result = await asyncio.wait_for(
                entry.handler(**arguments, **ctx_kwargs),
                timeout=max(1.0, float(entry.timeout or 30.0)),
            )
            elapsed = (time.perf_counter() - start) * 1000

            # Normalise to ToolResult
            if isinstance(raw_result, ToolResult):
                result = raw_result
            else:
                # Legacy handler returned a string (or something else).
                # issue #293：这里曾把"工具 except 后返回的错误字符串"一并包成
                # success=True —— SSE/指标/run_report/findings 全绿。修复选择了
                # "迁移工具到 ToolResult.fail"（web_search / translation /
                # mcp_bridge / data_analysis 首批，`str()` 即 "Error: [code] ..."
                # 既有契约），而不是按字符串内容启发式判别：translate_text 的
                # 翻译结果、execute_python 的 stdout 等**内容透传型**成功输出
                # 可以合法地长得像错误串（如 "Error: file not found" 的译文），
                # 内容启发式的误判无法根除。判别权必须留在工具内部——能失败的
                # 工具应显式 return ToolResult.fail，本分支只兜底纯字符串成功值。
                result = ToolResult.ok(data=raw_result)

            result.meta.latency_ms = elapsed
            result.meta.tool_name = name

            if entry.output_schema and result.success:
                valid, reason = validate_against_schema(result.data, entry.output_schema)
                if not valid:
                    result = ToolResult.fail(
                        code="validation_error",
                        message=f"工具输出不符合声明 schema: {reason}",
                        raw=result.data,
                        meta=result.meta,
                    )

        except ToolAbortError as e:
            elapsed = (time.perf_counter() - start) * 1000
            result = ToolResult.fail(
                code=e.code,
                message=e.message,
                raw=e.raw,
                meta=ToolMeta(latency_ms=elapsed, tool_name=name),
            )
        except TimeoutError:
            # Classify explicitly so the retry loop can select the "timeout" policy
            # instead of falling through to the unknown/fatal default.
            elapsed = (time.perf_counter() - start) * 1000
            logger.warning("Tool '%s' timed out after %.1fs", name, elapsed / 1000)
            result = ToolResult.fail(
                code="timeout",
                message=f"工具 {name} 执行超时（{entry.timeout or 30.0}s）",
                meta=ToolMeta(latency_ms=elapsed, tool_name=name),
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            logger.error("Tool '%s' failed: %s", name, e, exc_info=True)
            result = ToolResult.fail(
                code="unknown",
                message=f"{type(e).__name__}: {e}",
                raw=e,
                meta=ToolMeta(latency_ms=elapsed, tool_name=name),
            )

        # ── Post-hook pipeline ──
        await self._run_post_hooks(result, hook_ctx)

        if span:
            span.end(
                output=result.to_dict(),
                metadata={"latency_ms": round(elapsed, 1), "success": result.success},
            )

        return result

    async def _run_post_hooks(self, result: ToolResult, ctx: HookContext) -> None:
        """Run all registered post-hooks, catching individual failures."""
        for hook in self._post_hooks:
            try:
                await hook(result, ctx)
            except Exception as e:
                logger.warning("Post-hook %s failed: %s", getattr(hook, "__name__", "?"), e)

    # ── Built-in Hooks ────────────────────────────────────────────────────

    async def _builtin_auth_hook(self, ctx: HookContext) -> None:
        """Pre-hook enforcing `requires_auth`（此前只 logger.debug，从不阻断）。

        **语义边界** —— 这个字段此前把两件事混在一起，所以一直不敢拦：

          · `requires_auth=True`  → 需要**已认证的调用者**，即调用方必须带上组织
            身份。当前标了它的三个工具（list_documents / list_conversations /
            get_conversation_history）都是这个含义 —— 它们不需要外部凭据，
            需要的是「你是谁、你属于哪个组织」。
          · `auth_handler="xxx"`  → 需要**外部凭据**（如飞书的 tenant token）。
            凭据的获取与刷新由工具自身负责，注册表只检查它是否声明了。

        本 hook 只强制前者。**fail-closed**：声明了 requires_auth 却没有可用的
        调用者身份时拒绝执行 —— 让工具带着空组织上下文跑进去，只会在更深处抛一个
        含糊的异常，或者更糟：查到不属于调用者的数据。
        """
        if not ctx.entry.requires_auth:
            return
        if ctx.extra.get("organization_id") is None:
            raise ToolAbortError(
                code="auth_required",
                message=(
                    f"工具 '{ctx.tool_name}' 声明了 requires_auth，但调用方未提供"
                    f"组织上下文（organization_id）—— 拒绝执行"
                ),
            )

    async def _builtin_error_mapper_hook(self, result: ToolResult, ctx: HookContext) -> None:
        """Post-hook: map raw exception types to 27 standardised ToolErrorType codes.

        Classifies into retryable (timeout/rate/unreachable/interrupted),
        non-retryable (not_found/perm/validation/empty/payload...),
        and unknown fallback.

        Tools with custom error handling should return their own ToolResult directly.
        """
        if result.success or not result.error:
            return

        raw = result.error.raw
        if raw is None:
            return

        raw_str = str(raw).lower()

        # ── Retryable: timeout-class ──
        if any(
            kw in raw_str
            for kw in (
                "timeout",
                "timed out",
                "deadline",
                "operation timed out",
                "no progress",
                "idle",
                "stalled",
                "stream hung",
            )
        ):
            # Distinguish ordinary timeout from idle (no progress) timeout
            if any(kw in raw_str for kw in ("no progress", "idle", "stalled", "stream hung")):
                result.error.code = "idle_timeout"
            else:
                result.error.code = "timeout"
            if "超时" not in result.error.message:
                result.error.message = f"API 调用超时: {result.error.message}"
            return

        # ── Retryable: rate limit ──
        if any(kw in raw_str for kw in ("rate limit", "rate_limit", "too many requests", "429")):
            result.error.code = "rate_limited"
            result.error.message = f"请求频率超限: {result.error.message}"
            return

        # ── Retryable: connection / network ──
        if any(
            kw in raw_str
            for kw in (
                "connectionrefused",
                "econnrefused",
                "connection refused",
                "cannot assign requested address",
                "network is unreachable",
                "getaddrinfo failed",
                "no route to host",
            )
        ):
            result.error.code = "unreachable"
            result.error.message = f"无法连接目标服务: {result.error.message}"
            return

        if any(
            kw in raw_str
            for kw in (
                "connection reset",
                "connectionreset",
                "econnreset",
                "broken pipe",
                "connection aborted",
                "incomplete read",
            )
        ):
            result.error.code = "interrupted"
            result.error.message = f"连接中断: {result.error.message}"
            return

        # Generic connection — fallback to classification by message
        if any(kw in raw_str for kw in ("connection", "network", "socket", "transport error")):
            result.error.code = "connection_error"
            result.error.message = f"网络连接失败: {result.error.message}"
            return

        # ── Non-retryable: auth ──
        if any(
            kw in raw_str
            for kw in (
                "401",
                "unauthorized",
                "unauthenticated",
                "invalid api key",
                "incorrect api key",
                "token expired",
                "token revoked",
            )
        ):
            if any(kw in raw_str for kw in ("refresh", "renew", "grant type", "token expired")):
                result.error.code = "auth_expired_refreshable"
            else:
                result.error.code = "auth_expired"
            return

        # ── Non-retryable: permission ──
        # 注：insufficient_quota 已移至 budget 分支（余额耗尽属预算语义，issue #203 ②）
        if any(
            kw in raw_str
            for kw in ("403", "forbidden", "permission", "access denied", "not allowed")
        ):
            result.error.code = "permission_denied"
            return

        # ── Non-retryable: configuration ── (must check before "not found")
        if any(
            kw in raw_str
            for kw in (
                "invalid config",
                "misconfigur",
                "bad model",
                "model not found",
                "unknown model",
                "no such model",
                "not supported",
                "disabled",
            )
        ):
            result.error.code = "invalid_configuration"
            return

        # ── Non-retryable: not found ──
        if any(
            kw in raw_str
            for kw in (
                "404",
                "not found",
                "notfound",
                "no such file",
                "document not found",
                "resource not found",
            )
        ):
            result.error.code = "not_found"
            return

        # ── Non-retryable: validation / input ──
        if any(
            kw in raw_str
            for kw in (
                "400",
                "validation",
                "invalid parameter",
                "bad request",
                "missing required",
                "out of range",
            )
        ):
            result.error.code = "validation_error"
            return

        # ── Non-retryable: payload / context length ──
        if any(
            kw in raw_str
            for kw in (
                "413",
                "payload too large",
                "request entity too large",
                "context length",
                "maximum context length",
                "context window exceeded",
                "max tokens",
                "token limit",
                "reduce the length",
            )
        ):
            if any(
                kw in raw_str
                for kw in (
                    "context length",
                    "context window",
                    "maximum context",
                    "reduce the length",
                )
            ):
                result.error.code = "context_length_exceeded"
            else:
                result.error.code = "payload_too_large"
            return

        # ── Non-retryable: empty / malformed ──
        if any(
            kw in raw_str
            for kw in (
                "jsondecode",
                "json parse",
                "malformed",
                "unexpected token",
                "invalid json",
                "cannot parse",
                "deserialization",
            )
        ):
            result.error.code = "serialization_error"
            return

        if any(
            kw in raw_str
            for kw in (
                "empty response",
                "null response",
                "no content",
                "returned nothing",
                "no choice",
                "no completion",
            )
        ):
            result.error.code = "empty_response"
            return

        # ── Non-retryable: truncation ──
        if any(
            kw in raw_str
            for kw in (
                "truncat",
                "finish_reason",
                "stop reason",
                "max_tokens",
                "output limit",
                "length",
            )
        ):
            if any(kw in raw_str for kw in ("truncat", "max_tokens", "output limit")):
                result.error.code = "max_tokens_truncation"
                return

        # ── Non-retryable: internal / permanent ──
        if any(
            kw in raw_str
            for kw in (
                "500",
                "internal server error",
                "internal error",
                "service unavailable",
                "503",
                "server error",
            )
        ):
            result.error.code = "api_error"
            return

        # ── Non-retryable: budget ──
        # insufficient_quota（OpenAI 余额耗尽）从 permission 分支移来，issue #203 ②
        if any(
            kw in raw_str
            for kw in (
                "billing",
                "budget",
                "quota exceeded",
                "insufficient_quota",
                "insufficient funds",
                "account balance",
                "payment required",
                "402",
            )
        ):
            result.error.code = "budget_exceeded"
            return

        # ── Non-retryable: repeated failure (doom loop) ──
        if any(
            kw in raw_str
            for kw in (
                "repetitive",
                "doom loop",
                "stuck in loop",
                "identical response",
                "no variation",
            )
        ):
            result.error.code = "doom_loop_detected"
            return

        # ── Non-retryable: configuration ──
        if any(
            kw in raw_str
            for kw in (
                "invalid config",
                "misconfigur",
                "bad model",
                "model not found",
                "unknown model",
                "no such model",
                "not supported",
                "disabled",
            )
        ):
            result.error.code = "invalid_configuration"
            return

        # ── Image / multimodal ──
        if any(
            kw in raw_str
            for kw in (
                "image",
                "multipart",
                "attachment",
                "media",
                "unsupported format",
                "resolution",
            )
        ):
            result.error.code = "image_processing_error"
            return


# ──────────────────────────────────────────────────────────────────────────────
# 6.  Global Singleton & Decorator
# ──────────────────────────────────────────────────────────────────────────────


# Global singleton
tool_registry = ToolRegistry()


def register_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    tags: list[str] | None = None,
    requires_auth: bool = False,
    # v2 optional fields — all backward compatible
    output_schema: dict[str, Any] | None = None,
    auth_handler: str | None = None,
    rate_limit: int | None = None,
    timeout: float = 30.0,
    # v2.1 semantic fields
    category: str = "",
    semantic: dict[str, Any] | None = None,
    # v2.2 risk declaration（HITL 审批门判定输入："low" | "medium" | "high"）
    risk_level: str = "",
    # v2.3 surface gating（issue #509）
    hidden: bool = False,
    superseded_by: str = "",
    enabled_predicate: Callable[[Any], bool] | None = None,
):
    """Decorator to register a function as a tool.

    Usage (v1 — unchanged):
        @register_tool(name="search", description="...", parameters={...})
        async def search(query: str, **ctx) -> str: ...

    Usage (v2 — new optional fields):
        @register_tool(
            name="feishu_table",
            description="...",
            parameters={...},
            output_schema={...},
            auth_handler="feishu_tenant_token",
            rate_limit=10,
            category="feishu",
            semantic={"type": "read_only", "idempotent": True, ...},
        )
        async def feishu_table(app_token: str, **ctx) -> ToolResult | str: ...
    """

    def decorator(func: Callable[..., Coroutine[Any, Any, str | ToolResult]]):
        tool_registry.register(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
            tags=tags,
            requires_auth=requires_auth,
            # v2
            output_schema=output_schema,
            auth_handler=auth_handler,
            rate_limit=rate_limit,
            timeout=timeout,
            # v2.1
            category=category,
            semantic=semantic,
            # v2.2
            risk_level=risk_level,
            # v2.3
            hidden=hidden,
            superseded_by=superseded_by,
            enabled_predicate=enabled_predicate,
        )
        return func

    return decorator
