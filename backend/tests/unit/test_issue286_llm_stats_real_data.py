"""issue #286 判别用例：/llm-stats 必须返回真实指标数据（不是恒零值的 200）。

背景（#283 评审核实 R3 → 立单 #286，现场日志
`Failed to get LLM stats: 'MetricsCollector' object has no attribute
'get_application_stats'`）：处理体调用的 `get_application_stats` 在类上不存在
→ 恒 AttributeError → except 吞掉 → 管理员面板 LLM 统计恒为假零。同一处理体
另有两处同款影子调用一并接回：RAG 侧按 `total_input_tokens/total_output_tokens`
读 snapshot（键从未存在，静默 0）；延迟按 `avg_response_time_ms` 读
（`get_stats()` 真实键是 `response_time`，静默 0）。

本用例构造已知用量（照 tests/unit/test_llm_usage_accounting.py 的记账模式）：
- agent 侧 120/30：`record_llm_usage` → `agent_tokens_total{model,direction}`
- RAG 侧 200/50：`rag_service.report_tokens` → RAGMetrics 窗口事件
- 延迟状态直接钉为确定态：avg=200ms、p95=100ms
断言响应数值 == 已知值（对进程级单例取 before 增量，避免与同批用例互相污染）。

变异判别（三条，见 exec 日志）：
- 还原影子调用 `metrics_collector.get_application_stats()` → except 回退零值 → 红；
- `agent_samples = []`（agent 侧丢账）→ total 少 150 → 红；
- 延迟键名还原为 `avg_response_time_ms` → avg_latency_ms 恒 0 ≠ 200 → 红。
"""
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.middleware import metrics_collector
from app.core.security import get_current_user
from app.models.rbac import PermissionType


def _agent_totals_from_registry() -> dict[str, dict[str, int]]:
    """从 prometheus 原始样本独立求和：{model: {input_tokens, output_tokens}}。

    刻意不走被测代码的 `read_counter_samples` —— 期望值与被测实现相互独立。
    """
    from app.core.prometheus import _get_real

    real = _get_real()
    assert real is not None, "conftest 应已开启 PROMETHEUS_ENABLED（真实指标）"
    out: dict[str, dict[str, int]] = {}
    for family in real["AGENT_TOKENS"].collect():
        for sample in family.samples:
            if sample.name.endswith("_created"):
                continue
            model = sample.labels.get("model", "unknown")
            entry = out.setdefault(model, {"input_tokens": 0, "output_tokens": 0})
            if sample.labels.get("direction") == "input":
                entry["input_tokens"] += int(sample.value)
            elif sample.labels.get("direction") == "output":
                entry["output_tokens"] += int(sample.value)
    return out


def _seed_latency_state():
    """把 metrics_collector 置为确定态：avg=200ms、p95=100ms。

    p95：n=21 时 rank=0.95*20=19.0 恰落 ordered[19]=100.0（见 _calc_percentile）。
    返回原状态三元组供还原（本用例不应污染共享单例）。
    """
    saved = (
        metrics_collector.request_count,
        metrics_collector.total_response_time,
        metrics_collector.duration_samples,
    )
    metrics_collector.request_count = 10
    metrics_collector.total_response_time = 2.0
    metrics_collector.duration_samples = deque([100.0] * 20 + [500.0], maxlen=21)
    return saved


def _restore_latency_state(saved):
    (
        metrics_collector.request_count,
        metrics_collector.total_response_time,
        metrics_collector.duration_samples,
    ) = saved


def test_llm_stats_returns_real_known_usage(client: TestClient):
    from app.agent.usage import record_llm_usage
    from app.main import app
    from app.services.rag_service import rag_service

    # ── before 快照（进程级单例，对已知用量取增量） ──
    agent_before = _agent_totals_from_registry()
    before_agent_in = sum(e["input_tokens"] for e in agent_before.values())
    before_agent_out = sum(e["output_tokens"] for e in agent_before.values())
    before_agent_total = before_agent_in + before_agent_out
    ds_before = agent_before.get("deepseek-chat", {"input_tokens": 0, "output_tokens": 0})

    rag_before = rag_service.get_metrics(window_seconds=24 * 3600)
    before_rag_in = int(rag_before.get("total_input_tokens", 0))
    before_rag_out = int(rag_before.get("total_output_tokens", 0))

    # ── 播已知用量：agent 120/30（=150）、RAG 200/50（=250） ──
    assert record_llm_usage(SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
        model="deepseek-chat",
    )) == 150
    rag_service.report_tokens(200, 50)

    saved = _seed_latency_state()
    user = SimpleNamespace(
        id=1, organization_id=5, is_superuser=False, role="admin", is_active=True
    )
    db = MagicMock()
    db.execute = AsyncMock()

    async def _db():
        return db

    async def _user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    try:
        with patch(
            "app.services.permission_service.permission_service.get_user_permissions",
            new=AsyncMock(return_value={p.value for p in PermissionType}),
        ):
            r = client.get("/api/v1/monitoring/llm-stats")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        _restore_latency_state(saved)

    assert r.status_code == 200, r.text
    data = r.json()

    # 1) 已知用量必须精确流到响应（修前：恒零 + 200）
    assert data["agent_tokens_today"] == before_agent_total + 150, data
    assert data["rag_tokens_today"] == before_rag_in + before_rag_out + 250, data
    assert data["total_tokens_today"] == before_agent_total + 150 + before_rag_in + before_rag_out + 250, data
    assert data["input_tokens_today"] == before_rag_in + 200 + before_agent_in + 120, data
    assert data["output_tokens_today"] == before_rag_out + 50 + before_agent_out + 30, data
    assert data["total_tokens_today"] == data["input_tokens_today"] + data["output_tokens_today"]

    # 2) model 维度（agent 侧可得则带）：deepseek-chat 增量精确
    ds_entry = next(e for e in data["agent_tokens_by_model"] if e["model"] == "deepseek-chat")
    assert ds_entry["input_tokens"] == ds_before["input_tokens"] + 120, data
    assert ds_entry["output_tokens"] == ds_before["output_tokens"] + 30, data
    assert ds_entry["total_tokens"] == ds_entry["input_tokens"] + ds_entry["output_tokens"]

    # 3) 延迟来自 metrics_collector 真实键名（修前 avg 键名错读 → 恒 0）
    assert data["avg_latency_ms"] == 200, data
    assert data["p95_latency_ms"] == 100, data

    # 4) 派生字段与合计一致（成本按前端标注的 GPT-4o-mini 费率）
    assert data["cost_today_usd"] == round(
        data["input_tokens_today"] * 0.00000015 + data["output_tokens_today"] * 0.0000006, 4
    )
    assert len(data["token_trend_7d"]) == 7
    assert all(v > 0 for v in data["token_trend_7d"]), "有真实用量时趋势不得全零"
    assert isinstance(data["request_count_today"], int)
