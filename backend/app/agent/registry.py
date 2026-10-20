"""Tool Registry v2 — self-registering tool system with hooks and unified results.

Architecture:
    ToolEntry                     — metadata + handler for one tool
    ToolResult / ToolError        — unified return structure
    pre_hooks / post_hooks        — pluggable pipeline (auth, rate_limit, error mapping)

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

    code: one of 26 ToolErrorType literals (§13.3 agent-reliability-design.md)
    """

    # ── Union of all known error types ──────────────────────────────────
    ToolErrorType = Literal[
        "timeout",
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
    message: str       # human-readable (Chinese preferred)
    raw: Any = None    # original exception / API error body for debugging

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "raw": str(self.raw)[:500] if self.raw else None}


@dataclass
class ToolMeta:
    """Execution metadata attached to every tool call."""
    latency_ms: float = 0.0
    tool_name: str = ""
    retry_count: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"latency_ms": round(self.latency_ms, 1), "tool_name": self.tool_name, "retry_count": self.retry_count}


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
    def fail(cls, code: str, message: str, raw: Any = None, meta: ToolMeta | None = None) -> "ToolResult":
        """Factory: failed result with structured error."""
        return cls(success=False, error=ToolError(code=code, message=message, raw=raw), meta=meta or ToolMeta())

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
# 2.  Tool Entry
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
    """
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, str | ToolResult]]
    tags: list[str] = field(default_factory=list)
    requires_auth: bool = False

    # ── v2 fields ──
    output_schema: dict[str, Any] | None = None
    auth_handler: str | None = None          # "feishu_tenant_token" | "dingtalk_token" | "api_key" | None
    rate_limit: int | None = None            # max QPS
    timeout: float = 30.0

    # ── v2.1 semantic fields ──
    category: str = ""                       # business domain for tool grouping
    # ── v2.2 risk declaration（HITL 审批门的判定输入，见 app/agent/approval.py）──
    # 工具作者显式声明的风险等级："low" | "medium" | "high"；"" = 未声明
    # （由 approval.TOOL_RISK_LEVELS 集中判定表 + 语义兜底补齐）。
    risk_level: str = ""
    semantic: dict[str, Any] = field(default_factory=lambda: {
        "type": "read_only",                 # read_only | mutating | long_running
        "idempotent": True,                  # safe to retry without side effects
        "retry_safe": True,                  # can auto-retry on transient errors
        "consistency": "eventual",           # strong | eventual
        "parallel_safe": True,               # safe to call concurrently
    })

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
        }


# ──────────────────────────────────────────────────────────────────────────────
# 3.  Hook Types
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
# 4.  The Registry
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
    ) -> ToolEntry:
        """Register a tool. All v2/v2.1 params are optional — zero migration needed."""
        resolved_semantic = semantic if semantic is not None else {
            "type": "read_only",
            "idempotent": True,
            "retry_safe": True,
            "consistency": "eventual",
            "parallel_safe": True,
        }
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
        )
        self._tools[name] = entry
        logger.info("Registered tool: %s%s",
                     name,
                     f" [auth={auth_handler}, qps={rate_limit}]" if auth_handler or rate_limit else "")
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

    def list_tools(self, tags: list[str] | None = None) -> list[ToolEntry]:
        if not tags:
            return list(self._tools.values())
        return [t for t in self._tools.values() if any(tag in t.tags for tag in tags)]

    def to_openai_tools(self, tags: list[str] | None = None) -> list[dict[str, Any]]:
        """Export tools as OpenAI function-calling definitions."""
        return [t.to_openai_schema() for t in self.list_tools(tags)]

    def get_manifest(self) -> list[dict[str, Any]]:
        """Full manifest of all registered tools (for admin UI / debugging)."""
        return [t.to_manifest() for t in self._tools.values()]

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
        """
        entry = self._tools.get(name)
        if not entry:
            available = ", ".join(self._tools.keys())
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
            ctx_kwargs.update({k: v for k, v in hook_ctx.extra.items() if k in handler_params and k not in ctx_kwargs})

            # 安全加固：强制执行工具的 rate_limit 与 timeout 元数据
            # （此前只记录不打钩，工具调用链没有真正的限速/超时兜底）
            if entry.rate_limit and entry.rate_limit > 0:
                allowed = await self._check_tool_rate_limit(name, context.get("user_id"), entry.rate_limit)
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
                valid, reason = validate_against_schema(
                    result.data, entry.output_schema
                )
                if not valid:
                    result = ToolResult.fail(
                        code="validation_error",
                        message=f"工具输出不符合声明 schema: {reason}",
                        raw=result.data,
                        meta=result.meta,
                    )

        except ToolAbortError as e:
            elapsed = (time.perf_counter() - start) * 1000
            result = ToolResult.fail(code=e.code, message=e.message, raw=e.raw,
                                      meta=ToolMeta(latency_ms=elapsed, tool_name=name))
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
        """Post-hook: map raw exception types to 26 standardised ToolErrorType codes.

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
        if any(kw in raw_str for kw in ("timeout", "timed out", "deadline", "operation timed out",
                                          "no progress", "idle", "stalled", "stream hung")):
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
        if any(kw in raw_str for kw in ("connectionrefused", "econnrefused", "connection refused",
                                          "cannot assign requested address", "network is unreachable",
                                          "getaddrinfo failed", "no route to host")):
            result.error.code = "unreachable"
            result.error.message = f"无法连接目标服务: {result.error.message}"
            return

        if any(kw in raw_str for kw in ("connection reset", "connectionreset", "econnreset",
                                          "broken pipe", "connection aborted", "incomplete read")):
            result.error.code = "interrupted"
            result.error.message = f"连接中断: {result.error.message}"
            return

        # Generic connection — fallback to classification by message
        if any(kw in raw_str for kw in ("connection", "network", "socket", "transport error")):
            result.error.code = "connection_error"
            result.error.message = f"网络连接失败: {result.error.message}"
            return

        # ── Non-retryable: auth ──
        if any(kw in raw_str for kw in ("401", "unauthorized", "unauthenticated", "invalid api key",
                                          "incorrect api key", "token expired", "token revoked")):
            if any(kw in raw_str for kw in ("refresh", "renew", "grant type", "token expired")):
                result.error.code = "auth_expired_refreshable"
            else:
                result.error.code = "auth_expired"
            return

        # ── Non-retryable: permission ──
        # 注：insufficient_quota 已移至 budget 分支（余额耗尽属预算语义，issue #203 ②）
        if any(kw in raw_str for kw in ("403", "forbidden", "permission", "access denied",
                                          "not allowed")):
            result.error.code = "permission_denied"
            return

        # ── Non-retryable: configuration ── (must check before "not found")
        if any(kw in raw_str for kw in ("invalid config", "misconfigur", "bad model",
                                          "model not found", "unknown model", "no such model",
                                          "not supported", "disabled")):
            result.error.code = "invalid_configuration"
            return

        # ── Non-retryable: not found ──
        if any(kw in raw_str for kw in ("404", "not found", "notfound", "no such file",
                                          "document not found", "resource not found")):
            result.error.code = "not_found"
            return

        # ── Non-retryable: validation / input ──
        if any(kw in raw_str for kw in ("400", "validation", "invalid parameter", "bad request",
                                          "missing required", "out of range")):
            result.error.code = "validation_error"
            return

        # ── Non-retryable: payload / context length ──
        if any(kw in raw_str for kw in ("413", "payload too large", "request entity too large",
                                          "context length", "maximum context length",
                                          "context window exceeded", "max tokens", "token limit",
                                          "reduce the length")):
            if any(kw in raw_str for kw in ("context length", "context window", "maximum context",
                                              "reduce the length")):
                result.error.code = "context_length_exceeded"
            else:
                result.error.code = "payload_too_large"
            return

        # ── Non-retryable: empty / malformed ──
        if any(kw in raw_str for kw in ("jsondecode", "json parse", "malformed", "unexpected token",
                                          "invalid json", "cannot parse", "deserialization")):
            result.error.code = "serialization_error"
            return

        if any(kw in raw_str for kw in ("empty response", "null response", "no content",
                                          "returned nothing", "no choice", "no completion")):
            result.error.code = "empty_response"
            return

        # ── Non-retryable: truncation ──
        if any(kw in raw_str for kw in ("truncat", "finish_reason", "stop reason", "max_tokens",
                                          "output limit", "length")):
            if any(kw in raw_str for kw in ("truncat", "max_tokens", "output limit")):
                result.error.code = "max_tokens_truncation"
                return

        # ── Non-retryable: internal / permanent ──
        if any(kw in raw_str for kw in ("500", "internal server error", "internal error",
                                          "service unavailable", "503", "server error")):
            result.error.code = "api_error"
            return

        # ── Non-retryable: budget ──
        # insufficient_quota（OpenAI 余额耗尽）从 permission 分支移来，issue #203 ②
        if any(kw in raw_str for kw in ("billing", "budget", "quota exceeded", "insufficient_quota",
                                          "insufficient funds",
                                          "account balance", "payment required", "402")):
            result.error.code = "budget_exceeded"
            return

        # ── Non-retryable: repeated failure (doom loop) ──
        if any(kw in raw_str for kw in ("repetitive", "doom loop", "stuck in loop",
                                          "identical response", "no variation")):
            result.error.code = "doom_loop_detected"
            return

        # ── Non-retryable: configuration ──
        if any(kw in raw_str for kw in ("invalid config", "misconfigur", "bad model",
                                          "model not found", "unknown model", "no such model",
                                          "not supported", "disabled")):
            result.error.code = "invalid_configuration"
            return

        # ── Image / multimodal ──
        if any(kw in raw_str for kw in ("image", "multipart", "attachment", "media",
                                          "unsupported format", "resolution")):
            result.error.code = "image_processing_error"
            return


# ──────────────────────────────────────────────────────────────────────────────
# 5.  Global Singleton & Decorator
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
        )
        return func
    return decorator
