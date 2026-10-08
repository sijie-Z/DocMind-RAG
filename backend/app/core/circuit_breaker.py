"""
企业级熔断器 - 防止系统雪崩，提供优雅降级方案
小白解释：就像家里的保险丝，如果某个外部服务（比如 AI 接口）一直报错，
保险丝就会断开，直接返回预设好的“友好提示”，而不是让整个系统卡死在那。

当前接线状态（issue #106 §4c 核实；issue #242 第 7 条更新）：**状态机 API 已接入
LLM 步骤调用路径**（`executor._execute_step_once` 用 `allow_request/record_success/
record_failure` + `CircuitOpenError.error_type="circuit_open"` 进 retry_policy 的
fatal 分支），即"跨请求熔断 + 半开恢复"这一全仓唯一缺失的容错维度已补齐；
**装饰器路径仍未接线**——它在失败/熔断时吞掉异常并返回按返回注解推导的降级值
（list→[]、dict→{...}、str→提示语、无注解→None），调用方无法区分「成功返回了
空结果」与「服务已熔断/失败」，会破坏执行器的错误分类与重试。若要给别的调用层
用装饰器：先让熔断可区分（OPEN 时抛出可分类异常或显式 degraded 标记），并同步
修改 tests/unit/test_circuit_breaker.py 中锁定的「降级返回值」契约。
"""

import asyncio
import functools
import logging
import time
from collections.abc import Callable  # 补全了 List 导入
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "CLOSED"      # 正常运行（保险丝闭合）
    OPEN = "OPEN"          # 熔断开启（保险丝断开）
    HALF_OPEN = "HALF_OPEN" # 半开启（尝试恢复中）


class CircuitOpenError(Exception):
    """熔断开启时的可分类异常（issue #242 第 7 条）。

    带 `error_type="circuit_open"`：调用方可把它并入既有错误分类管线
    （retry_policy 中为 fatal——OPEN 期内重试必然被门拒绝，重试只是白付探测成本）。
    """
    error_type = "circuit_open"

class CircuitBreaker:
    """
    熔断器实现类
    """
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,      # 连续失败多少次开启熔断
        recovery_timeout: int = 30,      # 熔断后等待多久尝试恢复（秒）
        expected_exception: type[Exception] = Exception
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.last_recovery_attempt = 0.0

    def __call__(self, func: Callable):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                # 1. 检查状态
                if self.state == CircuitState.OPEN:
                    if time.time() - self.last_failure_time > self.recovery_timeout:
                        logger.info(f"🔄 熔断器 [{self.name}] 进入半开启状态，尝试恢复...")
                        self.state = CircuitState.HALF_OPEN
                    else:
                        logger.warning(f"🛡️ 熔断器 [{self.name}] 已开启，拦截请求")
                        return self._get_fallback_value(func)

                # 2. 执行原始函数
                try:
                    result = await func(*args, **kwargs)
                    if self.state == CircuitState.HALF_OPEN:
                        logger.info(f"✅ 熔断器 [{self.name}] 恢复成功，状态闭合")
                        self._reset()
                    return result
                except self.expected_exception as e:
                    self._handle_failure(e)
                    return self._get_fallback_value(func)
            return async_wrapper
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            if self.state == CircuitState.OPEN:
                if time.time() - self.last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                else:
                    return self._get_fallback_value(func)
            try:
                result = func(*args, **kwargs)
                if self.state == CircuitState.HALF_OPEN:
                    self._reset()
                return result
            except self.expected_exception as e:
                self._handle_failure(e)
                return self._get_fallback_value(func)
        return sync_wrapper

    def _handle_failure(self, error: Exception):
        self.failure_count += 1
        self.last_failure_time = time.time()

        logger.error(f"❌ 熔断器 [{self.name}] 发生故障 ({self.failure_count}/{self.failure_threshold}): {str(error)}")

        if self.failure_count >= self.failure_threshold and self.state != CircuitState.OPEN:
            logger.critical(f"🚨 熔断器 [{self.name}] 达到阈值，正式开启熔断！")
            self.state = CircuitState.OPEN

    def _reset(self):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    # ── 状态机 API（issue #242 第 7 条）──────────────────────────────────
    # 不触碰装饰器（保持 tests/unit/test_circuit_breaker.py 的降级值契约不动）；
    # 供"失败必须可分类上浮"的调用方（如 executor 的 LLM 步骤路径）显式使用。

    def allow_request(self) -> bool:
        """是否放行一次真实调用；OPEN 超过恢复窗口则转 HALF_OPEN 放行探测。"""
        if self.state is CircuitState.CLOSED:
            return True
        if self.state is CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                logger.info(f"🔄 熔断器 [{self.name}] 进入半开启状态，尝试恢复...")
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        return True  # HALF_OPEN：放行探测（v1 不设单探测闸，多并发探测是良性的）

    def record_success(self) -> None:
        """一次成功调用：半开探测成功（或任何非 CLOSED 态下的成功）→ 闭合。"""
        if self.state is not CircuitState.CLOSED:
            self._reset()

    def record_failure(self) -> None:
        """一次失败调用：计数，达阈值转 OPEN（复用既有 _handle_failure）。"""
        self._handle_failure(CircuitOpenError("probe failed"))

    def _get_fallback_value(self, func: Callable) -> Any:
        """根据函数返回类型提供降级后的默认值"""
        # 这里可以根据实际需要扩展更复杂的降级逻辑
        logger.info(f"💡 熔断器 [{self.name}] 触发降级逻辑")

        # 获取函数的返回注解（如果有的话）
        # 注意：在 Python 3.9+ 中，typing.List 和 list 有时表现不一，这里同时检查
        return_type = getattr(func, '__annotations__', {}).get('return')

        if return_type in (list, list):
            return []
        if return_type in (dict, dict):
            return {"error": "service temporarily unavailable", "fallback": True}
        if return_type == str:
            return "服务暂时繁忙，请稍后再试。"

        return None
