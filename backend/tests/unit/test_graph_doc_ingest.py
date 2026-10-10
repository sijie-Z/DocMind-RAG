"""GraphRAG 单文档摄入 API（幂等标记 / 超时切断 / Redis 降级）单元测试。

背景：图谱写侧此前只有 `build_graph_from_entities`（唯一调用者=单测），
worker 索引成功后不做任何抽取，图数据源恒空。本文件钉住新增的
`GraphRAGService.ingest_document` 的契约：

- **幂等**：同一文档重复摄入 → 第二次 skipped，occurrences/relationships 不叠加
  （变异点：删掉 `ingest_document` 里的 is_document_extracted 跳过分支 → T1 红）；
- **标记失效自愈**：标记在、图键失踪（/graph/clear 或 TTL 到期）→ 视为未摄入，可重建
  （变异点：`is_document_extracted` 只看 sismember 不看图键 → T2 红）；
- **clear 同步清标记**：否则清空图谱后回填脚本会被旧标记永久挡住
  （变异点：`clear()` 不删 docs 标记键 → T6 红）；
- **超时切断**：LLM 挂起时 wait_for 超时向上抛（不静默回退规则抽取），且不写标记
  （变异点：去掉 asyncio.wait_for → T4 挂死/红）；
- **Redis 不可用降级**：判不了 → None；摄入照常返回 extracted、不崩溃（与既有
  容错风格一致）。
"""
import asyncio
import json

import pytest

from app.services.graph_rag_service import GraphRAGService


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.expires: list[tuple[str, int]] = []

    def __bool__(self):
        return True

    async def get(self, key):
        return self.data.get(key)

    async def setex(self, key, ttl, value):
        self.data[key] = value

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)
        return 1

    async def sismember(self, key, member):
        return member in self.sets.get(key, set())

    async def expire(self, key, ttl):
        self.expires.append((key, ttl))
        return True

    async def delete(self, *keys):
        for k in keys:
            self.data.pop(k, None)
            self.sets.pop(k, None)


class _NoRedis:
    def __bool__(self):
        return False


ENTITIES_A = [
    {
        "entity": "DocMind",
        "type": "PRODUCT",
        "description": "企业级 RAG 系统",
        "relations": [{"target": "FastAPI", "relation": "PART_OF"}],
    }
]


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)
    return fake


ENTITY_JSON = (
    '[{"entity": "DocMind", "type": "PRODUCT", "description": "企业级 RAG 系统", '
    '"relations": [{"target": "FastAPI", "relation": "PART_OF"}]}]'
)


def _stub_extract(monkeypatch, service, entities):
    """替换实例的抽取方法：本文件测的是写侧（标记/合并/幂等），不测提示词。"""
    async def fake(text, llm_client=None):
        return list(entities)

    monkeypatch.setattr(service, "extract_entities_with_llm", fake, raising=False)


async def test_ingest_is_idempotent(monkeypatch, fake_redis):
    """同一文档两次摄入：第二次 skipped，不叠加 occurrences/关系条目。"""
    service = GraphRAGService()
    _stub_extract(monkeypatch, service, ENTITIES_A)

    r1 = await service.ingest_document(
        document_id="doc-1", organization_id=1, text="x" * 60, llm_client=None
    )
    assert r1 == {"status": "extracted", "entities": 1}

    r2 = await service.ingest_document(
        document_id="doc-1", organization_id=1, text="x" * 60, llm_client=None
    )
    assert r2 == {"status": "skipped", "entities": 0}

    raw = json.loads(fake_redis.data["rag:graph:1"])
    key = service._normalize_entity("DocMind")
    assert raw[key]["occurrences"] == 1, "重复摄入不得叠加计数"
    assert len(raw[key]["relationships"]) == 1, "重复摄入不得叠加关系条目"
    assert "doc-1" in fake_redis.sets["rag:graph:docs:1"]


async def test_marker_without_graph_is_treated_as_not_ingested(monkeypatch, fake_redis):
    """标记在、图键失踪（clear/TTL）→ 视为未摄入：新进程可整体重建。"""
    service = GraphRAGService()
    _stub_extract(monkeypatch, service, ENTITIES_A)
    await service.ingest_document(
        document_id="doc-1", organization_id=1, text="x" * 60, llm_client=None
    )
    # 模拟图键过期/被清：仅保留文档标记
    fake_redis.data.pop("rag:graph:1")

    fresh = GraphRAGService()
    _stub_extract(monkeypatch, fresh, ENTITIES_A)
    assert await fresh.is_document_extracted("doc-1", 1) is False

    r = await fresh.ingest_document(
        document_id="doc-1", organization_id=1, text="x" * 60, llm_client=None
    )
    assert r == {"status": "extracted", "entities": 1}
    raw = json.loads(fake_redis.data["rag:graph:1"])
    assert raw[fresh._normalize_entity("DocMind")]["occurrences"] == 1


async def test_clear_wipes_doc_markers(monkeypatch, fake_redis):
    """clear() 必须同步清掉幂等标记，否则回填重建被旧标记永久挡住。"""
    service = GraphRAGService()
    _stub_extract(monkeypatch, service, ENTITIES_A)
    await service.ingest_document(
        document_id="doc-1", organization_id=1, text="x" * 60, llm_client=None
    )
    assert "doc-1" in fake_redis.sets["rag:graph:docs:1"]

    await service.clear(1)

    assert await service.is_document_extracted("doc-1", 1) is False
    assert fake_redis.sets.get("rag:graph:docs:1", set()) == set()


async def test_llm_hang_is_cut_by_timeout_and_no_marker_written(fake_redis):
    """LLM 挂起：wait_for 超时向上抛 TimeoutError，不静默回退、不写标记。"""

    class _HangCompletions:
        async def create(self, **kwargs):
            await asyncio.sleep(30)

    class _HangChat:
        completions = _HangCompletions()

    class _HangClient:
        chat = _HangChat()

    service = GraphRAGService()
    with pytest.raises(TimeoutError):
        await service.ingest_document(
            document_id="doc-hang",
            organization_id=1,
            text="这是一段足够长的文本（≥50 字符）用于触发 LLM 分支：" + "内容" * 40,
            llm_client=_HangClient(),
            timeout=0.05,
        )

    assert fake_redis.sets.get("rag:graph:docs:1", set()) == set(), "超时不得写标记"
    assert "rag:graph:1" not in fake_redis.data


async def test_short_text_is_empty_but_marked(fake_redis):
    """空/过短文本：零实体、走空结果，不调 LLM；仍写入标记避免反复重试。"""
    service = GraphRAGService()
    r = await service.ingest_document(
        document_id="doc-empty", organization_id=1, text="太短", llm_client=None
    )
    assert r == {"status": "extracted", "entities": 0}
    assert "doc-empty" in fake_redis.sets["rag:graph:docs:1"]

    r2 = await service.ingest_document(
        document_id="doc-empty", organization_id=1, text="太短", llm_client=None
    )
    assert r2 == {"status": "skipped", "entities": 0}, "零实体文档也不得反复重抽"


async def test_redis_unavailable_degrades_without_crash(monkeypatch):
    """Redis 不可用：判不了（None），摄入仍返回 extracted，不抛异常。"""
    monkeypatch.setattr("app.core.redis.redis_client", _NoRedis())
    service = GraphRAGService()
    _stub_extract(monkeypatch, service, ENTITIES_A)

    assert await service.is_document_extracted("doc-1", 1) is None
    r = await service.ingest_document(
        document_id="doc-1", organization_id=1, text="x" * 60, llm_client=None
    )
    assert r == {"status": "extracted", "entities": 1}


async def test_ingest_uses_llm_client_with_configured_model(fake_redis):
    """传了 llm_client 时必须真走 LLM 分支，且模型名取真实配置。"""
    from types import SimpleNamespace

    from app.core.config import settings

    calls: list[dict] = []

    class _Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=ENTITY_JSON))]
            )

    class _Client:
        chat = SimpleNamespace(completions=_Completions())

    service = GraphRAGService()
    r = await service.ingest_document(
        document_id="doc-llm",
        organization_id=1,
        text="这是一段长度足够的文本，用于验证 LLM 分支真正被调用。" * 3,
        llm_client=_Client(),
    )

    assert r == {"status": "extracted", "entities": 1}
    assert len(calls) == 1
    assert calls[0]["model"] == settings.DEEPSEEK_MODEL
    raw = json.loads(fake_redis.data["rag:graph:1"])
    assert raw[service._normalize_entity("DocMind")]["entity_name"] == "DocMind"
