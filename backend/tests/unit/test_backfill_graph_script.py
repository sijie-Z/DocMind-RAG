"""回填脚本 `scripts/backfill_graph.py` 的幂等性与边界测试。

钉住：

- **幂等**：同一批文档连续回填两次（第二次换新进程级 service 实例模拟重启），
  Redis 里的图谱快照（含 occurrences/relationships）完全一致——不叠加；
  （变异点：去掉 `ingest_document` 的跳过分支 → 第二次跑后快照不等 → 红）
- **空文档边界**：零/过短文本的已索引文档不崩、计入 empty、写标记避免反复重排；
- **dry-run**：只列计划（已摄入=skipped，待摄入=planned），不写任何数据。
"""

import importlib.util
import json
from pathlib import Path

import pytest

from app.services.graph_rag_service import GraphRAGService

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "backfill_graph.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("backfill_graph", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


TEXTS = {
    "doc-a": "DOC-A 段落：DocMind 系统由 Planner、Executor、Reflector 三个核心模块组成，"
    "通过工具注册表协同工作。" * 3,
    "doc-b": "DOC-B 段落：检索层使用 Elasticsearch 与向量混合召回，并以 RRF 融合排序。" * 3,
    "doc-empty": "",
}


def _entities_for(text: str):
    if "DOC-A" in text:
        return [
            {
                "entity": "DocMind",
                "type": "PRODUCT",
                "description": "AI Agent 系统",
                "relations": [{"target": "Planner", "relation": "PART_OF"}],
            }
        ]
    return [
        {
            "entity": "Elasticsearch",
            "type": "TECHNOLOGY",
            "description": "检索引擎",
            "relations": [],
        }
    ]


async def _stub_extract(service, monkeypatch):
    async def fake(text, llm_client=None):
        return _entities_for(text)

    monkeypatch.setattr(service, "extract_entities_with_llm", fake)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)
    return fake


def _documents():
    return [("doc-a", 7), ("doc-b", 7)]


async def _text_loader(doc_id):
    return TEXTS[doc_id]


@pytest.mark.asyncio
async def test_backfill_twice_is_idempotent(monkeypatch, fake_redis):
    mod = _load_script_module()
    service = GraphRAGService()
    await _stub_extract(service, monkeypatch)

    r1 = await mod.backfill(_documents(), _text_loader, service=service)
    assert sorted(r1["extracted"]) == ["doc-a", "doc-b"]
    assert r1["failed"] == []
    snapshot_1 = json.dumps(fake_redis.data, sort_keys=True)

    # 第二个进程（新 service 实例）再跑一遍：全部跳过，快照分毫不变
    fresh = GraphRAGService()
    await _stub_extract(fresh, monkeypatch)
    r2 = await mod.backfill(_documents(), _text_loader, service=fresh)

    assert sorted(r2["skipped"]) == ["doc-a", "doc-b"]
    assert r2["extracted"] == [] and r2["failed"] == []
    snapshot_2 = json.dumps(fake_redis.data, sort_keys=True)
    assert snapshot_2 == snapshot_1, "重复回填不得叠加任何实体/关系/计数"


@pytest.mark.asyncio
async def test_backfill_empty_document_boundary(monkeypatch, fake_redis):
    mod = _load_script_module()
    service = GraphRAGService()
    # 不 stub 抽取：空文本走真实短路（<50 字符零实体、不触网）
    docs = [("doc-empty", 7)]

    r1 = await mod.backfill(docs, _text_loader, service=service)
    assert r1["empty"] == ["doc-empty"]
    assert r1["failed"] == []
    assert "doc-empty" in fake_redis.sets["rag:graph:docs:7"]

    fresh = GraphRAGService()
    r2 = await mod.backfill(docs, _text_loader, service=fresh)
    assert r2["skipped"] == ["doc-empty"], "空文档不得每轮反复重排"


@pytest.mark.asyncio
async def test_backfill_dry_run_lists_plan_without_writing(monkeypatch, fake_redis):
    mod = _load_script_module()
    service = GraphRAGService()
    await _stub_extract(service, monkeypatch)

    r0 = await mod.backfill(_documents(), _text_loader, service=service, dry_run=True)
    assert sorted(r0["planned"]) == ["doc-a", "doc-b"]
    assert fake_redis.data == {} and fake_redis.sets == {}, "dry-run 不得写任何数据"

    await mod.backfill(_documents(), _text_loader, service=service)
    r1 = await mod.backfill(_documents(), _text_loader, service=service, dry_run=True)
    assert sorted(r1["skipped"]) == ["doc-a", "doc-b"] and r1["planned"] == []


@pytest.mark.asyncio
async def test_backfill_surfaces_per_document_failure(monkeypatch, fake_redis):
    """单文档失败不中断整批：进 failed 列表，其余照常。"""
    mod = _load_script_module()
    service = GraphRAGService()

    async def flaky(text, llm_client=None):
        if "DOC-B" in text:
            raise RuntimeError("boom")
        return _entities_for(text)

    monkeypatch.setattr(service, "extract_entities_with_llm", flaky)

    r = await mod.backfill(_documents(), _text_loader, service=service)
    assert r["extracted"] == ["doc-a"]
    assert [f[0] for f in r["failed"]] == ["doc-b"]


def test_script_arg_parser_accepts_core_flags():
    mod = _load_script_module()
    parser = mod.build_arg_parser()
    args = parser.parse_args(["--org", "7", "--document-id", "doc-a", "--dry-run"])
    assert args.org == 7
    assert args.document_id == "doc-a"
    assert args.dry_run is True
