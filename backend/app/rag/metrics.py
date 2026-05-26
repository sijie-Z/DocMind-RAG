"""RAG performance metrics — standalone, no business logic coupling."""
import time
from collections import defaultdict
from typing import Any


class RAGMetrics:
    """Collects and reports RAG pipeline performance metrics."""

    def __init__(self):
        self._counters: dict[str, int] = defaultdict(int)
        self._events: dict[str, list[tuple]] = defaultdict(list)

    def inc(self, key: str, value: int = 1) -> None:
        self._counters[key] += value

    def record_event(self, key: str, value: float) -> None:
        now = time.time()
        self._events[key].append((now, value))
        # Keep last 24h
        cutoff = now - 24 * 3600
        arr = self._events[key]
        while arr and arr[0][0] < cutoff:
            arr.pop(0)

    def window_sum(self, key: str, window_seconds: int) -> float:
        now = time.time()
        cutoff = now - window_seconds if window_seconds > 0 else 0
        return sum(v for ts, v in self._events.get(key, []) if ts >= cutoff)

    def window_count(self, key: str, window_seconds: int) -> int:
        now = time.time()
        cutoff = now - window_seconds if window_seconds > 0 else 0
        return sum(1 for ts, _ in self._events.get(key, []) if ts >= cutoff)

    def percentile(self, key: str, pct: float, window_seconds: int) -> float | None:
        now = time.time()
        cutoff = now - window_seconds if window_seconds > 0 else 0
        arr = sorted(v for ts, v in self._events.get(key, []) if ts >= cutoff)
        if not arr:
            return None
        idx = int(len(arr) * pct)
        return arr[min(idx, len(arr) - 1)]

    def get_snapshot(self, window_seconds: int = 0) -> dict[str, Any]:
        if window_seconds > 0:
            rt = max(1, self.window_sum("retrieval", window_seconds))
            rh = self.window_sum("retrieval_hit", window_seconds)
            gt = max(1, self.window_sum("grounded", window_seconds))
            gh = self.window_sum("grounded_hit", window_seconds)
            lc = max(1, self.window_count("latency", window_seconds))
            ls = self.window_sum("latency", window_seconds)
            ch = self.window_sum("cache_hit", window_seconds)
            sc = self.window_sum("semantic_cache_hit", window_seconds)
            rt_retry = self.window_sum("retry", window_seconds)
            return {
                "window_seconds": window_seconds,
                "retrieval_total": int(rt),
                "retrieval_hit": int(rh),
                "hit_rate": round(rh / rt, 4),
                "grounded_total": int(gt),
                "grounded_hit": int(gh),
                "groundedness": round(gh / gt, 4),
                "avg_latency_ms": round(ls / lc, 2),
                "latency_p50_ms": round(self.percentile("latency", 0.5, window_seconds) or 0, 2),
                "latency_p95_ms": round(self.percentile("latency", 0.95, window_seconds) or 0, 2),
                "latency_p99_ms": round(self.percentile("latency", 0.99, window_seconds) or 0, 2),
                "cache_hit": int(ch),
                "semantic_cache_hit": int(sc),
                "retry_total": int(rt_retry),
                "cache_hit_rate": round(ch / rt, 4) if rt > 0 else 0,
            }

        rt = max(1, self._counters["retrieval_total"])
        gt = max(1, self._counters["grounded_total"])
        lc = max(1, self._counters["latency_count"])
        return {
            "window_seconds": 0,
            "retrieval_total": self._counters["retrieval_total"],
            "retrieval_hit": self._counters["retrieval_hit"],
            "hit_rate": round(self._counters["retrieval_hit"] / rt, 4),
            "grounded_total": self._counters["grounded_total"],
            "grounded_hit": self._counters["grounded_hit"],
            "groundedness": round(self._counters["grounded_hit"] / gt, 4),
            "avg_latency_ms": round(self._counters["latency_sum_ms"] / lc, 2),
            "cache_hit": self._counters["cache_hit"],
            "semantic_cache_hit": self._counters.get("semantic_cache_hit", 0),
            "retry_total": self._counters["retry_total"],
            "cache_hit_rate": round(self._counters["cache_hit"] / rt, 4) if rt > 0 else 0,
        }

    def reset(self) -> None:
        self._counters.clear()
        self._events.clear()
