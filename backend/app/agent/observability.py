"""Observability — Langfuse tracing for the PER agent loop.

Deprecated candidate（issue #510 第三步待仓库所有者逐项批准删除）：在批准前
本模块、依赖与全部埋点**保留且行为不变**。调用级替代记录已由
``app.agent.llm_call_log``（ExecutionContext JSONL）提供；需要关闭本模块时
置 ``LANGFUSE_ENABLED=false``（不删除代码、不改变未配置时的既有行为）。

Provides helpers to trace Planner, Executor, Reflector, Tool calls,
and Memory retrieval into Langfuse.

Usage:
    from app.agent.observability import get_langfuse

    lf = get_langfuse()
    if lf:
        span = lf.span(name="my_span", input="...")
        span.end(output="...")
"""

import logging
from contextlib import asynccontextmanager
from typing import Any

from langfuse import Langfuse

logger = logging.getLogger(__name__)

_langfuse: Langfuse | None = None


def get_langfuse() -> Langfuse | None:
    """Get the Langfuse singleton, lazily initialised from settings."""
    global _langfuse
    if _langfuse is None:
        try:
            from app.core.config import settings

            if not getattr(settings, "LANGFUSE_ENABLED", True):
                logger.info("Langfuse disabled by configuration (LANGFUSE_ENABLED=false)")
                _langfuse = None
                return _langfuse
            if settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY:
                _langfuse = Langfuse(
                    public_key=settings.LANGFUSE_PUBLIC_KEY,
                    secret_key=settings.LANGFUSE_SECRET_KEY,
                    host=settings.LANGFUSE_HOST,
                )
                logger.info("Langfuse observability enabled")
            else:
                logger.info("Langfuse not configured — observability disabled")
                _langfuse = None
        except Exception as e:
            logger.warning("Langfuse init failed: %s — observability disabled", e)
            _langfuse = None
    return _langfuse


@asynccontextmanager
async def trace_span(
    name: str,
    *,
    input: Any = None,
    metadata: dict | None = None,
    tool_name: str | None = None,
    parent_observation_id: str | None = None,
):
    """Async context manager wrapping a Langfuse span.

    Auto-ends on exit, tagging with ERROR level on exception.

    Usage:
        async with trace_span("my_func", input=query) as span:
            result = await do_work()
            # span is a dict-like object you can set .output on, or just
            # let the context manager capture the return from the block.
            span["output"] = result
            yield result
    """
    lf = get_langfuse()
    span = None
    if lf:
        span = lf.span(
            name=name,
            input=input,
            metadata=metadata or {},
        )
    try:
        # Provide a mutable dict so the caller can set .output inside the block
        ctx: dict[str, Any] = {"output": None, "span": span}
        yield ctx
        if span:
            span.end(output=ctx.get("output"))
    except Exception as exc:
        if span:
            span.end(output=str(exc), level="ERROR")
        raise


@asynccontextmanager
async def trace_generation(
    name: str,
    *,
    model: str | None = None,
    messages: list[dict] | None = None,
    input: Any = None,
):
    """Context manager to trace an LLM generation call.

    Usage:
        async with trace_generation("planning", model="...", messages=msgs) as gen:
            response = await client.chat.completions.create(...)
            gen["response"] = response
    """
    lf = get_langfuse()
    span = None
    if lf:
        span = lf.generation(
            name=name,
            model=model or "unknown",
            messages=messages,
            input=input,
        )
    try:
        ctx: dict[str, Any] = {"response": None, "span": span}
        yield ctx
        if span and ctx.get("response"):
            resp = ctx["response"]
            span.end(
                output=resp.choices[0].message.content if resp.choices else "",
                usage=resp.usage,
            )
    except Exception as exc:
        if span:
            span.end(output=str(exc), level="ERROR")
        raise


def trace_tool_call(name: str, input: dict):
    """Context manager for a tool-call span (sync version for hot-path use).

    Prefer trace_span for most cases; this exists for tool_registry.execute
    which is called from many places.
    """
    return trace_span(name, input=input, metadata={"tool_name": name})
