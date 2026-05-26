"""CircuitBreaker 纯逻辑测试。"""
import time

import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreakerState:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, recovery_timeout=60)

        @cb
        def failing():
            raise RuntimeError("boom")

        for _ in range(3):
            failing()

        assert cb.state == CircuitState.OPEN

    def test_fallback_returns_none_for_untyped(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60)

        @cb
        def failing():
            raise RuntimeError("boom")

        result = failing()
        assert result is None  # fallback for no return annotation

    def test_fallback_returns_list(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60)

        @cb
        def failing() -> list:
            raise RuntimeError("boom")

        result = failing()
        assert result == []

    def test_fallback_returns_dict(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60)

        @cb
        def failing() -> dict:
            raise RuntimeError("boom")

        result = failing()
        assert isinstance(result, dict)
        assert result.get("fallback") is True

    def test_fallback_returns_str(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60)

        @cb
        def failing() -> str:
            raise RuntimeError("boom")

        result = failing()
        assert isinstance(result, str)

    def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=0)

        @cb
        def failing():
            raise RuntimeError("boom")

        failing()  # triggers OPEN
        assert cb.state == CircuitState.OPEN

        # recovery_timeout=0 means immediate recovery attempt
        time.sleep(0.01)

        @cb
        def succeeding():
            return "ok"

        result = succeeding()
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    def test_success_resets_failure_count_when_half_open(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=0)

        call_count = 0

        @cb
        def sometimes_fails():
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("boom")
            return "ok"

        sometimes_fails()  # failure 1
        sometimes_fails()  # failure 2 → OPEN
        assert cb.state == CircuitState.OPEN

        time.sleep(0.01)  # wait for recovery timeout
        result = sometimes_fails()  # HALF_OPEN → success → CLOSED
        assert result == "ok"
        assert cb.failure_count == 0
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerAsync:
    @pytest.mark.asyncio
    async def test_async_opens_after_threshold(self):
        cb = CircuitBreaker(name="test-async", failure_threshold=2, recovery_timeout=60)

        @cb
        async def failing():
            raise RuntimeError("async boom")

        await failing()
        await failing()
        assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_async_success_resets(self):
        cb = CircuitBreaker(name="test-async", failure_threshold=3, recovery_timeout=60)

        @cb
        async def ok():
            return 42

        result = await ok()
        assert result == 42
        assert cb.state == CircuitState.CLOSED
