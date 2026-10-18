"""让「索引是空的」这件事可见 —— issue #182 与 #183 的回归。

## 背景（两个 issue 是同一件事的两面）

**`#182`**：`POST /api/v1/knowledge/search` 在索引为空时返回 `200` + `results: []`
+ `search_time: null`。而「没匹配到」是正常结果、「索引是空的」是严重故障 ——
**两者在响应里同形**，调用方（前端 / 测试 / 监控）无从分辨。

**`#183`**：`/health` 的 `elasticsearch` 项查的是**集群健康**（green/yellow）——
**一个全新的空集群同样 healthy**。所以它对「索引里一篇都没有」完全沉默。
实测过的那次：文档表 10 篇标着 `INDEXED`、ES 里 0 篇、`/health` 五项全绿。

## 本文件钉住的

`#183`（`_knowledge_base_health()`，已抽成模块级函数以便独立测试）：
- 索引配置有问题 → `unhealthy`
- **有文档声称已索引、但索引里一篇都没有 → `unhealthy`** ← 核心，就是那个「假状态」
- 索引为空且没有文档声称已索引 → `healthy`（全新部署，合法）
- 索引里有数据 → `healthy`

`#182`（搜索端点）：
- 空结果 + 空索引 → `index_status == "empty_index"`
- 空结果 + 索引有数据 → `index_status == "ok"`
- 有结果 → `index_status == "ok"`，且**不去查 ES**（避免每次搜索多一次 round-trip）
- `search_time` 真的有值（此前声明了却从未赋值，恒为 null）

## 变异验证

把 `_knowledge_base_health` 里 `if indexed_chunks == 0 and claimed > 0:` 的
返回改成 `"healthy"`（即退回「不检查内容」的旧行为），核心用例必须变红。
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user


# ══════════════════════════════════════════════════════════════════════
# #183 · 知识库健康度（模块级函数，可独立测）
# ══════════════════════════════════════════════════════════════════════
def _patch_es(*, count: int | None = 0, index_error: str | None = None):
    """patch 掉 `_knowledge_base_health` 用到的两个 ES 入口 + DB 会话。

    被测函数内部是 `from app.core.elasticsearch import ...` 的**局部导入**，
    所以 patch 的目标是 `app.core.elasticsearch` 上的名字。
    """
    fake_client = MagicMock()
    fake_client.count = AsyncMock(return_value={"count": count})
    return (
        patch("app.core.elasticsearch.get_index_error", return_value=index_error),
        patch("app.core.elasticsearch.get_elasticsearch", new=AsyncMock(return_value=fake_client)),
    )


def _patch_db_count(n: int):
    """patch `AsyncSessionLocal`，让对账查询返回 n。"""
    sess = MagicMock()
    result = MagicMock()
    result.scalar = MagicMock(return_value=n)
    sess.execute = AsyncMock(return_value=result)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=sess)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch("app.core.database.AsyncSessionLocal", return_value=ctx)


@pytest.mark.asyncio
async def test_index_error_makes_knowledge_base_unhealthy():
    """索引配置错误 → unhealthy，且细节里带原因。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(index_error="向量维度不匹配：索引里是 2048，配置是 1024")
    with p_err, p_es:
        status, details = await _knowledge_base_health()

    assert status == "unhealthy"
    assert "维度不匹配" in details["index_error"]


@pytest.mark.asyncio
async def test_claimed_indexed_but_empty_index_is_unhealthy():
    """★ 核心：文档表说已索引、索引里却是空的 —— 必须 unhealthy。

    这正是 issue #183 实测到的那个假状态（10 篇 INDEXED / ES 0 篇 / health 全绿）。
    """
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=0)
    with p_err, p_es, _patch_db_count(10):
        status, details = await _knowledge_base_health()

    assert status == "unhealthy", "状态在撒谎时不能报 healthy —— 这就是 #183"
    assert details["indexed_chunks"] == 0
    assert details["documents_marked_indexed"] == 10
    assert "10 篇文档状态为 INDEXED" in details["mismatch"]


@pytest.mark.asyncio
async def test_empty_index_with_no_claims_is_healthy():
    """全新部署：索引为空、也没有文档声称已索引 —— 合法，不该报故障。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=0)
    with p_err, p_es, _patch_db_count(0):
        status, details = await _knowledge_base_health()

    assert status == "healthy"
    assert details["indexed_chunks"] == 0
    assert details["documents_marked_indexed"] == 0
    assert "mismatch" not in details


@pytest.mark.asyncio
async def test_index_with_data_is_healthy():
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=39)
    with p_err, p_es, _patch_db_count(10):
        status, details = await _knowledge_base_health()

    assert status == "healthy"
    assert details["indexed_chunks"] == 39


# ══════════════════════════════════════════════════════════════════════
# #182 · 搜索端点要能说清「为什么是空的」
# ══════════════════════════════════════════════════════════════════════
@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


def _override_auth():
    from app.main import app

    mock_user = MagicMock()
    mock_user.id = 1
    mock_user.username = "testuser"
    mock_user.role = "admin"
    mock_user.organization_id = 1
    mock_user.is_superuser = True
    mock_user.is_active = True

    mock_db = MagicMock()

    async def _db():
        return mock_db

    async def _user():
        return mock_user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _user
    return app


def _post_search(client: TestClient, results: list, diagnose):
    """发一次搜索请求；`diagnose` 是给 `_diagnose_empty_results` 的返回值。"""
    app = _override_auth()
    with (
        patch("app.api.v1.endpoints.knowledge.knowledge_service") as mock_ks,
        patch(
            "app.api.v1.endpoints.knowledge._diagnose_empty_results",
            new=AsyncMock(return_value=diagnose),
        ) as mock_diag,
        # B4：搜索按组织设置解析 top_k；打桩为 None（平台默认），
        # 避免真实服务 await MagicMock 化的 db。
        patch(
            "app.api.v1.endpoints.knowledge.organization_settings_service.resolve_retrieval_top_k",
            new=AsyncMock(return_value=None),
        ),
    ):
        mock_ks.search_knowledge = AsyncMock(return_value=results)
        try:
            r = client.post(
                "/api/v1/knowledge/search",
                json={"query": "随便问问", "organization_id": 1},
                headers={"Authorization": "Bearer test_token"},
            )
            return r, mock_diag
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


def test_empty_results_on_empty_index_says_why(client: TestClient):
    """空结果 + 索引为空 → 必须告诉调用方「索引是空的」。"""
    r, _ = _post_search(client, [], ("empty_index", 0))

    assert r.status_code == 200
    body = r.json()
    assert body["results"] == []
    assert body["index_status"] == "empty_index", "空索引与没匹配到必须可区分（#182）"
    assert body["index_doc_count"] == 0


def test_empty_results_with_data_in_index_says_ok(client: TestClient):
    """空结果但索引里有数据 → `ok`（就是没匹配到，属正常）。"""
    r, _ = _post_search(client, [], ("ok", 39))

    body = r.json()
    assert body["index_status"] == "ok"
    assert body["index_doc_count"] == 39


def test_non_empty_results_skip_the_diagnosis(client: TestClient):
    """有结果时不该多跑一次 ES 计数 —— 诊断只在空结果时才值这个 round-trip。"""
    r, mock_diag = _post_search(client, [{"id": "c1", "text": "内容", "score": 0.9}], ("ok", None))

    assert r.status_code == 200
    assert r.json()["total_count"] == 1
    assert r.json()["index_status"] == "ok"
    assert not mock_diag.await_count, "有结果时不该调用诊断"


def test_search_time_is_populated(client: TestClient):
    """`search_time` 此前声明了却从未赋值，恒为 null。"""
    r, _ = _post_search(client, [], ("empty_index", 0))

    assert r.json()["search_time"] is not None, "search_time 必须真的有值"
    assert r.json()["search_time"] >= 0
