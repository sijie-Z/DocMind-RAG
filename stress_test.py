#!/usr/bin/env python3
"""
DocMind RAG 压力测试 v3
修复: login只发一次请求, 控制速率在限流内, 输出详细错误信息
"""

import asyncio
import aiohttp
import time
import statistics
import sys
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

BASE_URL = "http://localhost:8000"
# Rate limit: 180/min = 3/s, use 2.5/s to stay safe
MIN_INTERVAL = 0.45


@dataclass
class RequestResult:
    success: bool
    status_code: int
    latency_ms: float
    error: Optional[str] = None


@dataclass
class ScenarioReport:
    name: str
    total: int
    ok: int
    fail: int
    elapsed: float
    latencies: List[float] = field(default_factory=list)
    error_map: Dict[str, int] = field(default_factory=dict)

    @property
    def rps(self):
        return self.total / self.elapsed if self.elapsed > 0 else 0

    @property
    def success_rate(self):
        return self.ok / self.total * 100 if self.total else 0

    def pct(self, p):
        if not self.latencies:
            return 0.0
        s = sorted(self.latencies)
        return s[min(int(len(s) * p / 100), len(s) - 1)]

    def print(self):
        print(f"\n  [{self.name}]")
        print(f"  Requests: {self.total} | OK: {self.ok} ({self.success_rate:.1f}%) | Fail: {self.fail}")
        print(f"  Elapsed: {self.elapsed:.2f}s | QPS: {self.rps:.1f}")
        if self.latencies:
            print(f"  Latency(ms): avg={statistics.mean(self.latencies):.1f} "
                  f"min={min(self.latencies):.1f} max={max(self.latencies):.1f}")
            print(f"  Percentiles: P50={self.pct(50):.1f} P90={self.pct(90):.1f} "
                  f"P95={self.pct(95):.1f} P99={self.pct(99):.1f}")
        if self.error_map:
            top3 = sorted(self.error_map.items(), key=lambda x: -x[1])[:3]
            errs = " | ".join(f"{k}:{v}" for k, v in top3)
            print(f"  Errors: {errs}")


async def req(session, method, url, headers=None, data=None) -> Tuple[RequestResult, Optional[dict]]:
    """Single request, returns (result, parsed_json_or_None)"""
    start = time.perf_counter()
    try:
        kw = {"timeout": aiohttp.ClientTimeout(total=30)}
        if headers:
            kw["headers"] = headers
        if data:
            kw["data"] = data
        async with session.request(method, url, **kw) as resp:
            body = await resp.read()
            latency = (time.perf_counter() - start) * 1000
            parsed = None
            try:
                parsed = json.loads(body)
            except Exception:
                pass
            return RequestResult(
                success=200 <= resp.status < 400,
                status_code=resp.status,
                latency_ms=latency,
            ), parsed
    except Exception as e:
        latency = (time.perf_counter() - start) * 1000
        return RequestResult(False, 0, latency, str(e)[:60]), None


async def get_token(session) -> Optional[str]:
    """Login once, return token"""
    r, body = await req(session, "POST", f"{BASE_URL}/api/v1/auth/login",
                        data={"username": "guest", "password": "123456"})
    if not r.success or not body:
        return None
    return body.get("data", {}).get("access_token")


def build_report(name, results: List[Tuple[RequestResult, Optional[dict]]], elapsed) -> ScenarioReport:
    ok_list = [r for r, _ in results if r.success]
    err_map: Dict[str, int] = {}
    for r, body in results:
        if not r.success:
            if r.status_code > 0:
                msg = f"HTTP {r.status_code}"
                if body and isinstance(body, dict):
                    detail = body.get("message") or body.get("detail") or ""
                    if detail:
                        msg = f"HTTP {r.status_code}: {str(detail)[:50]}"
                err_map[msg] = err_map.get(msg, 0) + 1
            else:
                err_map[r.error or "Unknown"] = err_map.get(r.error or "Unknown", 0) + 1

    return ScenarioReport(
        name=name,
        total=len(results),
        ok=len(ok_list),
        fail=len(results) - len(ok_list),
        elapsed=elapsed,
        latencies=[r.latency_ms for r in ok_list],
        error_map=err_map,
    )


async def main():
    print("=" * 60)
    print("  DocMind RAG Stress Test v3")
    print(f"  Target: {BASE_URL}")
    print("=" * 60)

    # Verify backend
    conn = aiohttp.TCPConnector(limit=100)
    async with aiohttp.ClientSession(connector=conn) as s:
        r, _ = await req(s, "GET", f"{BASE_URL}/health")
        if not r.success:
            print("Backend unavailable!")
            return
        print(f"Backend OK (latency: {r.latency_ms:.1f}ms)\n")

        all_reports: List[ScenarioReport] = []

        # ── Phase 1: Health check baseline (excluded from rate limit) ──
        print(">> Phase 1: Health check (no rate limit)")
        results = []
        t0 = time.perf_counter()
        for i in range(200):
            r, b = await req(s, "GET", f"{BASE_URL}/health")
            results.append((r, b))
        elapsed = time.perf_counter() - t0
        rp = build_report("Health Check x200", results, elapsed)
        rp.print()
        all_reports.append(rp)

        # ── Phase 2: Login throughput (sequential, within rate limit) ──
        print("\n>> Phase 2: Login (sequential, 2.5 req/s)")
        await asyncio.sleep(2)
        results = []
        t0 = time.perf_counter()
        for i in range(30):
            r, b = await req(s, "POST", f"{BASE_URL}/api/v1/auth/login",
                             data={"username": "guest", "password": "123456"})
            results.append((r, b))
            await asyncio.sleep(MIN_INTERVAL)
        elapsed = time.perf_counter() - t0
        rp = build_report("Login x30 (sequential)", results, elapsed)
        rp.print()
        all_reports.append(rp)

        # ── Phase 3: Login parallel (5 users, controlled) ──
        print("\n>> Phase 3: Login (5 concurrent users, 6 req each)")
        await asyncio.sleep(3)

        async def user_login(n_req):
            res = []
            for _ in range(n_req):
                r, b = await req(s, "POST", f"{BASE_URL}/api/v1/auth/login",
                                 data={"username": "guest", "password": "123456"})
                res.append((r, b))
                await asyncio.sleep(MIN_INTERVAL)
            return res

        t0 = time.perf_counter()
        all_res = await asyncio.gather(*[user_login(6) for _ in range(5)])
        elapsed = time.perf_counter() - t0
        flat = [item for batch in all_res for item in batch]
        rp = build_report("Login x30 (5 concurrent)", flat, elapsed)
        rp.print()
        all_reports.append(rp)

        # ── Phase 4: Auth endpoints (pre-login, then burst read) ──
        print("\n>> Phase 4: Auth endpoints (token pre-cached)")
        await asyncio.sleep(3)
        tokens = []
        for _ in range(5):
            t = await get_token(s)
            if t:
                tokens.append(t)
            await asyncio.sleep(0.5)
        print(f"  Got {len(tokens)} tokens")

        if tokens:
            # Test /auth/me with each token
            results = []
            await asyncio.sleep(2)
            t0 = time.perf_counter()
            for token in tokens * 6:  # 30 requests total
                r, b = await req(s, "GET", f"{BASE_URL}/api/v1/auth/me",
                                 headers={"Authorization": f"Bearer {token}"})
                results.append((r, b))
                await asyncio.sleep(MIN_INTERVAL)
            elapsed = time.perf_counter() - t0
            rp = build_report("GET /auth/me x30 (auth)", results, elapsed)
            rp.print()
            all_reports.append(rp)

            # ── Phase 5: File list ──
            print("\n>> Phase 5: File list (auth + DB query)")
            await asyncio.sleep(3)
            results = []
            t0 = time.perf_counter()
            for token in tokens * 6:
                r, b = await req(s, "GET", f"{BASE_URL}/api/v1/files/list?organization_id=default&skip=0&limit=10",
                                 headers={"Authorization": f"Bearer {token}"})
                results.append((r, b))
                await asyncio.sleep(MIN_INTERVAL)
            elapsed = time.perf_counter() - t0
            rp = build_report("GET /files/list x30 (auth+DB)", results, elapsed)
            rp.print()
            all_reports.append(rp)

            # ── Phase 6: Knowledge bases ──
            print("\n>> Phase 6: Knowledge base list")
            await asyncio.sleep(3)
            results = []
            t0 = time.perf_counter()
            for token in tokens * 6:
                r, b = await req(s, "GET", f"{BASE_URL}/api/v1/knowledge/",
                                 headers={"Authorization": f"Bearer {token}"})
                results.append((r, b))
                await asyncio.sleep(MIN_INTERVAL)
            elapsed = time.perf_counter() - t0
            rp = build_report("GET /knowledge/ x30", results, elapsed)
            rp.print()
            all_reports.append(rp)

        # ── Phase 7: Burst test (rate limit boundary) ──
        print("\n>> Phase 7: Burst test (50 instant requests to /health)")
        t0 = time.perf_counter()
        burst = [req(s, "GET", f"{BASE_URL}/health") for _ in range(50)]
        burst_results = await asyncio.gather(*burst)
        elapsed = time.perf_counter() - t0
        rp = build_report("Burst /health x50", list(burst_results), elapsed)
        rp.print()
        all_reports.append(rp)

        # ── Phase 8: Burst on rate-limited endpoint ──
        print("\n>> Phase 8: Burst login (50 instant, expect 429s)")
        await asyncio.sleep(5)
        t0 = time.perf_counter()
        burst = [req(s, "POST", f"{BASE_URL}/api/v1/auth/login",
                     data={"username": "guest", "password": "123456"})
                 for _ in range(50)]
        burst_results = await asyncio.gather(*burst)
        elapsed = time.perf_counter() - t0
        rp = build_report("Burst /login x50 (expect 429)", list(burst_results), elapsed)
        rp.print()
        all_reports.append(rp)

    # ── Summary ──
    print("\n" + "=" * 80)
    print("  SUMMARY")
    print("=" * 80)
    print(f"  {'Scenario':<35} {'QPS':>7} {'Avg(ms)':>8} {'P95':>7} {'P99':>7} {'OK%':>6}")
    print(f"  {'-'*70}")
    for r in all_reports:
        p95 = r.pct(95) if r.latencies else 0
        p99 = r.pct(99) if r.latencies else 0
        avg = statistics.mean(r.latencies) if r.latencies else 0
        print(f"  {r.name:<35} {r.rps:>7.1f} {avg:>8.1f} {p95:>7.1f} {p99:>7.1f} {r.success_rate:>5.1f}%")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
