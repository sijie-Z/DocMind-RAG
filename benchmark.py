#!/usr/bin/env python3
"""DocMind QPS 压测 v4 — 干净利落"""
import asyncio, json, sys, time, statistics
try: import aiohttp
except: print("pip install aiohttp"); sys.exit(1)

BASE = "http://localhost:8000"

async def bench(name, url, method="GET", headers=None, data=None, concurrency=50):
    sem = asyncio.Semaphore(concurrency)
    conn = aiohttp.TCPConnector(limit=concurrency, force_close=False)
    durations = []
    errors = 0

    async with aiohttp.ClientSession(connector=conn) as sess:
        async def req():
            nonlocal errors
            async with sem:
                start = time.perf_counter()
                try:
                    if method == "GET":
                        async with sess.get(url, headers=headers or {}, timeout=30) as r:
                            await r.read()
                            if r.status >= 400: errors += 1
                    else:
                        async with sess.post(url, data=data, headers=headers or {}, timeout=30) as r:
                            await r.read()
                            if r.status >= 400: errors += 1
                    return time.perf_counter() - start
                except:
                    errors += 1
                    return time.perf_counter() - start

        tasks = [req() for _ in range(500)]
        start_ts = time.perf_counter()
        all_durations = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start_ts

    d = sorted(all_durations)
    avg = statistics.mean(d) if d else 0
    success = 500 - errors
    return {
        "name": name,
        "qps": round(success / elapsed, 1) if elapsed > 0 else 0,
        "avg_ms": round(avg * 1000, 1),
        "p50_ms": round(d[int(len(d)*0.5)]*1000, 1) if d else 0,
        "p95_ms": round(d[int(len(d)*0.95)]*1000, 1) if d else 0,
        "p99_ms": round(d[int(len(d)*0.99)]*1000, 1) if d else 0,
        "errors": errors,
        "total_s": round(elapsed, 2),
    }

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{BASE}/api/v1/auth/login",
                           data={"username": "guest", "password": "123456"}) as r:
            d = await r.json()
    token = (d.get("data") or {}).get("access_token", "")
    auth = {"Authorization": f"Bearer {token}"}
    print(f"Token: {'OK' if token else 'FAIL'}  |  Target: {BASE}")
    print("-" * 75)
    print(f"{'端点':20s}  并发  {'QPS':>8s}  {'avg':>8s}  {'p50':>8s}  {'p95':>8s}  {'p99':>8s}  {'错误':>5s}")
    print("-" * 75)

    endpoints = [
        ("健康检查", "GET", "/health", {}),
        ("监控健康", "GET", "/api/v1/monitoring/health", {}),
        ("知识库列表", "GET", "/api/v1/knowledge?page=1&page_size=5", auth),
        ("对话历史", "GET", "/api/v1/chat/conversations?page=1&page_size=5", auth),
        ("Agent工具列表", "GET", "/api/v1/agent/tools", auth),
        ("登录接口(POST)", "POST", "/api/v1/auth/login",
         {"Content-Type": "application/x-www-form-urlencoded"}),
    ]

    for concurrency in [20, 50, 100]:
        print(f"\n--- 并发={concurrency} ---")
        for name, method, path, hdrs in endpoints:
            body = "username=guest&password=123456" if "login" in name else None
            r = await bench(name, f"{BASE}{path}", method, hdrs, body, concurrency)
            print(f"{name:18s}  {concurrency:3d}  {r['qps']:>8.1f}  "
                  f"{r['avg_ms']:>7.1f}ms  {r['p50_ms']:>7.1f}  {r['p95_ms']:>7.1f}  "
                  f"{r['p99_ms']:>7.1f}  {r['errors']:>4d}")

if __name__ == "__main__":
    asyncio.run(main())
