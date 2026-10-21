"""系统指标采样器 —— /monitoring 页 CPU/内存趋势图的数据源（round2/sampling）。

背景：monitoring dashboard 的 ``trends.system`` 此前恒为硬编码 ``[]``（注释
「待实现历史记录」），CPU/内存趋势图永久空轴。本模块提供进程内周期采样：

- 采集：psutil 读 CPU% / 内存%（非阻塞 ``interval=None``）；
- 写入：每次采样 append 进进程内环形缓冲（``deque(maxlen=N)``），并镜像到
  Redis 有界列表 ``metrics:system:snapshots``（键名风格对齐 core/middleware.py
  的 ``metrics:snapshots``；``LTRIM`` 保留最近 N 条；Redis 不可用时仅内存兜底）；
- 读取：``get_history()`` 按旧→新返回；内存为空时从 Redis 回填（跨 worker/
  重启可见）；无数据 / 键缺失一律返回空列表（诚实空态，不造假）。

接线：``main.py`` 的 ``metrics_snapshot_task`` 周期调用 ``record_sample()``，与
既有应用指标快照共用 ``ENABLE_MONITORING`` 门控——监控关则采样不启动（读端点
不受该开关影响，见 core/config/base.py 的语义注释）。

可测试性：时钟（``now_fn``）与采集函数（``collect_fn``）构造器注入——测试用
受控时钟与确定性采集值，零真实 sleep、零真实 psutil 调用。
"""

import json
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Redis 键名风格对齐 core/middleware.py 的 "metrics:snapshots"（应用指标快照）。
SYSTEM_METRICS_REDIS_KEY = "metrics:system:snapshots"

# 有界保留上限：默认 60s 采样间隔（MONITORING_INTERVAL）× 1440 ≈ 24 小时。
# 内存环形缓冲与 Redis LTRIM 共用同一上限；要更大窗口改这一个常量即可。
SYSTEM_METRICS_HISTORY_SIZE = 1440


def collect_system_metrics() -> dict[str, float]:
    """默认采集函数：CPU% / 内存%（psutil，非阻塞模式）。"""
    import psutil

    return {
        "cpu_percent": psutil.cpu_percent(interval=None),
        "memory_percent": psutil.virtual_memory().percent,
    }


class SystemMetricsSampler:
    """系统指标采样器（时钟与采集函数可注入，供测试确定性驱动）。"""

    def __init__(
        self,
        history_size: int = SYSTEM_METRICS_HISTORY_SIZE,
        collect_fn: Callable[[], dict[str, float]] | None = None,
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self._history_size = max(1, int(history_size))
        self.history: deque[dict[str, Any]] = deque(maxlen=self._history_size)
        self._collect = collect_fn or collect_system_metrics
        self._now = now_fn or time.time

    async def record_sample(self) -> dict[str, Any]:
        """采集一次系统指标并写入内存环形缓冲 + Redis 有界列表。

        Redis 持久化失败不抛出：内存样本仍保留（对齐 middleware._persist_snapshot
        的降级语义）；采集函数自身抛错由调用方（快照任务循环）兜异常。
        """
        sample: dict[str, Any] = {**self._collect(), "timestamp": int(self._now())}
        self.history.append(sample)
        await self._persist_sample(sample)
        return sample

    async def get_history(self) -> list[dict[str, Any]]:
        """读取采样历史（旧→新）。内存为空时从 Redis 回填；无数据 → 空列表。"""
        if not self.history:
            await self._load_samples()
        return list(self.history)

    async def _persist_sample(self, sample: dict[str, Any]) -> None:
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            await redis_client.lpush(
                SYSTEM_METRICS_REDIS_KEY, json.dumps(sample, ensure_ascii=False)
            )
            # lpush 头插最新；LTRIM 0..N-1 即保留最近 N 条（有界保留）。
            await redis_client.ltrim(SYSTEM_METRICS_REDIS_KEY, 0, self._history_size - 1)
        except Exception as e:
            logger.warning(f"System metrics sample persist failed: {e}")

    async def _load_samples(self) -> None:
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            raw_samples = await redis_client.lrange(SYSTEM_METRICS_REDIS_KEY, 0, -1)
            parsed: list[dict[str, Any]] = []
            for raw in raw_samples:
                try:
                    parsed.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue
            if parsed:
                self.history.clear()
                # 列表头插为最新（新→旧）；反转为旧→新，与内存缓冲同向。
                self.history.extend(reversed(parsed))
        except Exception as e:
            logger.warning(f"System metrics sample load failed: {e}")


# 模块级单例：main.py lifespan 的快照任务与 monitoring 端点共用同一份内存历史。
system_metrics_sampler = SystemMetricsSampler()
