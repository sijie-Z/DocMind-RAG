# -*- coding: utf-8 -*-
"""
企业级熔断器 - 防止系统雪崩，提供优雅降级方案
小白解释：就像家里的保险丝，如果某个外部服务（比如 AI 接口）一直报错，
保险丝就会断开，直接返回预设好的“友好提示”，而不是让整个系统卡死在那。
"""

import asyncio
import functools
import logging
import time
from enum import Enum
from typing import Callable, Any, Dict, Optional, Type, List  # 补全了 List 导入

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "CLOSED"      # 正常运行（保险丝闭合）
    OPEN = "OPEN"          # 熔断开启（保险丝断开）
    HALF_OPEN = "HALF_OPEN" # 半开启（尝试恢复中）

class CircuitBreaker:
    """
    熔断器实现类
    """
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,      # 连续失败多少次开启熔断
        recovery_timeout: int = 30,      # 熔断后等待多久尝试恢复（秒）
        expected_exception: Type[Exception] = Exception
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
        else:
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
        
        if self.failure_count >= self.failure_threshold:
            if self.state != CircuitState.OPEN:
                logger.critical(f"🚨 熔断器 [{self.name}] 达到阈值，正式开启熔断！")
                self.state = CircuitState.OPEN

    def _reset(self):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0

    def _get_fallback_value(self, func: Callable) -> Any:
        """根据函数返回类型提供降级后的默认值"""
        # 这里可以根据实际需要扩展更复杂的降级逻辑
        logger.info(f"💡 熔断器 [{self.name}] 触发降级逻辑")
        
        # 获取函数的返回注解（如果有的话）
        # 注意：在 Python 3.9+ 中，typing.List 和 list 有时表现不一，这里同时检查
        return_type = getattr(func, '__annotations__', {}).get('return')
        
        if return_type in (list, List):
            return []
        if return_type in (dict, Dict):
            return {"error": "service temporarily unavailable", "fallback": True}
        if return_type == str:
            return "服务暂时繁忙，请稍后再试。"
            
        return None

# 定义常用的熔断器实例
ai_service_breaker = CircuitBreaker(name="AI-DeepSeek", failure_threshold=3, recovery_timeout=60)
db_service_breaker = CircuitBreaker(name="Database", failure_threshold=10, recovery_timeout=30)
es_service_breaker = CircuitBreaker(name="Elasticsearch", failure_threshold=5, recovery_timeout=45)
