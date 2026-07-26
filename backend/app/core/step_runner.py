"""StepRunner — reliable step execution with timeout, retry, and fallback.

This is the core abstraction for the Reliability Layer. Every step in the
agent execution (LLM calls, tool executions, retrieval) should go through
StepRunner to ensure bounded execution time and predictable failure behavior.
"""
import asyncio
import builtins
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ── Error types ──────────────────────────────────────────────

class StepError(Exception):
    """Base error for step execution failures."""
    def __init__(self, message: str, error_type: str = "step_error", recoverable: bool = False):
        super().__init__(message)
        self.error_type = error_type
        self.recoverable = recoverable


class StepTimeoutError(StepError):
    def __init__(self, message: str = "Step timed out"):
        super().__init__(message, error_type="timeout", recoverable=True)


class ToolExecutionError(StepError):
    def __init__(self, message: str, tool_name: str = ""):
        super().__init__(message, error_type=f"tool_error:{tool_name}", recoverable=True)


class LLMError(StepError):
    def __init__(self, message: str):
        super().__init__(message, error_type="llm_error", recoverable=True)


class ResourceExhaustedError(StepError):
    def __init__(self, message: str):
        super().__init__(message, error_type="resource_exhausted", recoverable=False)


# ── Results ──────────────────────────────────────────────────

@dataclass
class StepResult:
    """Result of a single step execution after timeout/retry/fallback."""
    success: bool
    output: Any = None
    error: str | None = None
    error_type: str = ""
    recoverable: bool = False
    latency: float = 0.0
    attempts: int = 0
    step_id: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "error": self.error,
            "error_type": self.error_type,
            "recoverable": self.recoverable,
            "latency_ms": round(self.latency * 1000),
            "attempts": self.attempts,
        }


# ── StepRunner ───────────────────────────────────────────────

class StepRunner:
    """Wraps an async callable with timeout, retry, and fallback.

    Usage:
        runner = StepRunner(my_async_fn, timeout=10.0, max_retries=2)
        result = await runner.run(arg1, arg2)

        # With fallback:
        runner = StepRunner(my_fn, timeout=10.0, fallback_fn=fallback)
        result = await runner.run(arg1)
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 2,
        fallback_fn: Callable[..., Awaitable[Any]] | None = None,
        step_id: str = "",
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.fallback_fn = fallback_fn
        self.step_id = step_id or uuid.uuid4().hex[:8]

    async def run(
        self,
        fn: Callable[..., Awaitable[Any]],
        *args,
        **kwargs,
    ) -> StepResult:
        """Execute fn with timeout and retry. Returns StepResult (never raises)."""
        start = time.perf_counter()
        last_error: StepError | Exception | None = None
        attempts = 0

        for attempt in range(self.max_retries + 1):
            attempts += 1
            try:
                if attempt > 0:
                    backoff = min(2.0, 0.5 * (2 ** (attempt - 1)))
                    await asyncio.sleep(backoff)

                output = await asyncio.wait_for(
                    fn(*args, **kwargs),
                    timeout=self.timeout,
                )
                latency = time.perf_counter() - start
                return StepResult(
                    success=True,
                    output=output,
                    latency=latency,
                    attempts=attempts,
                    step_id=self.step_id,
                )

            except builtins.TimeoutError:
                last_error = StepTimeoutError(
                    f"Step timed out after {self.timeout}s (attempt {attempt + 1}/{self.max_retries + 1})"
                )
                logger.warning("[%s] timeout on attempt %d/%d", self.step_id, attempt + 1, self.max_retries + 1)
                continue

            except StepError as e:
                last_error = e
                if not e.recoverable:
                    break  # non-recoverable → no retry
                logger.warning("[%s] %s (attempt %d/%d)", self.step_id, e.error_type, attempt + 1, self.max_retries + 1)
                continue

            except Exception as e:
                last_error = StepError(str(e)[:200], error_type="unexpected", recoverable=True)
                logger.warning("[%s] unexpected error: %s (attempt %d/%d)", self.step_id, str(e)[:80], attempt + 1, self.max_retries + 1)
                continue

        # ── Retries exhausted — try fallback ──
        if self.fallback_fn and last_error:
            try:
                logger.info("[%s] trying fallback after %d attempts", self.step_id, attempts)
                output = await asyncio.wait_for(
                    self.fallback_fn(),
                    timeout=self.timeout,
                )
                latency = time.perf_counter() - start
                return StepResult(
                    success=False,
                    output=output,
                    error=str(last_error)[:200],
                    error_type=getattr(last_error, "error_type", "unknown"),
                    recoverable=getattr(last_error, "recoverable", False),
                    latency=latency,
                    attempts=attempts,
                    step_id=self.step_id,
                )
            except Exception:
                pass  # fallback also failed → return original error

        latency = time.perf_counter() - start
        return StepResult(
            success=False,
            error=str(last_error)[:300] if last_error else "Unknown error",
            error_type=getattr(last_error, "error_type", "unknown") if last_error else "unknown",
            recoverable=getattr(last_error, "recoverable", False) if last_error else False,
            latency=latency,
            attempts=attempts,
            step_id=self.step_id,
        )


# ── Convenience wrapper ──────────────────────────────────────

async def safe_run(
    fn: Callable[..., Awaitable[Any]],
    *args,
    timeout: float = 30.0,
    max_retries: int = 2,
    fallback_fn: Callable[..., Awaitable[Any]] | None = None,
    step_id: str = "",
    **kwargs,
) -> StepResult:
    """One-shot: create a StepRunner and run immediately."""
    runner = StepRunner(
        timeout=timeout,
        max_retries=max_retries,
        fallback_fn=fallback_fn,
        step_id=step_id,
    )
    return await runner.run(fn, *args, **kwargs)
