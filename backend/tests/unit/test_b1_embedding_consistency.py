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
- 把 `EmbeddingService._validate_dimensions` 改成 no-op → 维度用例必红；
- 把检索器改回 `settings.EMBEDDING_MODEL` → 单一模型用例必红；
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
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=[0.1] * self.dims) for _ in inputs]
        )


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
    assert models == {"nomic-embed-text"}, (
        f"索引与检索必须使用同一模型；实际用过的模型：{models}"
    )


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
        config=EmbeddingConfig(
            client=client, model="m", dimension=8, source="online"
        )
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
        config=EmbeddingConfig(
            client=client, model="m", dimension=8, source="online"
        )
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

    assert any(
        "embedding" in record.getMessage().lower() for record in caplog.records
    ), "旧索引缺少模型元数据时必须显式告警（无法证明一致）"
