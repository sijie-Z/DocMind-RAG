"""B1 · issue #196：embedding 单一真源 + 模型/维度一致性。

钉住的契约（修复前每条都红）：

1. **唯一构造点**：检索期不得再有独立构造的 embedding 客户端
   （旧 `app.dependencies._build_embedding_client` 完全不读 `ENABLE_LOCAL_EMBEDDING`，
   本机曾出现索引 `nomic-embed-text` / 查询 `bge-m3` 的模型漂移）。
   `get_rag_pipeline()` 注入的 provider 必须就是索引期同一个
   `embedding_service` 单例。
2. **单一模型来源**：本地 embedding 模式下，索引与查询都使用
   `LOCAL_EMBEDDING_MODEL`；检索器自己不得再选模型。
3. **维度校验**：模型返回的向量长度必须等于 `VECTOR_DIMENSION`（ES mapping
   的 dense_vector dims），不一致时抛 `EmbeddingDimensionMismatchError`，
   消息包含实际/期望维度与模型名——让漂移在写入路径上显式失败，而不是
   让 ES 抛一个难懂 mapping 错误或静默混用。
4. **启动校验**：probe 可达且维度不符 → fail fast；服务不可达 → 显式
   `EmbeddingProbeUnavailableError`（启动层降级为告警，不阻塞开发环境）。
5. **索引模型元数据**：建索引时把 embedding 模型写进 mapping `_meta`；
   存量索引模型与当前配置不一致时抛 `IndexConfigError`（绝不自动删数据）。
   旧索引没有 `_meta` 时只告警（无法证明一致，但不能凭空判定不一致）。

变异验证（先提交后变异，见 `_orchestrate/round2/ragb1_*.log`）：
- 把 `EmbeddingService._ensure_dimensions` 调用改成 no-op → 维度用例必红；
- 把模型解析改回 `settings.EMBEDDING_MODEL`（模拟本地/在线漂移）→ 单一模型用例必红；
- 删掉 `_create_index_if_not_exists` 的 `_meta` 模型比较 → 模型漂移用例必红。
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.core.config import settings


class _FakeEmbeddingClient:
    """最小 OpenAI 兼容 embeddings 客户端替身。"""

    def __init__(self, dims: int = 8, fail_with: Exception | None = None):
        self.dims = dims
        self.fail_with = fail_with
        self.calls: list[dict] = []
        self.embeddings = SimpleNamespace(create=self._create)

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_with is not None:
            raise self.fail_with
        inputs = kwargs.get("input")
        if isinstance(inputs, str):
            inputs = [inputs]
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1] * self.dims) for _ in inputs])


def _run(coro):
    return asyncio.run(coro)


def _make_service(*, dims: int = 8, dimension: int = 8, send_dimensions: bool = False):
    from app.services.embedding_service import EmbeddingConfig, EmbeddingService

    client = _FakeEmbeddingClient(dims=dims)
    config = EmbeddingConfig(
        client=client,
        model="fake-embed-model",
        dimension=dimension,
        source="online",
        send_dimensions=send_dimensions,
    )
    return EmbeddingService(config=config), client


# ── 1. 唯一构造点 / 同一 provider ────────────────────────────────────────


def test_retrieval_has_no_independent_embedding_client():
    """检索期不得再维护第二套 embedding 客户端构造逻辑（#196 根因）。"""
    import app.dependencies as deps

    assert not hasattr(deps, "_build_embedding_client"), (
        "app.dependencies 仍存在独立的 embedding 客户端构造点；"
        "索引与检索必须共用 app.services.embedding_service 的唯一构造点"
    )


def test_pipeline_injects_the_same_embedding_service_as_indexing():
    import app.dependencies as deps
    from app.services.embedding_service import embedding_service

    deps.get_rag_pipeline.cache_clear()
    try:
        pipeline = deps.get_rag_pipeline()
        assert pipeline.retriever.embedding_provider is embedding_service
        assert pipeline.get_embedding.__self__ is pipeline
    finally:
        deps.get_rag_pipeline.cache_clear()


def test_pipeline_falls_back_when_embedding_is_unconfigured(monkeypatch):
    """无 embedding 配置时管线仍可构造（向量臂关闭），不得拖垮 LLM-only 工具。

    基线语义：`_build_embedding_client()` 无 key 返回 None，管线照常构造。
    唯一构造点的单例在 import 期抛 EmbeddingConfigError，`get_rag_pipeline()`
    必须把它降级为 `embedding_provider=None`（否则 `rag_service.openai_client`
    这类只读 LLM 的路径会被无关能力拖崩）。
    """
    import sys
    import types

    import app.dependencies as deps
    from app.rag.errors import EmbeddingConfigError

    class _BrokenEmbeddingModule(types.ModuleType):
        @property
        def embedding_service(self):
            raise EmbeddingConfigError("Embedding API key is missing")

    monkeypatch.setitem(
        sys.modules,
        "app.services.embedding_service",
        _BrokenEmbeddingModule("app.services.embedding_service"),
    )
    deps.get_rag_pipeline.cache_clear()
    try:
        pipeline = deps.get_rag_pipeline()
        assert pipeline.retriever.embedding_provider is None
        assert pipeline.retriever.embedding_client is None
        assert pipeline.retriever.get_embedding is not None
    finally:
        deps.get_rag_pipeline.cache_clear()


def test_retriever_does_not_select_embedding_model_itself():
    from app.rag.retriever import HybridRetriever

    src = inspect.getsource(HybridRetriever._embed)
    assert "EMBEDDING_MODEL" not in src, (
        "检索器不得自行读取 EMBEDDING_MODEL/LOCAL_EMBEDDING_MODEL —— "
        "模型名只能来自唯一解析点（否则本地模式下再次漂移）"
    )


# ── 2. 单一模型来源：本地模式索引与查询同模型 ───────────────────────────


def test_local_embedding_mode_uses_one_model_for_index_and_query(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_LOCAL_EMBEDDING", True)
    monkeypatch.setattr(settings, "LOCAL_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL", "bge-m3")
    monkeypatch.setattr(settings, "VECTOR_DIMENSION", 768)

    from app.services.embedding_service import (
        EmbeddingConfig,
        EmbeddingService,
        resolve_embedding_config,
    )

    resolved = resolve_embedding_config()
    assert resolved.model == "nomic-embed-text", (
        "本地 embedding 模式下应使用 LOCAL_EMBEDDING_MODEL，"
        f"实际解析为 {resolved.model!r}（正是 #196 的模型漂移）"
    )
    assert resolved.source == "local"
    assert resolved.dimension == 768

    client = _FakeEmbeddingClient(dims=768)
    config = EmbeddingConfig(
        client=client,
        model=resolved.model,
        dimension=resolved.dimension,
        source=resolved.source,
    )
    service = EmbeddingService(config=config)

    async def _both_paths():
        await service.get_embeddings(["索引期文本"])
        from app.rag.retriever import HybridRetriever

        retriever = HybridRetriever(embedding_provider=service)
        vector = await retriever._embed("检索期查询")
        assert len(vector) == 768

    _run(_both_paths())
    models = {call["model"] for call in client.calls}
    assert models == {"nomic-embed-text"}, f"索引与检索必须使用同一模型；实际用过的模型：{models}"


def test_local_embedding_mode_does_not_require_online_key(monkeypatch):
    """本地模式不得因为缺在线 key 而关闭向量臂（#196 已报告的部署形态）。"""
    monkeypatch.setattr(settings, "ENABLE_LOCAL_EMBEDDING", True)
    monkeypatch.setattr(settings, "LOCAL_EMBEDDING_MODEL", "nomic-embed-text")
    monkeypatch.setattr(settings, "EMBEDDING_API_KEY", None)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")

    from app.services.embedding_service import resolve_embedding_config

    config = resolve_embedding_config()
    assert config.source == "local"
    assert config.model == "nomic-embed-text"
    assert config.client is not None


def test_lifespan_wires_startup_probe_with_fail_fast():
    """启动层必须真的接线：维度漂移 → raise（拒绝启动），而非只写日志。"""
    import re
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[2] / "app" / "main.py").read_text(encoding="utf-8")
    assert "verify_embedding_consistency()" in main_src
    assert re.search(
        r"except EmbeddingDimensionMismatchError as e:\s*"
        r"logger\.critical\([^)]*\)\s*raise\b",
        main_src,
    ), "lifespan 捕获 EmbeddingDimensionMismatchError 后必须 raise（fail fast）"


def test_auto_tier_does_not_apply_hardware_dims_to_local_embedding(monkeypatch):
    """本地 embedding 模式不得套用在线模型的硬件档维度（#196 升级回归防线）。

    硬件档面向在线模型（bge-m3/1024 等）；本地默认模型 nomic-embed-text 是
    768 维。若硬件档仍覆盖 VECTOR_DIMENSION，新启动校验会拒绝启动本地部署。
    """
    from types import SimpleNamespace

    from app.core import hardware
    from app.core.config.ai import _auto_tier_defaults

    monkeypatch.setattr(
        hardware,
        "detect",
        lambda: SimpleNamespace(embedding_model="bge-m3", vector_dim=1024),
    )
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("VECTOR_DIMENSION", raising=False)

    assert _auto_tier_defaults(explicitly_set=set(), local_embedding=True) == {}
    # 对照：在线模式仍然应用硬件档（原行为不变）。
    assert _auto_tier_defaults(explicitly_set=set(), local_embedding=False) == {
        "EMBEDDING_MODEL": "bge-m3",
        "VECTOR_DIMENSION": 1024,
    }


# ── 3. 维度校验 ─────────────────────────────────────────────────────────


def test_indexing_path_rejects_dimension_drift():
    from app.services.embedding_service import EmbeddingDimensionMismatchError

    service, _client = _make_service(dims=7, dimension=8)
    with pytest.raises(EmbeddingDimensionMismatchError) as excinfo:
        _run(service.get_embeddings(["文本"]))
    message = str(excinfo.value)
    assert "7" in message and "8" in message and "fake-embed-model" in message


def test_query_path_rejects_dimension_drift():
    from app.services.embedding_service import EmbeddingDimensionMismatchError

    service, _client = _make_service(dims=7, dimension=8)
    with pytest.raises(EmbeddingDimensionMismatchError):
        _run(service.embed_query("查询"))


def test_dimensions_parameter_is_opt_in():
    """默认不发送 dimensions：请求截断会掩盖「配置维度已过期」的漂移。"""
    service, client = _make_service(dims=8, dimension=8)
    _run(service.get_embeddings(["x"]))
    assert "dimensions" not in client.calls[0]

    service2, client2 = _make_service(dims=8, dimension=8, send_dimensions=True)
    _run(service2.get_embeddings(["x"]))
    assert client2.calls[0]["dimensions"] == 8


# ── 4. 启动校验 ─────────────────────────────────────────────────────────


def test_startup_probe_fails_fast_on_dimension_drift():
    from app.services.embedding_service import (
        EmbeddingDimensionMismatchError,
        verify_embedding_consistency,
    )

    service, _client = _make_service(dims=7, dimension=8)
    with pytest.raises(EmbeddingDimensionMismatchError):
        _run(verify_embedding_consistency(service=service))


def test_startup_probe_unreachable_is_explicit_unavailable_error():
    from app.services.embedding_service import (
        EmbeddingConfig,
        EmbeddingProbeUnavailableError,
        EmbeddingService,
        verify_embedding_consistency,
    )

    client = _FakeEmbeddingClient(fail_with=ConnectionError("connection refused"))
    service = EmbeddingService(
        config=EmbeddingConfig(client=client, model="m", dimension=8, source="online")
    )
    with pytest.raises(EmbeddingProbeUnavailableError):
        _run(verify_embedding_consistency(service=service))


def test_startup_probe_can_be_disabled(monkeypatch):
    from app.services.embedding_service import (
        EmbeddingConfig,
        EmbeddingService,
        verify_embedding_consistency,
    )

    monkeypatch.setattr(settings, "EMBEDDING_STARTUP_PROBE", False)
    client = _FakeEmbeddingClient(fail_with=ConnectionError("should not be called"))
    service = EmbeddingService(
        config=EmbeddingConfig(client=client, model="m", dimension=8, source="online")
    )
    info = _run(verify_embedding_consistency(service=service))
    assert info["skipped"] is True
    assert client.calls == []


# ── 5. 索引模型元数据与漂移拒绝 ──────────────────────────────────────────


def test_knowledge_index_mapping_records_embedding_model(monkeypatch):
    from app.core import elasticsearch as es_mod

    monkeypatch.setattr(es_mod, "resolve_embedding_model", lambda: "model-A")
    monkeypatch.setattr(settings, "VECTOR_DIMENSION", 1024)

    mapping = es_mod._knowledge_index_mapping()["mappings"]
    assert mapping["_meta"] == {
        "embedding_model": "model-A",
        "vector_dimension": 1024,
    }


class _FakeIndexMappingIndices:
    def __init__(self, mapping: dict):
        self._mapping = mapping
        self.deleted = False

    async def exists(self, index=None):
        return True

    async def get_mapping(self, index=None):
        return self._mapping

    async def delete(self, index=None):
        self.deleted = True


def _mapping_for(index_name: str, *, dims: int, meta: dict | None) -> dict:
    mappings: dict = {"properties": {"embedding": {"dims": dims}}}
    if meta is not None:
        mappings["_meta"] = meta
    return {index_name: {"mappings": mappings}}


def test_index_model_drift_is_refused_without_deleting(monkeypatch):
    from app.core import elasticsearch as es_mod

    monkeypatch.setattr(settings, "VECTOR_DIMENSION", 1024)
    monkeypatch.setattr(es_mod, "resolve_embedding_model", lambda: "new-model")
    client = SimpleNamespace(
        indices=_FakeIndexMappingIndices(
            _mapping_for(
                settings.ELASTICSEARCH_INDEX_NAME,
                dims=1024,
                meta={"embedding_model": "old-model", "vector_dimension": 1024},
            )
        )
    )

    with pytest.raises(es_mod.IndexConfigError) as excinfo:
        _run(es_mod._create_index_if_not_exists(client))
    message = str(excinfo.value)
    assert "old-model" in message and "new-model" in message
    assert client.indices.deleted is False, "模型漂移时绝不能自动删除索引"


def test_legacy_index_without_model_meta_warns_but_does_not_block(monkeypatch, caplog):
    from app.core import elasticsearch as es_mod

    monkeypatch.setattr(settings, "VECTOR_DIMENSION", 1024)
    monkeypatch.setattr(es_mod, "resolve_embedding_model", lambda: "current-model")
    client = SimpleNamespace(
        indices=_FakeIndexMappingIndices(
            _mapping_for(settings.ELASTICSEARCH_INDEX_NAME, dims=1024, meta=None)
        )
    )

    with caplog.at_level("WARNING", logger=es_mod.logger.name):
        _run(es_mod._create_index_if_not_exists(client))  # 不抛错

    assert any("embedding" in record.getMessage().lower() for record in caplog.records), (
        "旧索引缺少模型元数据时必须显式告警（无法证明一致）"
    )


# ── 6. 索引配置错误时查询侧拒绝检索（漂移不得静默混用）──────────────────


@pytest.mark.asyncio
async def test_search_refuses_when_index_config_is_invalid(monkeypatch):
    """写入侧被 IndexConfigError 拦下后，查询侧不得继续用新模型撞旧索引。"""
    from app.rag.errors import RetrievalUnavailableError
    from app.rag.pipeline import RAGPipeline

    monkeypatch.setattr(
        "app.core.elasticsearch.get_index_error",
        lambda: "embedding 模型不匹配：索引 old-model，当前 new-model",
    )
    pipeline = RAGPipeline()
    with pytest.raises(RetrievalUnavailableError) as excinfo:
        await pipeline.search_knowledge_base("任意查询", organization_id=1)
    assert excinfo.value.kind == "failed"
    assert "old-model" in str(excinfo.value)


def test_index_error_latch_clears_after_successful_validation(monkeypatch):
    """闩锁必须反映最近一次校验结果：瞬时错误恢复后查询 gate 自动解除。"""
    from app.core import elasticsearch as es_mod

    monkeypatch.setattr(settings, "VECTOR_DIMENSION", 1024)
    monkeypatch.setattr(es_mod, "resolve_embedding_model", lambda: "same-model")
    monkeypatch.setattr(es_mod._holder, "index_error", "startup transient failure")
    client = SimpleNamespace(
        indices=_FakeIndexMappingIndices(
            _mapping_for(
                settings.ELASTICSEARCH_INDEX_NAME,
                dims=1024,
                meta={"embedding_model": "same-model", "vector_dimension": 1024},
            )
        )
    )

    _run(es_mod._create_index_if_not_exists(client))

    assert es_mod._holder.index_error is None


@pytest.mark.asyncio
async def test_write_path_drift_sets_query_gate_latch(monkeypatch):
    """启动后发生的漂移（写路径 IndexConfigError）必须让查询侧 gate 生效。"""
    from app.core import elasticsearch as es_mod

    monkeypatch.setattr(es_mod._holder, "index_error", None)

    async def _fake_get_es():
        return object()

    async def _boom(client):
        raise es_mod.IndexConfigError("embedding 模型不匹配：索引 old-model")

    monkeypatch.setattr(es_mod, "get_elasticsearch", _fake_get_es)
    monkeypatch.setattr(es_mod, "_create_index_if_not_exists", _boom)

    with pytest.raises(es_mod.IndexConfigError):
        await es_mod.create_index_if_not_exists()
    assert es_mod._holder.index_error
    assert "old-model" in es_mod._holder.index_error


@pytest.mark.asyncio
async def test_search_gate_runs_before_cache(monkeypatch):
    """fail-closed：闩锁置位时暖缓存也不得返回（可能来自旧模型索引）。"""
    from unittest.mock import AsyncMock

    from app.rag.errors import RetrievalUnavailableError
    from app.rag.pipeline import RAGPipeline

    monkeypatch.setattr("app.core.elasticsearch.get_index_error", lambda: "index broken")
    pipeline = RAGPipeline()
    cache_get = AsyncMock(return_value=(["stale-result"], None))
    monkeypatch.setattr(pipeline.cache, "get", cache_get)

    with pytest.raises(RetrievalUnavailableError):
        await pipeline.search_knowledge_base("任意查询", organization_id=1)
    cache_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_vector_arm_marks_dimension_drift_as_config_error():
    """查询期维度漂移必须标记为配置错误，而不是普通「服务故障」降级。"""
    from app.rag.retriever import HybridRetriever
    from app.services.embedding_service import EmbeddingDimensionMismatchError

    class _DriftingProvider:
        async def embed_query(self, _text):
            raise EmbeddingDimensionMismatchError("Embedding 维度不一致：7 vs 8")

    retriever = HybridRetriever(embedding_provider=_DriftingProvider())
    hits, query_vector, reason = await retriever._vector_hits("查询", [], 10)

    assert hits == [] and query_vector == []
    assert reason is not None and reason.startswith("vector_arm_config_error")
    assert "维度不一致" in reason


@pytest.mark.asyncio
async def test_pipeline_refuses_results_when_vector_config_error(monkeypatch):
    """即使关键词臂有命中，embedding 配置错误也不得返回「看似正常」的结果。"""
    from app.rag.errors import RetrievalUnavailableError
    from app.rag.pipeline import RAGPipeline

    pipeline = RAGPipeline()

    async def fake_retrieve(*args, **kwargs):
        return (
            [{"chunk_id": "c1", "content": "x", "score": 1.0, "filename": "a.txt"}],
            None,
            None,
            ["vector_arm_config_error: Embedding 维度不一致：7 vs 8"],
        )

    monkeypatch.setattr(pipeline.retriever, "retrieve", fake_retrieve)

    with pytest.raises(RetrievalUnavailableError) as excinfo:
        await pipeline.search_knowledge_base("查询", organization_id=1, document_ids=["d1"])
    assert "维度不一致" in str(excinfo.value)


class _RaceIndexIndices:
    """模拟多副本首启竞态：exists=False，create 报 resource_already_exists。"""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    async def exists(self, index=None):
        return False

    async def create(self, index=None, mappings=None, settings=None):
        raise RuntimeError("BadRequestError(400, 'resource_already_exists_exception')")

    async def get_mapping(self, index=None):
        return self._mapping


def test_resource_already_exists_race_falls_through_to_validation(monkeypatch):
    """并发建索引竞态不是配置错误：改为校验已存在索引并解除闩锁。"""
    from app.core import elasticsearch as es_mod

    monkeypatch.setattr(settings, "VECTOR_DIMENSION", 1024)
    monkeypatch.setattr(es_mod, "resolve_embedding_model", lambda: "same-model")
    monkeypatch.setattr(es_mod._holder, "index_error", "transient startup error")
    client = SimpleNamespace(
        indices=_RaceIndexIndices(
            _mapping_for(
                settings.ELASTICSEARCH_INDEX_NAME,
                dims=1024,
                meta={"embedding_model": "same-model", "vector_dimension": 1024},
            )
        )
    )

    _run(es_mod._create_index_if_not_exists(client))

    assert es_mod._holder.index_error is None


def test_resource_already_exists_race_still_rejects_incompatible_index(monkeypatch):
    """竞态兜底不得掩盖真正的维度漂移。"""
    from app.core import elasticsearch as es_mod

    monkeypatch.setattr(settings, "VECTOR_DIMENSION", 1024)
    client = SimpleNamespace(
        indices=_RaceIndexIndices(
            _mapping_for(settings.ELASTICSEARCH_INDEX_NAME, dims=2048, meta=None)
        )
    )

    with pytest.raises(es_mod.IndexConfigError):
        _run(es_mod._create_index_if_not_exists(client))
