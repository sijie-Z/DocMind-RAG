"""T8 监控「时间范围选择器」判别用例（R3-A：选择器无效）。

复核根因：get_metrics 忽略 time_range 查询参数、恒返回全量历史——线上实测
1h 与 7d 响应逐字节相同（_uisweep/logs/w2t8_recheck_before.json 的
apiCrossCheck.identical=true）。修复：按 TIME_RANGE_SECONDS 窗口裁剪。

判别：1h 窗口剔除 2 小时前的点；6h 窗口保留；7d 全量；未知取值不裁剪
（向后兼容）；无 timestamp 的点在已知窗口下剔除；边界点（恰等于 cutoff）
保留；端点层 application / system 两分支都吃 time_range。
变异点：删端点里的 filter_by_time_range 调用 → 端点两用例精确红。
"""

import time

import pytest

from app.api.v1.endpoints import monitoring as monitoring_module
from app.core.middleware import metrics_collector


def _points() -> list[dict]:
    now = int(time.time())
    return [
        {"timestamp": now - 60, "request_count": 1},      # 1 分钟前：1h 内
        {"timestamp": now - 1200, "request_count": 2},    # 20 分钟前：1h 内
        {"timestamp": now - 7200, "request_count": 3},    # 2 小时前：1h 外 / 6h 内
        {"timestamp": now - 100000, "request_count": 4},  # ~27.8 小时前：24h 外 / 7d 内
    ]


def test_filter_windows_trim_and_keep():
    pts = [dict(p) for p in _points()]
    now = time.time()

    out_1h = monitoring_module.filter_by_time_range([dict(p) for p in pts], "1h", now=now)
    assert [p["request_count"] for p in out_1h] == [1, 2], out_1h

    out_6h = monitoring_module.filter_by_time_range([dict(p) for p in pts], "6h", now=now)
    assert [p["request_count"] for p in out_6h] == [1, 2, 3], out_6h

    out_7d = monitoring_module.filter_by_time_range([dict(p) for p in pts], "7d", now=now)
    assert [p["request_count"] for p in out_7d] == [1, 2, 3, 4], out_7d


def test_filter_unknown_range_is_backward_compatible():
    """未知 time_range：不裁剪（保持旧行为，向后兼容外部调用）。"""
    pts = [dict(p) for p in _points()]
    out = monitoring_module.filter_by_time_range(pts, "bogus", now=time.time())
    assert out == pts


def test_filter_boundary_and_missing_timestamp():
    now = 1_000_000.0
    pts = [
        {"timestamp": int(now) - 3600, "request_count": "恰在边界：保留"},
        {"timestamp": int(now) - 3601, "request_count": "边界外：剔除"},
        {"request_count": "无 timestamp：已知窗口下剔除"},
    ]
    out = monitoring_module.filter_by_time_range([dict(p) for p in pts], "1h", now=now)
    assert [p["request_count"] for p in out] == ["恰在边界：保留"], out


@pytest.mark.asyncio
async def test_endpoint_application_branch_honors_range(monkeypatch):
    pts = _points()

    async def fake_history():
        return [dict(p) for p in pts]

    monkeypatch.setattr(metrics_collector, "get_history", fake_history)

    r1 = await monitoring_module.get_metrics(metric_type="application", time_range="1h", db=None)
    assert len(r1["data"]) == 2, r1

    r7 = await monitoring_module.get_metrics(metric_type="application", time_range="7d", db=None)
    assert len(r7["data"]) == 4, r7


@pytest.mark.asyncio
async def test_endpoint_system_branch_honors_range(monkeypatch):
    pts = _points()

    async def fake_sys_history():
        return [dict(p) for p in pts]

    monkeypatch.setattr(monitoring_module.system_metrics_sampler, "get_history", fake_sys_history)

    r1 = await monitoring_module.get_metrics(metric_type="system", time_range="1h", db=None)
    assert len(r1["data"]) == 2, r1

    r7 = await monitoring_module.get_metrics(metric_type="system", time_range="7d", db=None)
    assert len(r7["data"]) == 4, r7
