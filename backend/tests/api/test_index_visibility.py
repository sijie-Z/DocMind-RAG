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

`#223`（同一函数的对账口径）：
- 「恰好为 0」不够：`claimed=10 / indexed_chunks=1` 也是 `unhealthy` —— 按
  `document_id` 去重数对账（部分文档整个丢了），并按 `SUM(chunk_count)` 的
  chunk 覆盖率判定（<0.95 → `unhealthy`；0.95–1.0 → `degraded`）。
- 重建窗口：有未终局处理任务（QUEUED/PROCESSING）的文档不进对账基线 ——
  rebuild 会先删 ES chunk、状态保持 INDEXED 直到 worker 接单。
- 大库（>40000 文档）的 cardinality 是 HLL 估计值，允许 2% 容差。
- 详情必须暴露 `indexed_documents` / `expected_chunks` / `coverage_ratio` / `mismatch`。

`#182`（搜索端点）：
- 空结果 + 空索引 → `index_status == "empty_index"`
- 空结果 + 索引有数据 → `index_status == "ok"`
- 有结果 → `index_status == "ok"`，且**不去查 ES**（避免每次搜索多一次 round-trip）
- `search_time` 真的有值（此前声明了却从未赋值，恒为 null）

## 变异验证

把 `_knowledge_base_health` 里 `if indexed_chunks == 0 and claimed > 0:` 的
返回改成 `"healthy"`（即退回「不检查内容」的旧行为），核心用例必须变红。
#223 起还需验证：删掉 `indexed_docs < claimed` 判定、或删掉覆盖率阈值判定，
对应的部分丢失 / 比例边界用例必须变红。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user


# ══════════════════════════════════════════════════════════════════════
# #183 · 知识库健康度（模块级函数，可独立测）
# ══════════════════════════════════════════════════════════════════════
def _patch_es(*, count: int = 0, docs: int = 0, index_error: str | None = None):
    """patch 掉 `_knowledge_base_health` 用到的两个 ES 入口 + DB 会话。

    被测函数内部是 `from app.core.elasticsearch import ...` 的**局部导入**，
    所以 patch 的目标是 `app.core.elasticsearch` 上的名字。

    #223 起对账用**一次** ``search``（chunk 总数 + document_id 去重数）：
    ``_patch_es(count=..., docs=...)`` 分别控制这两个数字。
    """
    fake_client = MagicMock()
    fake_client.search = AsyncMock(
        return_value={
            "hits": {"total": {"value": count, "relation": "eq"}},
            "aggregations": {"documents": {"value": docs}},
        }
    )
    return (
        patch("app.core.elasticsearch.get_index_error", return_value=index_error),
        patch("app.core.elasticsearch.get_elasticsearch", new=AsyncMock(return_value=fake_client)),
    )


def _patch_db_count(claimed: int, expected_chunks: int | None = None):
    """patch `AsyncSessionLocal`，让对账查询返回 (声称 INDEXED 文档数, chunk_count 总和)。"""
    sess = MagicMock()
    result = MagicMock()
    result.one = MagicMock(
        return_value=(claimed, claimed if expected_chunks is None else expected_chunks)
    )
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

    p_err, p_es = _patch_es(count=0, docs=0)
    with p_err, p_es, _patch_db_count(10, 39):
        status, details = await _knowledge_base_health()

    assert status == "unhealthy", "状态在撒谎时不能报 healthy —— 这就是 #183"
    assert details["indexed_chunks"] == 0
    assert details["documents_marked_indexed"] == 10
    assert "10 篇文档状态为 INDEXED" in details["mismatch"]


@pytest.mark.asyncio
async def test_empty_index_with_no_claims_is_healthy():
    """全新部署：索引为空、也没有文档声称已索引 —— 合法，不该报故障。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=0, docs=0)
    with p_err, p_es, _patch_db_count(0, 0):
        status, details = await _knowledge_base_health()

    assert status == "healthy"
    assert details["indexed_chunks"] == 0
    assert details["documents_marked_indexed"] == 0
    assert "mismatch" not in details


@pytest.mark.asyncio
async def test_index_with_data_is_healthy():
    """正例：#183 里「39 条 chunk / 10 篇声称」的完整索引必须仍是 healthy。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=39, docs=10)
    with p_err, p_es, _patch_db_count(10, 39):
        status, details = await _knowledge_base_health()

    assert status == "healthy"
    assert details["indexed_chunks"] == 39
    assert details["indexed_documents"] == 10
    assert details["expected_chunks"] == 39
    assert details["coverage_ratio"] == 1.0
    assert "mismatch" not in details


@pytest.mark.asyncio
async def test_partial_document_loss_is_unhealthy():
    """★ #223 核心：10 篇声称 INDEXED、索引里只剩 1 篇的 chunk —— 必须 unhealthy。

    旧口径（`indexed_chunks == 0 and claimed > 0`）在这里会报 healthy：
    1 条 chunk 足以让它认为「索引里有数据」。按 document_id 对账才抓得住。
    """
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=1, docs=1)
    with p_err, p_es, _patch_db_count(10, 39):
        status, details = await _knowledge_base_health()

    assert status == "unhealthy", "部分文档整篇丢失不能被当成 healthy（#223）"
    assert details["indexed_chunks"] == 1
    assert details["indexed_documents"] == 1
    assert details["documents_marked_indexed"] == 10
    assert "9 篇文档完全检索不到" in details["mismatch"]


@pytest.mark.asyncio
async def test_document_count_mismatch_is_unhealthy_even_if_chunk_total_matches():
    """chunk 总数够、但有文档一篇都搜不到：仍须 unhealthy（按文档对账）。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=39, docs=9)
    with p_err, p_es, _patch_db_count(10, 39):
        status, details = await _knowledge_base_health()

    assert status == "unhealthy"
    assert details["indexed_documents"] == 9
    assert "1 篇文档完全检索不到" in details["mismatch"]


@pytest.mark.asyncio
async def test_chunk_coverage_below_threshold_is_unhealthy():
    """chunk 覆盖率 0.94 < 0.95 → unhealthy（部分 chunk 丢失）。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=94, docs=10)
    with p_err, p_es, _patch_db_count(10, 100):
        status, details = await _knowledge_base_health()

    assert status == "unhealthy"
    assert details["coverage_ratio"] == 0.94
    assert "低于阈值" in details["mismatch"]


@pytest.mark.asyncio
async def test_chunk_coverage_at_threshold_is_degraded():
    """chunk 覆盖率恰好 0.95（边界）→ degraded，而不是 unhealthy/healthy。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=95, docs=10)
    with p_err, p_es, _patch_db_count(10, 100):
        status, details = await _knowledge_base_health()

    assert status == "degraded"
    assert details["coverage_ratio"] == 0.95
    assert "mismatch" in details


@pytest.mark.asyncio
async def test_chunk_coverage_near_full_is_degraded():
    """0.95–1.0 之间（如 0.99）→ degraded：可服务，但需告警。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=99, docs=10)
    with p_err, p_es, _patch_db_count(10, 100):
        status, details = await _knowledge_base_health()

    assert status == "degraded"
    assert details["coverage_ratio"] == 0.99


@pytest.mark.asyncio
async def test_document_shortfall_within_cardinality_tolerance_is_healthy():
    """>4 万文档时 cardinality 是 HLL 估计值：2% 以内的短少不得误报 unhealthy。

    否则大库会因为估计误差让所有副本 /readyz 摘流（precision_threshold 是
    ES 允许的最大值 40000，超过后计数不再精确）。
    """
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=100_000, docs=49_700)
    with p_err, p_es, _patch_db_count(50_000, 100_000):
        status, details = await _knowledge_base_health()

    assert status == "healthy", "大库的 HLL 估计误差不能被当成文档丢失"
    assert details["document_coverage_ratio"] == 0.994


@pytest.mark.asyncio
async def test_large_cardinality_shortfall_beyond_tolerance_is_unhealthy():
    """>4 万文档时，超过 2% 容差的短少仍须 unhealthy。"""
    from app.main import _knowledge_base_health

    p_err, p_es = _patch_es(count=100_000, docs=40_000)
    with p_err, p_es, _patch_db_count(50_000, 100_000):
        status, details = await _knowledge_base_health()

    assert status == "unhealthy"
    assert "10000 篇文档完全检索不到" in details["mismatch"]


@pytest.mark.asyncio
async def test_reconciliation_excludes_documents_with_active_jobs():
    """重建窗口（ES 旧 chunk 已删、状态仍 INDEXED）不得把文档计入对账基线。

    否则一次正常的单文档重建（worker 积压时窗口无上界）会让所有副本摘流。
    只排除「最新任务未终局」的文档（旧 task 可能永远停在 QUEUED）。
    """
    from app.main import _knowledge_base_health

    sess = MagicMock()
    result = MagicMock()
    result.one = MagicMock(return_value=(0, 0))
    sess.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=sess)
    ctx.__aexit__ = AsyncMock(return_value=False)

    p_err, p_es = _patch_es(count=0, docs=0)
    with p_err, p_es, patch("app.core.database.AsyncSessionLocal", return_value=ctx):
        await _knowledge_base_health()

    stmt = sess.execute.await_args.args[0].compile(compile_kwargs={"literal_binds": True})
    sql = " ".join(str(stmt).split())
    assert "knowledge_processing_jobs.document_id = documents.id" in sql
    assert "max(knowledge_processing_jobs.id)" in sql, "只看该文档最新任务的状态"
    assert "'QUEUED'" in sql and "'PROCESSING'" in sql
    assert "NOT (EXISTS" in sql, "对账查询必须排除有未终局处理任务的文档（重建窗口）"


@pytest.mark.asyncio
async def test_missing_index_is_unhealthy_not_degraded():
    """索引被带外删除（ES NotFound）→ unhealthy；吞成 degraded 会让 /readyz fail-open。"""
    from elasticsearch import NotFoundError

    from app.main import _check_knowledge_base

    meta = MagicMock()
    meta.status = 404
    err = NotFoundError("index_not_found_exception", meta, {})
    with patch("app.main._knowledge_base_health", new=AsyncMock(side_effect=err)):
        status, details = await _check_knowledge_base()

    assert status == "unhealthy"
    assert "index_error" in details


@pytest.mark.asyncio
async def test_deterministic_api_errors_are_unhealthy():
    """运行期确定性 ES 错误（mapping 400 / 鉴权 403）同样 fail-closed → unhealthy。"""
    from elasticsearch import ApiError

    from app.main import _check_knowledge_base

    for status_code in (400, 403):
        meta = MagicMock()
        meta.status = status_code
        err = ApiError("deterministic_failure", meta, {})
        with patch("app.main._knowledge_base_health", new=AsyncMock(side_effect=err)):
            status, details = await _check_knowledge_base()
        assert status == "unhealthy", f"HTTP {status_code} 不应被吞成 degraded"
        assert "index_error" in details


@pytest.mark.asyncio
async def test_transient_api_error_stays_degraded():
    """429 等瞬时错误保留 degraded —— 不因一次限流让所有副本摘流。"""
    from elasticsearch import ApiError

    from app.main import _check_knowledge_base

    meta = MagicMock()
    meta.status = 429
    err = ApiError("too_many_requests", meta, {})
    with patch("app.main._knowledge_base_health", new=AsyncMock(side_effect=err)):
        status, _ = await _check_knowledge_base()

    assert status == "degraded"


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
