"""issue #192 收口：中文 wildcard 子句恒空转 + 改写变体的臂边界。

- **wildcard 死分支**：旧实现 ``[f"*{t}*" for t in q_text.split() if len(t) < 20]``
  对无空格的中文查询只产出唯一 token（整句），``*整句*`` 永不命中任何索引词项
  （默认 cjk 分析器下严格成立；IK 分析器下为近似结论，见源码 docstring），
  wildcard 子句对中文恒空转。现复用既有 ``extract_query_terms`` 分词口径，只对
  拉丁/数字词生成通配项（中文召回由 multi_match 的 bigram +
  minimum_should_match 负责），并对术语做白名单与上限约束。
- **臂边界**：多改写变体只进关键词臂；向量臂仍按既有策略 embed 原查询
  （``hybrid_hyde`` 下另含 HyDE 融合，与本边界无关）。这是 #192 的现状边界，
  按分诊报告备选方案明确文档化并由 ``debug_info["query_rewrite"]`` 体现
  （接线属检索质量变更，需 backend/evals 校准延迟/质量后进行）。

变异点（改回/改坏后本文件应红）：
1. ``_wildcard_terms`` 回退为 ``query.split()`` → 中文用例出现 ``*整句*``（红）；
2. 去掉拉丁词白名单（把 CJK bigram 也生成通配项）→ 中文 no-query_string 用例红；
3. 去掉 ``_WILDCARD_TERM_CAP`` 上限 → 上限用例红；
4. ``retrieve()`` 的 debug 不再输出 ``query_rewrite.vector_arm`` → 臂边界用例红。
"""

import json
import re
from unittest.mock import AsyncMock

import pytest

import app.rag.retriever as retriever_mod
from app.core.config import settings
from app.rag.retriever import HybridRetriever


def _capture_es(monkeypatch) -> AsyncMock:
    """假 ES 检索桩：返回空命中，同时捕获实际下发的查询体。"""
    search = AsyncMock(return_value={"hits": {"hits": []}})
    monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", search)
    # 关掉 LLM 改写，保证一次查询只下发一次 ES 检索（本地改写对无标点查询
    # 只产出原查询一条候选）。
    monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", False)
    return search


def _should_clauses(search: AsyncMock) -> list[dict]:
    es_query = search.await_args_list[0].args[0]
    return es_query["query"]["bool"]["must"][0]["bool"]["should"]


# ── 1. 术语生成：中文不生成通配项，拉丁词复用既有分词 ─────────────────────


def test_wildcard_terms_chinese_query_is_empty():
    """中文整句不再变成 ``*整句*`` 通配项（旧实现的死分支）。

    变异点：回退为 ``query.split()`` → 返回值变成 ``["*《…》第七条是什么*"]``，本用例红。
    """
    terms = retriever_mod._wildcard_terms("《消费者权益保护法》第七条是什么")
    assert terms == []
    assert all(not any("一" <= ch <= "鿿" for ch in t) for t in terms)


def test_wildcard_terms_latin_properties():
    """拉丁词走既有分词：小写、ASCII 字母数字、无 CJK、关键术语在场。"""
    terms = retriever_mod._wildcard_terms("How to configure RAG?")
    assert terms, "拉丁查询应产出通配项"
    assert all(re.fullmatch(r"\*[a-z0-9]+\*", term) for term in terms)
    assert "*configure*" in terms and "*rag*" in terms
    assert all(not any("一" <= ch <= "鿿" for ch in term) for term in terms)


def test_wildcard_terms_latin_golden_contract():
    """golden 契约：精确输出与 ``extract_query_terms`` 的当前口径一致。

    与共享分词口径强耦合是**有意**的——本函数承诺复用该口径。若停用词表/
    词正则有意调整，请同步更新本 golden；其余用例均为属性断言，不会因
    分词口径的合理演进误红。
    """
    assert retriever_mod._wildcard_terms("How to configure RAG?") == [
        "*how*",
        "*to*",
        "*configure*",
        "*rag*",
    ]


def test_wildcard_terms_mixed_query_keeps_only_latin():
    """中英混合查询只保留拉丁词——CJK bigram 由 multi_match 负责。"""
    assert retriever_mod._wildcard_terms("Ollama 本地部署") == ["*ollama*"]


def test_wildcard_terms_neutralizes_query_string_syntax():
    """术语白名单只允许 [a-z0-9]：引号/冒号/通配符等 query_string 语法不进入查询体。

    变异点：去掉 ``_WILDCARD_SAFE_TERM_RE`` 校验并用 split() → 术语里出现
    ``"``/``:``/``*``，本用例红。
    """
    terms = retriever_mod._wildcard_terms('foo" OR document_id:1 AND *')
    assert terms, "应仍抽取到合法词"
    # extract_query_terms 的拉丁支路是 [a-z0-9]{2,}：下划线本身不是词字符，
    # "document_id" 拆成 "document" + "id"；"and" 属停用词被剔除。
    for term in terms:
        inner = term[1:-1]
        assert inner.isascii() and inner.isalnum(), term
    assert "*document*" in terms and "*id*" in terms


def test_wildcard_terms_capped():
    """AND 连接的通配项有上限：控制 wildcard 查询代价与该子句满足难度。

    变异点：删掉 ``_WILDCARD_TERM_CAP`` 截断 → 返回 20 项，本用例红。
    """
    query = " ".join(f"token{i}" for i in range(20))
    terms = retriever_mod._wildcard_terms(query)
    assert len(terms) == retriever_mod._WILDCARD_TERM_CAP == 8


# ── 2. 实际下发的 ES 查询体：中文无 query_string，拉丁有通配子句 ───────────


@pytest.mark.asyncio
async def test_keyword_hits_chinese_query_has_no_wildcard_should(monkeypatch):
    """中文查询的 bool.should 只剩 multi_match，wildcard 死分支不再出现。

    变异点：``_wildcard_terms`` 放行 CJK bigram → 第二个 should 出现，本用例红。
    """
    search = _capture_es(monkeypatch)
    retriever = HybridRetriever()

    await retriever._keyword_hits("数据安全怎么保障", [], candidate_size=10)

    should = _should_clauses(search)
    assert len(should) == 1
    assert "multi_match" in should[0]
    assert "query_string" not in json.dumps(should, ensure_ascii=False)


@pytest.mark.asyncio
async def test_keyword_hits_latin_query_adds_wildcard_should(monkeypatch):
    """拉丁查询保留子串召回：``*term*`` 白名单术语 + AND 连接。"""
    search = _capture_es(monkeypatch)
    retriever = HybridRetriever()

    await retriever._keyword_hits("configure reload", [], candidate_size=10)

    should = _should_clauses(search)
    assert len(should) == 2
    query_string = should[1]["query_string"]
    assert query_string["query"] == "*configure* AND *reload*"
    assert query_string["analyze_wildcard"] is True
    assert query_string["fields"] == ["content^2", "filename^1.2"]


# ── 3. 臂边界可观测：改写变体不进向量臂（HyDE 另有策略级标记） ─────────────


@pytest.mark.asyncio
async def test_debug_marks_vector_arm_original_query_only(monkeypatch):
    """hybrid 检索的 debug 载荷体现「改写只进关键词臂」的现状边界。

    变异点：删掉 ``debug_info["query_rewrite"]`` → 本用例 KeyError（红）。
    """
    retriever = HybridRetriever()

    async def fake_kw(query, filters, size):
        return []

    async def fake_vec(query, filters, size, enable_hyde=True):
        return [], [1.0, 0.0], None

    async def fake_fallback(query, filters, top_k):
        return [], None

    monkeypatch.setattr(retriever, "_keyword_hits", fake_kw)
    monkeypatch.setattr(retriever, "_vector_hits", fake_vec)
    monkeypatch.setattr(retriever, "_fallback", fake_fallback)
    monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", True)

    _, _, debug_info, _ = await retriever.retrieve(
        "如何配置数据安全策略和流程",
        organization_id=1,
        top_k=5,
        debug=True,
        search_mode="hybrid",
    )

    assert debug_info["query_rewrite"] == {
        "llm_rewrite_enabled": True,
        "keyword_arm": "rewritten_variants",
        "vector_arm": "original_query_only",
        "vector_hyde_enabled": False,
    }


@pytest.mark.asyncio
async def test_debug_hyde_marker_is_strategy_level(monkeypatch):
    """hybrid_hyde 路由下 ``vector_hyde_enabled=True`` 只是策略级标记。

    它不代表本次真的发生了 HyDE 融合（无 client / 超时 / 空向量均不融合）。
    变异点：把该键改成恒 False 或恒 True → 本用例或 hybrid 用例红。
    """
    retriever = HybridRetriever()

    async def fake_kw(query, filters, size):
        return []

    async def fake_vec(query, filters, size, enable_hyde=True):
        return [], [1.0, 0.0], None

    async def fake_fallback(query, filters, top_k):
        return [], None

    monkeypatch.setattr(retriever, "_keyword_hits", fake_kw)
    monkeypatch.setattr(retriever, "_vector_hits", fake_vec)
    monkeypatch.setattr(retriever, "_fallback", fake_fallback)

    _, _, debug_info, _ = await retriever.retrieve(
        "对比分析数据安全策略与流程的优缺点和影响",
        organization_id=1,
        top_k=5,
        debug=True,
    )

    assert debug_info["strategy"] == "hybrid_hyde"
    assert debug_info["query_rewrite"]["vector_hyde_enabled"] is True
