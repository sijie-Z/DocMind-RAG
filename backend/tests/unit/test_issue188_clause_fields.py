"""#188 判别测试：section_title/clause_no 三重断链合拢（抽取 → 索引 → 查询侧）。

修前断链（分诊报告 #188，origin/develop 实测）：
1. 切分器抽了 section_title，但 `doc_processor` 写 ES 的 `_source` **没有顶层**
   `section_title` / `chunk_index` / `clause_no` —— mapping 字段是死的；
2. `metadata.enabled=false`，存而不索引；
3. 检索 `_source` 白名单不含这些字段，`retriever` 全文无 section_title，
   查询侧也没有条号 filter/boost。

本 PR 的修法（分诊推荐）：
- 索引侧：`doc_processor` 写顶层 `section_title` / `chunk_index` / `chunk_id` /
  `clause_no` / `section_no`；条号抽取**复用** `evals/clauses.py` 的定义位口径
  （经 `app/services/clause_refs.py` 单一入口，不写第三套解析）；
- 查询侧：`第X条` / `§X.Y` 查询额外发一条结构化精确检索腿（term/terms 于
  `clause_no`/`section_no`），命中按「定义目标条号的块优先」进入 keyword 臂序；
  旧索引（无这些字段）上该腿无命中、行为与修前一致（平滑迁移）。

变异点（红→绿判别）：
- `_build_es_source` 不写顶层字段 / 用 chunk_text（含摘要前缀）而非
  original_text 抽条号 → `TestDocumentProcessorWritesTopLevelFields` 红；
- mapping 缺 `clause_no`/`section_no` → `TestIndexMapping` 红；
- `_keyword_hits` 不发精确腿 / 不发或把 `terms` 用错字段 / 不把定义目标条号的
  命中排前 → `TestKeywordArmClauseWiring` 红；
- `_post_process` 不把结构化字段透传给下游 → `test_post_process_carries_structured_fields` 红；
- `clause_refs` 自写解析（不委托 evals.clauses）→ `test_extraction_delegates_to_evals_clauses` 红；
- golden set 任一 gold 的条号无法从对应语料 chunk 抽出 → `TestGoldenSetClauseCoverage` 红。
"""

import pathlib
from types import SimpleNamespace

import pytest

import app.rag.retriever as retriever_mod
import app.worker.doc_processor as doc_processor_mod
from app.core.elasticsearch import _knowledge_index_mapping
from app.rag.retriever import HybridRetriever
from app.worker.doc_processor import DocumentProcessor

TERM_QUERY = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"


def _clause_refs():
    """延迟 import：红阶段该模块尚不存在，逐个用例按失败计（而非收集期报错）。"""
    from app.services import clause_refs

    return clause_refs


# ══════════════════════════════════════════════════════════════════════════
# 1 · 索引侧抽取：单一口径（evals/clauses.py），定义位判定
# ══════════════════════════════════════════════════════════════════════════


class TestClauseFieldExtraction:
    def test_definition_sites_extracted_and_normalized(self):
        text = (
            "第一条 总则\n"
            "为规范数据安全管理，制定本制度。\n"
            "第3条 数据分级\n"
            "数据分为一般、重要和核心三级。\n"
            "## 2.3 告警响应时限\n"
            "P2 告警须在 30 分钟内响应。\n"
        )
        fields = _clause_refs().extract_clause_fields(text)
        assert fields["clause_no"] == [1, 3], (
            f"中文/阿拉伯条号必须归一化为整数定义位，实际 {fields['clause_no']}"
        )
        assert fields["section_no"] == ["2.3"]

    def test_body_mentions_not_counted(self):
        """正文引用（非行首定义位）不计入——与 golden 的 ClauseHit 判定同口径。"""
        text = "本制度依据第7条制定。\n正文引用第12条与 §2.3 的规则。"
        fields = _clause_refs().extract_clause_fields(text)
        assert fields["clause_no"] == []
        assert fields["section_no"] == []

    def test_extraction_delegates_to_evals_clauses(self):
        """单一来源锁：app 层的抽取必须就是 evals.clauses 的那一个函数。"""
        from evals.clauses import clause_definitions

        assert _clause_refs().clause_definitions is clause_definitions, (
            "索引侧条号抽取不得自写第三套解析（分诊横向建议第 3 条 / #188）"
        )


# ══════════════════════════════════════════════════════════════════════════
# 2 · 索引侧落库：mapping + doc_processor 的 ES _source
# ══════════════════════════════════════════════════════════════════════════


class TestIndexMapping:
    def test_mapping_has_top_level_structured_fields(self):
        props = _knowledge_index_mapping()["mappings"]["properties"]
        assert props["section_title"]["type"] == "text"
        assert props["chunk_index"]["type"] == "integer"
        assert props["clause_no"]["type"] == "integer", (
            "clause_no 必须是顶层字段（整型，chunk 可定义多条 → 数组）"
        )
        assert props["section_no"]["type"] == "keyword"


class TestDocumentProcessorWritesTopLevelFields:
    @staticmethod
    def _doc():
        return SimpleNamespace(
            id="doc-1",
            organization_id=1,
            filename="policy.md",
            file_type="md",
            file_size=123,
            created_at=None,
            uploaded_by=7,
        )

    @staticmethod
    def _chunk():
        return {
            # chunk_text 带上下文增强前缀，且前缀行本身以「第9条」开头——
            # 若实现误用 chunk_text 抽取，clause_no 会多出 9（本用例精确变红）；
            # 正确实现从 original_text 抽取，只得到 [3]。
            "chunk_text": "第9条 文档背景摘要（系统自动生成）\n\n第3条 数据分级\n数据分为三级。",
            "original_text": "第3条 数据分级\n数据分为三级。",
            "chunk_index": 2,
            "chunk_length": 40,
            "metadata": {"section_title": "第3条 数据分级"},
        }

    def test_build_es_source_carries_structured_fields(self):
        source = DocumentProcessor._build_es_source(self._doc(), "cid-1", 2, self._chunk())
        assert source["section_title"] == "第3条 数据分级"
        assert source["chunk_index"] == 2
        assert source["chunk_id"] == "cid-1"
        assert source["clause_no"] == [3], (
            "条号必须从 original_text 抽取（chunk_text 的摘要前缀不得污染定义位；"
            "误用 chunk_text 会得到 [3, 9]）"
        )
        assert source["section_no"] == []
        # 旧契约字段仍在
        assert source["content"] == self._chunk()["chunk_text"]
        assert source["document_id"] == "doc-1"
        assert source["metadata"]["section_title"] == "第3条 数据分级"

    @pytest.mark.asyncio
    async def test_replace_document_state_writes_fields_to_es(self, monkeypatch):
        captured: dict = {}

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def execute(self, *args, **kwargs):
                return None

            def add_all(self, rows):
                captured["rows"] = list(rows)

            async def commit(self):
                return None

        class _FakeES:
            async def delete_by_query(self, **kwargs):
                return {"deleted": 0, "failures": []}

            async def count(self, **kwargs):
                return {"count": 0}

        async def fake_create_index():
            return None

        async def fake_get_es():
            return _FakeES()

        async def fake_bulk(actions):
            captured["actions"] = list(actions)

        async def fake_verify(*args, **kwargs):
            return None

        async def fake_clear_for_org(self, org_id):
            return None

        from app.rag import cache as cache_mod

        monkeypatch.setattr(doc_processor_mod, "AsyncSessionLocal", lambda: _FakeSession())
        monkeypatch.setattr(doc_processor_mod, "create_index_if_not_exists", fake_create_index)
        monkeypatch.setattr(doc_processor_mod, "get_elasticsearch", fake_get_es)
        monkeypatch.setattr(
            doc_processor_mod.ElasticsearchTools,
            "bulk_index_documents",
            staticmethod(fake_bulk),
        )
        monkeypatch.setattr(doc_processor_mod, "_verify_indexed", fake_verify)
        monkeypatch.setattr(cache_mod.RetrievalCache, "clear_for_org", fake_clear_for_org)
        monkeypatch.setattr(cache_mod.SemanticCache, "clear_for_org", fake_clear_for_org)

        processor = DocumentProcessor()
        await processor._replace_document_state(self._doc(), [self._chunk()], [[0.1, 0.2]])

        source = captured["actions"][0]["_source"]
        assert source["section_title"] == "第3条 数据分级"
        assert source["chunk_index"] == 2
        assert source["clause_no"] == [3]
        assert source["section_no"] == []
        assert source["chunk_id"], "顶层 chunk_id 必须写入（mapping 已就绪的死字段复活）"


# ══════════════════════════════════════════════════════════════════════════
# 3 · 查询侧接线：结构化精确腿 + 排序提升 + 字段透传
# ══════════════════════════════════════════════════════════════════════════


def _is_exact_clause_arm(es_query: dict) -> bool:
    """精确腿形状：bool.filter 中含 clause_no/section_no 的 bool.should，无 multi_match。"""
    body = es_query.get("query", {}).get("bool", {})
    if "must" in body:
        return False
    return any(isinstance(clause, dict) and "bool" in clause for clause in body.get("filter", []))


class TestKeywordArmClauseWiring:
    @pytest.mark.asyncio
    async def test_clause_query_issues_exact_structured_arm(self, monkeypatch):
        captured: list[dict] = []

        async def fake_search(es_query):
            captured.append(es_query)
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits("第12条对数据删除有什么要求？", [], candidate_size=10)

        exact = [q for q in captured if _is_exact_clause_arm(q)]
        assert exact, "条号直查查询必须额外发一条结构化 clause_no 精确检索腿"
        assert exact[0]["size"] <= 20, (
            "精确腿返回条数必须有界（filter-only 命中分数为 0，不能把 BM25 内容词候选挤出窗口）"
        )
        should = exact[0]["query"]["bool"]["filter"][0]["bool"]["should"]
        assert {"terms": {"clause_no": [12]}} in should, (
            f"精确腿必须按 clause_no 精确检索（中文/阿拉伯数形已归一化），实际 {should}"
        )
        assert exact[0]["query"]["bool"]["filter"][0]["bool"]["minimum_should_match"] == 1

    @pytest.mark.asyncio
    async def test_section_ref_query_issues_section_arm(self, monkeypatch):
        captured: list[dict] = []

        async def fake_search(es_query):
            captured.append(es_query)
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits("§2.3 告警响应时限是怎么规定的？", [], candidate_size=10)

        exact = [q for q in captured if _is_exact_clause_arm(q)]
        assert exact, "§X.Y 查询同样必须发结构化精确腿"
        should = exact[0]["query"]["bool"]["filter"][0]["bool"]["should"]
        assert {"terms": {"section_no": ["2.3"]}} in should

    @pytest.mark.asyncio
    async def test_huge_clause_number_skips_exact_arm(self, monkeypatch):
        """超界条号不得进入 integer terms（会 400 拖垮整次检索）。"""
        captured: list[dict] = []

        async def fake_search(es_query):
            captured.append(es_query)
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits("第99999999999条规定了什么？", [], candidate_size=10)

        assert captured and not any(_is_exact_clause_arm(q) for q in captured), (
            "超出 clause_no 上界的条号必须跳过结构化精确腿（BM25 臂照常执行）"
        )

    @pytest.mark.asyncio
    async def test_semantic_query_has_no_exact_arm(self, monkeypatch):
        captured: list[dict] = []

        async def fake_search(es_query):
            captured.append(es_query)
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits("审计日志保留多久？", [], candidate_size=10)

        assert captured and not any(_is_exact_clause_arm(q) for q in captured), (
            "普通语义查询不得新增结构化检索腿（行为逐字不变）"
        )

    @pytest.mark.asyncio
    async def test_source_whitelist_includes_structured_fields(self, monkeypatch):
        captured: list[dict] = []

        async def fake_search(es_query):
            captured.append(es_query)
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits("第3条规定了什么？", [], candidate_size=10)

        required = {"section_title", "clause_no", "section_no", "chunk_index"}
        for q in captured:
            fields = set(q.get("_source") or [])
            assert required <= fields, f"检索 _source 白名单缺字段 {sorted(required - fields)}"

    @pytest.mark.asyncio
    async def test_exact_clause_hits_rank_first_in_keyword_arm(self, monkeypatch):
        """gold 块（clause_no 命中）即使在 BM25 里低分，也必须排到 keyword 臂最前。"""
        gold = {
            "_id": "doc01_c11",
            "_score": 1.0,
            "_source": {
                "content": "第12条 数据删除申请需经数据负责人审批。",
                "filename": "01.md",
                "document_id": "doc01",
                "clause_no": [12],
                "section_no": [],
                "section_title": "第12条 数据删除",
            },
        }
        noise = {
            "_id": "doc01_c00",
            "_score": 99.0,
            "_source": {
                "content": "前言：本文件说明知识库服务管理办法。",
                "filename": "01.md",
                "document_id": "doc01",
                "clause_no": [],
                "section_no": [],
            },
        }

        async def fake_search(es_query):
            if _is_exact_clause_arm(es_query):
                return {"hits": {"hits": [gold]}}
            return {"hits": {"hits": [noise]}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)

        retriever = HybridRetriever(openai_client=None)
        hits = await retriever._keyword_hits("第12条对数据删除有什么要求？", [], candidate_size=10)
        assert hits and hits[0]["_id"] == "doc01_c11", (
            "定义目标条号的块必须排在 keyword 臂首位（结构化 boost 不依赖 BM25 分数量纲）"
        )
        assert {h["_id"] for h in hits} == {"doc01_c11", "doc01_c00"}, (
            "精确腿是并集去重，不硬过滤候选"
        )

    def test_bare_clause_query_keeps_exact_defining_chunk(self):
        """裸「第5条」：查询术语为空（CJK 单字不成 bigram）、overlap 恒 0——
        精确定义该条的块不得被词面质量闸门误杀（否则精确腿形同虚设）。"""
        gold = {
            "_id": "c5",
            "_score": 0.0,
            "_source": {
                "content": "第5条 数据分级。",
                "filename": "p.md",
                "document_id": "d1",
                "clause_no": [5],
                "section_no": [],
            },
            "has_keyword": True,
            "keyword_rank": 1,
        }
        noise = {
            "_id": "c9",
            "_score": 0.9,
            "_source": {
                "content": "无关内容",
                "filename": "p.md",
                "document_id": "d1",
                "clause_no": [9],
                "section_no": [],
            },
            "has_keyword": True,
            "keyword_rank": 2,
        }
        docs = HybridRetriever()._post_process("第5条", [gold, noise], top_k=5)
        assert [d["chunk_id"] for d in docs] == ["c5"], (
            "定义目标条号的块必须存活；非目标块仍按质量闸门剔除"
        )

    def test_post_process_carries_structured_fields(self):
        hit = {
            "_id": "doc01_c11",
            "_score": 0.5,
            "_source": {
                "content": "第3条 数据分级，规定数据分为三级。",
                "filename": "01.md",
                "document_id": "doc01",
                "section_title": "第3条 数据分级",
                "clause_no": [3],
                "section_no": [],
                "chunk_index": 2,
            },
            # 臂标记：本用例只验证字段透传，不重复测质量闸门
            "has_keyword": True,
            "keyword_rank": 1,
        }
        docs = HybridRetriever()._post_process("第3条规定了什么？", [hit], top_k=1)
        assert docs[0]["section_title"] == "第3条 数据分级"
        assert docs[0]["clause_no"] == [3]
        assert docs[0]["section_no"] == []
        assert docs[0]["chunk_index"] == 2


# ══════════════════════════════════════════════════════════════════════════
# 4 · 离线门禁：golden set 的每个 gold 条号都能从语料 chunk 抽取到
# ══════════════════════════════════════════════════════════════════════════


class TestGoldenSetClauseCoverage:
    """用 evals 的确定性分块（CI seed 同一函数）+ 定义位口径做离线覆盖校验。

    这保证：重索引后（顶层 clause_no 生效），gold 定义块一定能被精确腿命中。
    无 ES 环境下这是门禁可离线执行的部分；ClauseHit/NearMiss/MRR 全套标
    「待栈恢复复测」。
    """

    def test_every_gold_locator_is_extractable_from_its_corpus_chunk(self):
        from evals.chunking import split_corpus_file
        from evals.golden import load_golden_set

        backend = pathlib.Path(__file__).resolve().parents[2]
        golden = load_golden_set(backend / "evals" / "golden_set" / "clause_set_v1.json")
        refs = _clause_refs()

        for item in golden.positive_items():
            for loc in item.gold:
                text = (golden.corpus_dir / loc.file).read_text(encoding="utf-8")
                chunk_fields = [refs.extract_clause_fields(c) for c in split_corpus_file(text)]
                if loc.clause.startswith("第"):
                    expected = int(loc.clause[1:-1])
                    covered = any(expected in f["clause_no"] for f in chunk_fields)
                else:
                    expected_sec = loc.clause[1:]
                    covered = any(expected_sec in f["section_no"] for f in chunk_fields)
                assert covered, (
                    f"gold 定位 {item.id}:{loc.label()} 无法由索引侧抽取覆盖——"
                    "顶层 clause_no/section_no 写入后精确腿仍会漏掉该 gold"
                )
