"""
企业级中间件 - 性能监控与请求追踪
小白解释：就像在系统的门口装了一个计数器和秒表，记录每一个进来的请求
花了多少时间，有没有报错，方便管理员查看系统的运行健康状态。
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.logging import request_id_var, trace_id_var
from app.core.redis import redis_client

logger = logging.getLogger(__name__)


def _calc_percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = max(0.0, min(1.0, percentile / 100.0)) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    if low == high:
        return float(ordered[low])
    weight = rank - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


# 全局性能指标存储（生产环境建议使用 Redis 或 Prometheus）
class MetricsCollector:
    def __init__(self, history_size: int = 100):
        self.request_count = 0
        self.error_count = 0
        self.slow_request_count = 0
        self.total_response_time = 0.0
        self.active_connections = 0
        self.status_counts: dict[str, int] = defaultdict(int)
        self.route_stats: dict[str, dict[str, float]] = defaultdict(
            lambda: {"count": 0, "error_count": 0, "duration_sum": 0.0}
        )
        self.duration_samples = deque(maxlen=max(100, int(settings.METRICS_DURATION_SAMPLE_SIZE)))
        route_sample_size = max(50, int(settings.METRICS_ROUTE_SAMPLE_SIZE))
        self.route_duration_samples: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=route_sample_size)
        )
        self.last_update = time.time()
        self.lock = asyncio.Lock()
        self._redis_live_key = "metrics:live"
        self._last_live_persist = 0.0

        # 历史快照，用于生成趋势图
        # 小白讲解：就像一个笔记本，每隔一段时间就把当前的计数器数字抄下来，这样就能画出曲线图了。
        self.history = deque(maxlen=history_size)

    async def inc_active_connections(self):
        async with self.lock:
            self.active_connections += 1

    async def dec_active_connections(self):
        async with self.lock:
            self.active_connections = max(0, self.active_connections - 1)

    async def record_request(
        self,
        duration: float,
        is_error: bool = False,
        is_slow: bool = False,
        status_code: int = 0,
        method: str = "",
        path: str = "",
    ):
        async with self.lock:
            self.request_count += 1
            self.total_response_time += duration
            if is_error:
                self.error_count += 1
            if is_slow:
                self.slow_request_count += 1
            if status_code:
                self.status_counts[str(status_code)] += 1
            if method and path:
                route_key = f"{method.upper()} {path}"
                route_data = self.route_stats[route_key]
                route_data["count"] += 1
                route_data["duration_sum"] += duration
                self.route_duration_samples[route_key].append(duration * 1000)
                if is_error:
                    route_data["error_count"] += 1
            self.duration_samples.append(duration * 1000)
            self.last_update = time.time()

        now = time.time()
        persist_seconds = max(0, int(settings.METRICS_LIVE_PERSIST_SECONDS))
        if self._last_live_persist == 0 or now - self._last_live_persist >= persist_seconds:
            self._last_live_persist = now
            await self._persist_live()

    async def take_snapshot(self):
        """记录当前时刻的快照"""
        await self._load_live()
        async with self.lock:
            stats = await self._get_stats_internal()
            stats["timestamp"] = int(time.time())
            self.history.append(stats)
        await self._persist_snapshot(stats)

    async def _get_stats_internal(self) -> dict[str, Any]:
        avg_time = (self.total_response_time / self.request_count * 1000) if self.request_count > 0 else 0
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "response_time": round(avg_time, 2),
            "active_connections": self.active_connections,
            "slow_request_count": self.slow_request_count,
            "error_rate_percent": round((self.error_count / self.request_count * 100), 2) if self.request_count > 0 else 0.0,
            "p95_response_time_ms": round(_calc_percentile(list(self.duration_samples), 95), 2),
            "p99_response_time_ms": round(_calc_percentile(list(self.duration_samples), 99), 2),
        }

    async def get_stats(self) -> dict[str, Any]:
        await self._load_live()
        async with self.lock:
            stats = await self._get_stats_internal()
            stats["last_update"] = self.last_update
            return stats

    async def get_history(self) -> list[dict[str, Any]]:
        """获取历史趋势数据"""
        async with self.lock:
            if not self.history:
                await self._load_snapshots()
            return list(self.history)

    async def _persist_snapshot(self, stats: dict[str, Any]) -> None:
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            await redis_client.lpush(
                "metrics:snapshots", json.dumps(stats, ensure_ascii=False)
            )
            await redis_client.ltrim(
                "metrics:snapshots",
                0,
                max(100, int(settings.METRICS_DURATION_SAMPLE_SIZE)),
            )
        except Exception as e:
            logger.warning(f"Metrics snapshot persist failed: {e}")

    async def _load_snapshots(self) -> None:
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            raw_snapshots = await redis_client.lrange("metrics:snapshots", 0, -1)
            parsed: list[dict[str, Any]] = []
            for raw in raw_snapshots:
                try:
                    parsed.append(json.loads(raw))
                except (json.JSONDecodeError, TypeError):
                    continue
            if parsed:
                self.history.clear()
                self.history.extend(reversed(parsed))
        except Exception as e:
            logger.warning(f"Metrics snapshot load failed: {e}")

    async def get_route_stats(self) -> dict[str, dict[str, float]]:
        await self._load_live()
        async with self.lock:
            route_output: dict[str, dict[str, float]] = {}
            for route, stats in self.route_stats.items():
                count = int(stats.get("count", 0))
                avg_ms = (stats.get("duration_sum", 0.0) / count * 1000) if count > 0 else 0.0
                samples = list(self.route_duration_samples.get(route, []))
                route_output[route] = {
                    **dict(stats),
                    "avg_response_time_ms": round(avg_ms, 2),
                    "p95_response_time_ms": round(_calc_percentile(samples, 95), 2),
                    "p99_response_time_ms": round(_calc_percentile(samples, 99), 2),
                }
            return route_output

    async def get_prometheus_text(self) -> str:
        await self._load_live()
        async with self.lock:
            lines = [
                "# HELP app_http_requests_total Total HTTP requests",
                "# TYPE app_http_requests_total counter",
                f"app_http_requests_total {self.request_count}",
                "# HELP app_http_errors_total Total HTTP error responses",
                "# TYPE app_http_errors_total counter",
                f"app_http_errors_total {self.error_count}",
                "# HELP app_http_slow_requests_total Total slow HTTP requests",
                "# TYPE app_http_slow_requests_total counter",
                f"app_http_slow_requests_total {self.slow_request_count}",
                "# HELP app_http_active_requests Current in-flight HTTP requests",
                "# TYPE app_http_active_requests gauge",
                f"app_http_active_requests {self.active_connections}",
                "# HELP app_http_request_duration_seconds_sum Total HTTP request duration in seconds",
                "# TYPE app_http_request_duration_seconds_sum counter",
                f"app_http_request_duration_seconds_sum {self.total_response_time:.6f}",
                "# HELP app_http_error_rate_percent HTTP error rate in percent",
                "# TYPE app_http_error_rate_percent gauge",
                f"app_http_error_rate_percent {(self.error_count / self.request_count * 100) if self.request_count > 0 else 0.0:.2f}",
                "# HELP app_http_p95_ms HTTP p95 response time in milliseconds",
                "# TYPE app_http_p95_ms gauge",
                f"app_http_p95_ms {_calc_percentile(list(self.duration_samples), 95):.2f}",
                "# HELP app_http_p99_ms HTTP p99 response time in milliseconds",
                "# TYPE app_http_p99_ms gauge",
                f"app_http_p99_ms {_calc_percentile(list(self.duration_samples), 99):.2f}",
            ]

            for status_code, count in self.status_counts.items():
                lines.append(f'app_http_status_total{{status="{status_code}"}} {count}')

            for route_key, route_data in self.route_stats.items():
                method, path = route_key.split(" ", 1)
                safe_path = path.replace("\\", "\\\\").replace('"', '\\"')
                lines.append(
                    f'app_http_route_requests_total{{method="{method}",path="{safe_path}"}} {int(route_data["count"])}'
                )
                lines.append(
                    f'app_http_route_errors_total{{method="{method}",path="{safe_path}"}} {int(route_data["error_count"])}'
                )
                lines.append(
                    f'app_http_route_duration_seconds_sum{{method="{method}",path="{safe_path}"}} {route_data["duration_sum"]:.6f}'
                )
                route_samples = list(self.route_duration_samples.get(route_key, []))
                lines.append(
                    f'app_http_route_p95_ms{{method="{method}",path="{safe_path}"}} {_calc_percentile(route_samples, 95):.2f}'
                )
                lines.append(
                    f'app_http_route_p99_ms{{method="{method}",path="{safe_path}"}} {_calc_percentile(route_samples, 99):.2f}'
                )
            return "\n".join(lines) + "\n"

    async def _persist_live(self) -> None:
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            async with self.lock:
                payload = json.dumps({
                    "request_count": self.request_count,
                    "error_count": self.error_count,
                    "slow_request_count": self.slow_request_count,
                    "total_response_time": self.total_response_time,
                    "status_counts": dict(self.status_counts),
                }, ensure_ascii=False)
            await redis_client.setex(self._redis_live_key, 3600, payload)
        except Exception as e:
            logger.warning(f"Metrics live persist failed: {e}")

    async def _load_live(self) -> None:
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            raw = await redis_client.get(self._redis_live_key)
            if not raw:
                return
            data = json.loads(raw)
            async with self.lock:
                if self.request_count == 0 and int(data.get("request_count", 0) or 0) > 0:
                    self.request_count = int(data["request_count"])
                    self.error_count = int(data.get("error_count", 0) or 0)
                    self.slow_request_count = int(data.get("slow_request_count", 0) or 0)
                    self.total_response_time = float(data.get("total_response_time", 0.0) or 0.0)
                    self.status_counts.clear()
                    self.status_counts.update({
                        str(k): int(v) for k, v in (data.get("status_counts", {}) or {}).items()
                    })
        except Exception as e:
            logger.warning(f"Metrics live load failed: {e}")

# 全局单例
metrics_collector = MetricsCollector()


class PerformanceASGIMiddleware:
    """Pure ASGI metrics middleware (avoids BaseHTTPMiddleware/SQLAlchemy issues)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.time()
        status_code = {"value": 500}
        await metrics_collector.inc_active_connections()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_code["value"] = message.get("status", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            await metrics_collector.dec_active_connections()
            duration = time.time() - start
            await metrics_collector.record_request(
                duration=duration,
                is_error=status_code["value"] >= 400,
                is_slow=duration * 1000 >= settings.SLOW_REQUEST_THRESHOLD_MS,
                status_code=status_code["value"],
                method=scope.get("method", ""),
                path=scope.get("path", ""),
            )


class RateLimitASGIMiddleware:
    """Pure ASGI sliding-window rate limiter with Redis + in-memory fallback."""

    def __init__(self, app):
        self.app = app
        self.window_seconds = max(1, int(settings.RATE_LIMIT_WINDOW_SECONDS))
        self.requests_per_window = max(1, int(settings.RATE_LIMIT_REQUESTS_PER_MINUTE))
        self.exclude_paths = tuple(settings.RATE_LIMIT_EXCLUDE_PATHS or [])
        self._memory: dict[str, tuple[int, int]] = {}

    def _get_identifier(self, scope) -> str:
        headers = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in scope.get("headers", [])
        }
        forwarded = headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        client = scope.get("client") or ("unknown", 0)
        return str(client[0])

    async def _redis_incr(self, identifier: str, now: int) -> tuple[int, int]:
        key = f"rl:sliding:{identifier}"
        window_start = now - self.window_seconds
        reset_at = now + self.window_seconds
        pipeline = redis_client.pipeline()
        pipeline.zremrangebyscore(key, 0, window_start)
        pipeline.zcard(key)
        pipeline.zadd(key, {str(now): now})
        pipeline.expire(key, self.window_seconds + 5)
        results = await pipeline.execute()
        return int(results[1]) + 1, reset_at

    def _memory_incr(self, identifier: str, now: int) -> tuple[int, int]:
        window_start = now - (now % self.window_seconds)
        reset_at = window_start + self.window_seconds
        current = self._memory.get(identifier)
        if current is None or current[0] != window_start:
            self._memory[identifier] = (window_start, 1)
            return 1, reset_at
        self._memory[identifier] = (window_start, current[1] + 1)
        return current[1] + 1, reset_at

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if any(path.startswith(prefix) for prefix in self.exclude_paths):
            await self.app(scope, receive, send)
            return

        identifier = self._get_identifier(scope)
        now = int(time.time())
        count, reset_at = 0, now + self.window_seconds
        try:
            if redis_client:
                count, reset_at = await self._redis_incr(identifier, now)
            else:
                count, reset_at = self._memory_incr(identifier, now)
        except Exception:
            count, reset_at = self._memory_incr(identifier, now)

        if count > self.requests_per_window:
            response = JSONResponse(
                status_code=429,
                content={
                    "success": False,
                    "code": 429,
                    "message": "请求过于频繁，请稍后再试",
                    "detail": "rate_limit_exceeded",
                    "data": None,
                },
                headers={"Retry-After": str(max(1, reset_at - now))},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware using sliding window.

    Primary store: Redis sorted sets for accurate sliding window.
    Fallback: in-memory fixed-window counter (when Redis is unavailable).
    """

    def __init__(self, app):
        super().__init__(app)
        self.window_seconds = max(1, int(settings.RATE_LIMIT_WINDOW_SECONDS))
        self.requests_per_window = max(1, int(settings.RATE_LIMIT_REQUESTS_PER_MINUTE))
        self.exclude_paths = tuple(settings.RATE_LIMIT_EXCLUDE_PATHS or [])
        self._memory_counter: dict[str, tuple[int, int]] = {}
        self._memory_lock = asyncio.Lock()

    def _is_excluded_path(self, path: str) -> bool:
        return any(path.startswith(prefix) for prefix in self.exclude_paths)

    def _get_identifier(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    async def _check_and_incr_memory(self, key: str, now: int) -> tuple[int, int]:
        """In-memory fallback: fixed-window counter. Returns (count, reset_at)."""
        window_start = now - (now % self.window_seconds)
        reset_at = window_start + self.window_seconds
        async with self._memory_lock:
            current = self._memory_counter.get(key)
            if current is None or current[0] != window_start:
                self._memory_counter[key] = (window_start, 1)
                return (1, reset_at)
            self._memory_counter[key] = (window_start, current[1] + 1)
            return (self._memory_counter[key][1], reset_at)

    async def _check_and_incr_redis(self, identifier: str, now: int) -> tuple[int, int]:
        """Sliding window using Redis sorted set. Returns (count, reset_at)."""
        if not redis_client:
            raise RuntimeError("Redis not available")

        key = f"rl:sliding:{identifier}"
        window_start = now - self.window_seconds
        reset_at = now + self.window_seconds

        # Use a Redis pipeline for atomicity: remove old entries, count current, add new entry
        pipeline = redis_client.pipeline()
        pipeline.zremrangebyscore(key, 0, window_start)
        pipeline.zcard(key)
        pipeline.zadd(key, {str(now): now})
        pipeline.expire(key, self.window_seconds + 5)
        results = await pipeline.execute()

        count = int(results[1]) + 1  # +1 for the entry we just added
        return (count, reset_at)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if self._is_excluded_path(path):
            return await call_next(request)

        identifier = self._get_identifier(request)
        now = int(time.time())
        current_count = 0
        reset_at = now + self.window_seconds

        try:
            if redis_client:
                current_count, reset_at = await self._check_and_incr_redis(identifier, now)
            else:
                current_count, reset_at = await self._check_and_incr_memory(f"rl:{identifier}", now)
        except Exception:
            current_count, reset_at = await self._check_and_incr_memory(f"rl:{identifier}", now)

        remaining = max(0, self.requests_per_window - current_count)
        if current_count > self.requests_per_window:
            retry_after = max(1, reset_at - now)
            request_id = getattr(getattr(request, "state", None), "request_id", None)
            return JSONResponse(
                status_code=429,
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(self.requests_per_window),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                },
                content={
                    "success": False,
                    "code": 429,
                    "message": "请求过于频繁，请稍后再试",
                    "detail": "rate_limit_exceeded",
                    "request_id": request_id,
                    "data": None,
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_window)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)
        return response


class PerformanceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        await metrics_collector.inc_active_connections()
        request_id = getattr(request.state, "request_id", None) or str(uuid.uuid4())
        route_path = request.url.path
        method = request.method
        token = request_id_var.set(request_id)
        trace_token = trace_id_var.set(request.headers.get("X-Trace-ID") or request_id)

        try:
            response = await call_next(request)

            # 记录成功请求
            duration = time.time() - start_time
            is_error = response.status_code >= 400
            is_slow = duration * 1000 >= settings.SLOW_REQUEST_THRESHOLD_MS
            await metrics_collector.record_request(
                duration=duration,
                is_error=is_error,
                is_slow=is_slow,
                status_code=response.status_code,
                method=method,
                path=route_path,
            )
            if is_slow:
                logger.warning(
                    "Slow request detected: method=%s path=%s duration_ms=%.2f request_id=%s status=%s",
                    method,
                    route_path,
                    duration * 1000,
                    request_id,
                    response.status_code,
                )

            # 添加处理时间头（方便前端/运维调试）
            response.headers["X-Process-Time"] = str(round(duration * 1000, 2)) + "ms"

            # 缓存头优化：对静态资源和特定API响应添加缓存控制
            if route_path.startswith("/assets/") or route_path.endswith(".js") or route_path.endswith(".css"):
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
            elif route_path.startswith("/api/v1/knowledge/stats") or route_path.startswith("/api/v1/monitoring/health"):
                response.headers["Cache-Control"] = "public, max-age=30, stale-while-revalidate=60"
            elif route_path.startswith("/api/v1/conversations"):
                response.headers["Cache-Control"] = "private, no-cache"

            return response

        except Exception:
            # 记录异常请求
            duration = time.time() - start_time
            await metrics_collector.record_request(
                duration=duration,
                is_error=True,
                is_slow=duration * 1000 >= settings.SLOW_REQUEST_THRESHOLD_MS,
                status_code=500,
                method=method,
                path=route_path,
            )
            logger.exception(
                "Request failed: method=%s path=%s duration_ms=%.2f request_id=%s",
                method,
                route_path,
                duration * 1000,
                request_id,
            )
            raise
        finally:
            await metrics_collector.dec_active_connections()
            request_id_var.reset(token)
            trace_id_var.reset(trace_token)
