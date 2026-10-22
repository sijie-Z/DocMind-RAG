"""CircuitBreaker 纯逻辑测试（issue #467：装饰器路径已删除，仅状态机 API）。

背景（issue #467 第 1 条）：``CircuitBreaker.__call__`` 装饰器与
``_get_fallback_value`` 全仓零使用——executor 只接状态机 API
（``allow_request / record_success / record_failure``）。装饰器在失败/熔断时
**吞掉异常并返回按返回注解推导的降级值**（list→[]、dict→{...}、str→提示语），
调用方无法区分「成功返回了空结果」与「服务已熔断/失败」，会破坏执行器的
错误分类与重试。为避免读者误以为「降级兜底值已启用」，整条装饰器路径已删除。

本文件锁定：
1. 状态机 API 的既有语义（CLOSED → OPEN → HALF_OPEN → CLOSED）；
2. 装饰器入口**不得**被重新引入（回归钉）——若未来确需装饰器式调用，
   必须先把 OPEN 做成可分类异常（CircuitOpenError）或显式 degraded 标记，
   并同步更新本契约与 executor 的错误分类。
"""

import time

import pytest

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState


class TestStateMachine:
    def test_initial_state_is_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3)
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_failure_below_threshold_stays_closed(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 2
        assert cb.allow_request() is True

    def test_opens_after_threshold(self):
        cb = CircuitBreaker(name="test", failure_threshold=3, recovery_timeout=60)
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False, "OPEN 且恢复窗口未过 → 拒绝请求"

    def test_half_open_after_recovery_timeout(self):
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        # 拨回 61s：窗口必已过（不依赖 sleep/同一 time.time() tick，避免 Windows
        # 15.6ms 时钟粒度造成的偶发假阳性）。
        cb.last_failure_time = time.time() - 61
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

    def test_success_resets_when_half_open(self):
        cb = CircuitBreaker(name="test", failure_threshold=2, recovery_timeout=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.last_failure_time = time.time() - 61
        assert cb.allow_request() is True
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb.failure_count == 0

    def test_failure_in_half_open_reopens(self):
        """半开探测失败 → 立即回到 OPEN（计数继续累计，不误闭合）。"""
        cb = CircuitBreaker(name="test", failure_threshold=1, recovery_timeout=60)
        cb.record_failure()
        assert cb.state == CircuitState.OPEN

        cb.last_failure_time = time.time() - 61
        assert cb.allow_request() is True
        assert cb.state == CircuitState.HALF_OPEN

        cb.record_failure()
        assert cb.state == CircuitState.OPEN
        assert cb.allow_request() is False, "重新计时后窗口未过 → 拒绝"

    def test_record_failure_carries_classifiable_error_type(self):
        assert CircuitOpenError("x").error_type == "circuit_open"


class TestDecoratorPathRemoved:
    """回归钉（issue #467）：吞异常返回降级值的装饰器路径不得重新引入。

    变异点：重新添加 ``__call__`` 或 ``_get_fallback_value`` → 本组用例红。
    该路径已确认全仓零使用；executor 的熔断接线只依赖状态机 API
    （见 test_circuit_breaker_wiring.py 的判别性用例）。
    """

    def test_decorator_entrypoints_do_not_exist(self):
        assert "__call__" not in CircuitBreaker.__dict__, (
            "装饰器路径已删除（issue #467）——重新引入前请先让 OPEN 可分类，"
            "并同步更新执行器的错误分类契约"
        )
        assert "_get_fallback_value" not in CircuitBreaker.__dict__

    def test_instance_is_not_callable(self):
        cb = CircuitBreaker(name="test", failure_threshold=1)
        with pytest.raises(TypeError):
            cb(lambda: 1)  # type: ignore[operator]

    def test_state_machine_api_retained(self):
        for name in ("allow_request", "record_success", "record_failure", "_handle_failure"):
            assert hasattr(CircuitBreaker, name), f"状态机 API 不得随装饰器一起删除：{name}"
