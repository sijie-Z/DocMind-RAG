"""
企业级熔断器 - 防止系统雪崩
小白解释：就像家里的保险丝，如果某个外部服务（比如 AI 接口）一直报错，
保险丝就会断开——调用方通过 `allow_request()` 得到「拒绝」，再按既有错误分类
（`CircuitOpenError`）快速失败或降级，而不是让整个系统卡死在那。

当前接线状态（issue #106 §4c 核实；issue #242 第 7 条更新；issue #467 收口）：
**唯一公开契约是状态机 API**——`allow_request / record_success / record_failure`
（`executor._execute_step_once` 已接入，`CircuitOpenError.error_type="circuit_open"`
进 retry_policy 的 fatal 分支，实现跨请求熔断 + 半开恢复）。

issue #467 删除了从未被使用的装饰器路径（`__call__` + `_get_fallback_value`）：
它会在失败/熔断时吞掉异常并返回按返回注解推导的降级值（list→[]、dict→{...}、
str→提示语、无注解→None），调用方无法区分「成功返回了空结果」与「服务已熔断/
失败」，会破坏执行器的错误分类与重试。若未来确需装饰器式调用，必须先把 OPEN
做成可分类异常（`CircuitOpenError`）或显式 degraded 标记，并把
`tests/unit/test_circuit_breaker.py::TestDecoratorPathRemoved` 的回归钉一起更新。
"""

import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"  # 正常运行（保险丝闭合）
    OPEN = "OPEN"  # 熔断开启（保险丝断开）
    HALF_OPEN = "HALF_OPEN"  # 半开启（尝试恢复中）


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
        failure_threshold: int = 5,  # 连续失败多少次开启熔断
        recovery_timeout: int = 30,  # 熔断后等待多久尝试恢复（秒）
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self.last_recovery_attempt = 0.0

    def _handle_failure(self, error: Exception):
        self.failure_count += 1
        self.last_failure_time = time.time()

        logger.error(
            f"❌ 熔断器 [{self.name}] 发生故障 ({self.failure_count}/{self.failure_threshold}): {str(error)}"
        )

        if self.failure_count >= self.failure_threshold and self.state != CircuitState.OPEN:
            logger.critical(f"🚨 熔断器 [{self.name}] 达到阈值，正式开启熔断！")
            self.state = CircuitState.OPEN

    def _reset(self):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    # ── 状态机 API（issue #242 第 7 条）──────────────────────────────────
    # issue #467：装饰器式「降级返回值」路径已删除——它吞异常且不可分类，
    # 全仓零使用。调用方（如 executor 的 LLM 步骤路径）一律显式使用本组 API，
    # 失败必须可分类上浮（CircuitOpenError）。

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
        """一次失败调用：计数，达阈值转 OPEN（复用既有 _handle_failure）。

        注入异常只承载分类（error_type="circuit_open"），文案保持中性——
        它同时服务真实调用失败与半开探测失败，不应显示为"probe failed"
        （#255 复核报告 minor-7；该编号属复核报告体系，issue #256 明细为 1~6）。
        """
        self._handle_failure(CircuitOpenError("failure recorded"))
