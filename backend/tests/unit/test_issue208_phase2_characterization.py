"""#208 Phase 2 判别测试：实验锁现状条目 + 两环修复后的新锁（a/b）。

把 2026-10-09 逐阶段 trace 实验（结论见 backend/docs/eval-gate-phase2-experiment.md）
证实、且当前代码仍在执行的机制钉成可执行断言。**这些测试锁的是「实验测得的现状」，
在决策落地前防止机制被无声漂移；修复落地的条目在本文件内显式换锁**：

1. `_post_process` 的 doc 帽：同一文档至多 2 个 chunk（即使 top_k 尚有余量）。
   —— 2026-10-09 决策 a 落地（本 PR）：帽保留，但计数移到质量过滤之后，
   被任一质量闸门剔除的块不占帽位（旧行为「先占槽后剔除」已被换下，见
   `TestPostProcessDocCap` 的旧/新锁对照注释）；
2. keyword_only 路径的命中此前不带 `has_keyword` 标记（该键只由 `_rrf_fuse` 写）
   → 终滤按最严的 `ov >= 0.15` 档；同一 chunk 在 hybrid 序里带标记（0.10 档）
   就能通过。实测 gold overlap = 0.1167 恰好落在两档之间。
   —— 2026-10-09 决策 d 落地（本 PR）：`retrieve` 的 keyword_only 分支给命中
   补上 `has_keyword=True`（与 hybrid 序一致），终滤改走 0.10 档。旧单元锁
   `TestKeywordOnlyArmMarkerGap` 的两条断言（`_post_process` 函数层语义）保留：
   函数对「无标记命中」的判定式未变；被换下的是**产品路径不再产出无标记的
   keyword_only 命中**这一前提。新锁见 `TestKeywordOnlyArmMarkerAligned`；
3. `extract_query_terms` 截断前 12 个 bigram 且标题 bigram 优先——
   带文档全称的查询里，标题 8 个 bigram 占位，内容词（数据/删除）被截断出列表
   （未修，仍锁现状）；
4. `rewrite_query_candidates` 保留条号文案（实验否证了「改写丢条号」假设，
   作为正向契约锁定）；
5. 4 条失败 query 现状路由 `keyword_only`（实验同时证明改路由不是修复点：
   hybrid 反事实 4/4 仍 miss——锁住是为了让未来的路由变更显式发生）；
6. keyword 臂的 `minimum_should_match`：带《…》文档全称的查询按「剔除标题 token
   后的 30%」（整数）计算；无全称查询保持 `"30%"`。
   —— 2026-10-09 决策 b 落地（PR #323），闭掉评审发现的 msm 环无断言缺口
   （此前把常量改 10% 全量测试仍绿，见 `TestKeywordArmMsmCaliber`）。
7. `第X条` 数形互为扩展（中文形⇄阿拉伯形并入 keyword 臂变体检索，查询侧、
   不动索引）。—— 2026-10-09 决策 c 落地（本 PR）：`第七`≠`第7` 导致中文数词
   查询的 gold 连候选都进不去（clause_chinese_7/chinese_12 的实验事实），
   本文件以 `TestNumeralClauseNormalization` 为新锁。

**e 环（MMR 对条号直查的适用性）仍受本闸门约束**——
任何修复其机制的 PR 必须显式更新本文件：这是刻意的「变更前先测量、变更要自觉」闸门。
"""

import pytest

import app.rag.retriever as retriever_mod
from app.core.config import settings
from app.rag.query_processor import (
    QueryComplexityClassifier,
    extract_query_terms,
    rewrite_query_candidates,
)
from app.rag.retriever import (
    HybridRetriever,
    _count_tokens_cjk,
    _int_to_cn_clause_number,
    _minimum_should_match_for,
    _numeral_clause_variants,
    _parse_clause_number,
)

# 12 个 ASCII 术语的探针查询：extract_query_terms 对拉丁词的抽取无截断歧义。
TERM_QUERY = "alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima"

# 实验中的 4 条失败 query（带文档全称的条号直查）+ 其条号文案。
FAILING_QUERIES = [
    ("《知识库服务管理办法》第3条规定了什么？", "第3条"),
    ("《知识库服务管理办法》第七条规定了什么？", "第七条"),
    ("《知识库服务管理办法》第7条规定了什么？", "第7条"),
    ("《知识库服务管理办法》第十二条关于数据删除的规定是什么？", "第十二条"),
]


def _hit(chunk_id: str, text: str, score: float, *, keyword_marked: bool = False) -> dict:
    """构造 keyword 臂 raw hit 形状；keyword_marked=True 时模拟经 _rrf_fuse 的形态。"""
    hit = {
        "_id": chunk_id,
        "_score": score,
        "_source": {"content": text, "filename": "x.md", "document_id": chunk_id.split("_")[0]},
    }
    if keyword_marked:
        hit["has_keyword"] = True
        hit["keyword_rank"] = 1
    return hit


class TestPostProcessDocCap:
    """机制 1：doc 帽 2/文档；#208 Phase 2 决策 a 后，计数在质量过滤之后。

    旧锁（实验现状）：计数发生在质量过滤之前——被剔的块也占帽位。
    新锁（本 PR）：帽仍为 2/文档，但只有通过全部质量闸门的块才占帽位；
    被剔除的块不占帽位、也不占 top_k 结果预算。
    """

    def test_third_chunk_of_same_document_is_capped_out(self):
        retriever = HybridRetriever()
        # 每个 chunk 命中 3 个术语 → overlap = 3/12×1.15 = 0.2875 ≥ 0.15（质量全达标）。
        fused = [
            _hit("docA_c1", "alpha bravo charlie 正文", 0.9),
            _hit("docA_c2", "alpha bravo charlie 其余内容", 0.8),
            _hit("docA_c3", "alpha bravo charlie 更多内容", 0.7),
        ]
        terms = extract_query_terms(TERM_QUERY)
        probe = retriever._compute_overlap("x.md alpha bravo charlie 更多内容", terms)
        assert probe >= 0.15, "前置：被削 chunk 的 overlap 达标——削它的是帽，不是质量过滤"

        docs = retriever._post_process(TERM_QUERY, fused, top_k=5)

        # top_k=5 有空位，但同文档第 3 个质量达标 chunk 仍被削（>2 → continue）。
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docA_c2"]

    def test_second_document_is_not_affected_by_cap(self):
        """帽是 per-document 的：换一个 document_id 的第二个 chunk 不受影响。"""
        retriever = HybridRetriever()
        fused = [
            _hit("docA_c1", "alpha bravo charlie 正文", 0.9),
            _hit("docB_c1", "alpha bravo charlie 另一文档", 0.8),
            _hit("docB_c2", "alpha bravo charlie 另一文档之二", 0.7),
        ]
        docs = retriever._post_process(TERM_QUERY, fused, top_k=5)
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docB_c1", "docB_c2"]

    def test_quality_rejected_chunk_does_not_consume_doc_cap_slot(self):
        """决策 a 新锁：被质量闸门（ov<0.005 档）剔除的块不占帽位。

        旧锁：docA_c2 先占掉 docA 第 2 个槽位（计数在质量过滤前），docA_c3 被帽削，
        结果只有 [docA_c1]（实验 Q7 轨迹实测的同型顺序）；新锁：[docA_c1, docA_c3]。
        """
        retriever = HybridRetriever()
        fused = [
            _hit("docA_c1", "alpha bravo charlie 正文", 0.9),
            _hit("docA_c2", "完全无关的填充内容", 0.7),  # 0 个术语命中 → ov=0 < 0.005
            _hit("docA_c3", "alpha bravo charlie 更多内容", 0.8),
        ]
        docs = retriever._post_process(TERM_QUERY, fused, top_k=5)
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docA_c3"], (
            "被 ov<0.005 闸门剔除的 docA_c2 不得占用 docA 的帽位"
        )

    def test_final_gate_rejected_chunk_does_not_consume_doc_cap_slot(self):
        """决策 a 新锁：只死于终滤档（0.05 ≤ ov < 0.15，无臂标记）的块同样不占帽位。

        旧锁：docA_c2（ov=0.1167，先占帽位）→ docA_c3 被削 → [docA_c1]；
        新锁：[docA_c1, docA_c3]。与 α 档用例一起钉住「被任一质量闸门剔除都不占槽」。
        """
        retriever = HybridRetriever()
        fused = [
            _hit("docA_c1", "alpha bravo charlie 正文", 0.5),
            _hit(
                "docA_c2", "alpha bravo 填充正文", 0.5
            ),  # 2 命中 → ov=0.1167：过前置闸门、死于终滤
            _hit("docA_c3", "alpha bravo charlie 更多内容", 0.5),
        ]
        probe = HybridRetriever._compute_overlap(
            "x.md alpha bravo 填充正文", extract_query_terms(TERM_QUERY)
        )
        assert 0.05 <= probe < 0.15, "前置：该块只死于终滤档——overlap 落在 [0.05, 0.15)"

        docs = retriever._post_process(TERM_QUERY, fused, top_k=5)
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docA_c3"], (
            "被终滤档剔除的块不得占用帽位"
        )

    def test_rejected_chunk_frees_top_k_budget(self):
        """决策 a 新锁：被剔的块不占 top_k 结果预算（此前先 append 占名额、后终滤剔除）。

        旧锁：top_k=2 时 [docA_c1, docA_c2(占名额), docB_c1] → c2 被终滤剔除后只剩
        [docA_c1]（docB_c1 根本没被处理到）；新锁：[docA_c1, docB_c1]。
        """
        retriever = HybridRetriever()
        fused = [
            _hit("docA_c1", "alpha bravo charlie 正文", 0.9),
            _hit("docA_c2", "alpha bravo 填充正文", 0.7),  # ov=0.1167：只死于终滤
            _hit("docB_c1", "alpha bravo charlie 另一文档", 0.8),
        ]
        docs = retriever._post_process(TERM_QUERY, fused, top_k=2)
        assert [d["chunk_id"] for d in docs] == ["docA_c1", "docB_c1"], (
            "被终滤剔除的块不得占用 top_k 名额（docB_c1 应被处理到）"
        )


class TestKeywordOnlyArmMarkerGap:
    """机制 2 旧锁（保留为 _post_process 函数级语义）：无臂标记 → 0.15 档。

    旧锁（实验现状）：keyword_only 路径的 raw hit 不带 `has_keyword` 标记
    （该键只由 `_rrf_fuse` 写）→ 终滤按最严 `ov >= 0.15` 档，实测 gold
    overlap=0.1167 恰死线上。
    —— 2026-10-09 决策 d 换锁：产品路径（`retrieve` 的 keyword_only 分支）
    已给命中补 `has_keyword=True`，不再产出无标记命中（新锁见
    `TestKeywordOnlyArmMarkerAligned`）。此处两条断言按原样保留：钉住的是
    `_post_process` 对「无标记命中」与「带标记命中」的判定式本身——函数级
    契约未变，若未来有人把函数内 0.15/0.10 两档改坏仍会在此处精确红。
    """

    def test_markerless_hit_with_overlap_0117_is_dropped(self):
        retriever = HybridRetriever()
        # 命中 2 个术语 → overlap = 2/12×0.7 = 0.1167（落于 0.10 与 0.15 之间）。
        docs = retriever._post_process(
            TERM_QUERY, [_hit("docA_c1", "alpha bravo 填充正文", 0.5)], top_k=5
        )
        assert docs == [], "无 has_keyword 标记的命中按 0.15 档终滤削掉 0.1167（函数级语义）"

    def test_same_hit_with_keyword_marker_passes_at_010(self):
        retriever = HybridRetriever()
        docs = retriever._post_process(
            TERM_QUERY,
            [_hit("docA_c1", "alpha bravo 填充正文", 0.5, keyword_marked=True)],
            top_k=5,
        )
        assert [d["chunk_id"] for d in docs] == ["docA_c1"], (
            "同一 overlap=0.1167，带 has_keyword（hybrid 序形态）时走 0.10 档放行"
        )


class TestKeywordOnlyArmMarkerAligned:
    """机制 2 新锁（决策 d）：keyword_only 序的命中携带 has_keyword 臂标记。

    `retrieve` 的 keyword_only 分支此前不过 `_rrf_fuse`，raw hit 拿不到
    `has_keyword` → 终滤 0.15 档（旧锁）。决策 d 后该分支显式补标记，
    与 hybrid 序一致地走 0.10 档；上游闸门（如 γ：has_kw 且 ov<0.02）也随
    标记语义对齐——标记是「与 hybrid 同口径」，不是无条件放行。
    """

    @pytest.mark.asyncio
    async def test_keyword_only_path_marks_hits_and_passes_mid_overlap(self, monkeypatch):
        """端到端：keyword_only 路由下，overlap=0.1167 的命中经标记后放行。"""
        retriever = HybridRetriever()

        async def fake_kw(query, filters, candidate_size):
            # 与旧锁同构：2 个术语命中 → ov = 0.1167（0.10 与 0.15 之间）。
            return [_hit("docA_c1", "alpha bravo 填充正文", 0.5)]

        monkeypatch.setattr(retriever, "_keyword_hits", fake_kw)
        monkeypatch.setattr(
            QueryComplexityClassifier,
            "get_strategy",
            classmethod(lambda cls, _q: "keyword_only"),
        )

        docs, _qv, dbg, _deg = await retriever.retrieve(TERM_QUERY, 1, top_k=5, debug=True)
        assert [d["chunk_id"] for d in docs] == ["docA_c1"], (
            "决策 d：keyword_only 命中带臂标记 → 0.1167 走 0.10 档放行"
        )
        assert docs[0]["has_keyword"] is True
        assert dbg.get("strategy") == "keyword_only"
        # 调试载荷与 post_process 看到同一批标记（keyword_top 也带 has_keyword）。
        assert dbg["keyword_top"][0]["has_keyword"] is True

    @pytest.mark.asyncio
    async def test_marked_hit_below_gamma_gate_is_still_dropped(self, monkeypatch):
        """标记不是无条件放行：ov=0（<0.02）的命中在 γ 闸门（has_kw 档）被剔。"""
        retriever = HybridRetriever()

        async def fake_kw(query, filters, candidate_size):
            return [_hit("docA_c1", "完全无关的填充内容", 0.5)]

        monkeypatch.setattr(retriever, "_keyword_hits", fake_kw)
        monkeypatch.setattr(
            QueryComplexityClassifier,
            "get_strategy",
            classmethod(lambda cls, _q: "keyword_only"),
        )

        docs, _qv, _dbg, _deg = await retriever.retrieve(TERM_QUERY, 1, top_k=5)
        assert docs == [], "与 hybrid 序一致：has_kw 且 ov<0.02 仍死于 γ 闸门"


class TestQueryTermTruncationTilt:
    """机制 3：术语表 12 项截断 + 标题 bigram 占前 8 位。"""

    def test_title_bigrams_fill_the_12_term_budget(self):
        terms = extract_query_terms("《知识库服务管理办法》第3条规定了什么？")
        assert terms == [
            "知识",
            "识库",
            "库服",
            "服务",
            "务管",
            "管理",
            "理办",
            "办法",
            "条规",
            "规定",
            "定了",
            "了什",
        ]

    def test_content_terms_are_truncated_out_for_title_heavy_query(self):
        terms = extract_query_terms("《知识库服务管理办法》第十二条关于数据删除的规定是什么？")
        assert len(terms) == 12
        # 标题占 8 位后，第 9-12 位是条号 bigram；内容词（数据/删除）在截断外——
        # 实验里 clause_chinese_12 的 overlap 分子正是因此为 0。
        assert "数据" not in terms and "删除" not in terms


class TestRewriteKeepsClauseNumber:
    """机制 4（正向契约）：「改写丢条号」已被实验否证，锁住该契约。"""

    @pytest.mark.parametrize(("query", "clause"), FAILING_QUERIES)
    def test_every_rewrite_variant_contains_the_clause_reference(self, query, clause):
        variants = rewrite_query_candidates(query)
        assert variants, "改写站必须至少返回原查询"
        assert all(clause in v for v in variants), f"改写变体不得丢掉 {clause}"


class TestFailingQueryRoutingLocks:
    """机制 5：4 条失败 query 现状路由（hybrid 反事实证明改路由不是修复点）。"""

    @pytest.mark.parametrize(("query", "_clause"), FAILING_QUERIES)
    def test_routes_keyword_only_today(self, query, _clause):
        assert QueryComplexityClassifier.classify(query) == "simple"
        assert QueryComplexityClassifier.get_strategy(query) == "keyword_only"


class TestKeywordArmMsmCaliber:
    """机制 6（决策 b 新锁）：keyword 臂 msm 30% 的基数剔除《…》文档全称 token。

    旧锁（实验现状）：msm 常量 "30%"，基数含标题 bigram——标题 8 个 bigram
    把基数 7 抬到 15，只命中内容词的 gold 被整条剪掉（评审实测的闸门缺口：
    把常量改成 10% 全量测试仍绿、不被惊动）。
    新锁（本 PR）：带《…》全称时按剔除标题后的 token 数计算 30%（整数，
    向下取整、下限 1）；无全称时保持 "30%"。常量 30 不变，只改口径。
    """

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("《知识库服务管理办法》第3条规定了什么？", 2),
            ("《知识库服务管理办法》第七条规定了什么？", 2),
            ("《知识库服务管理办法》第7条规定了什么？", 2),
            ("《知识库服务管理办法》第十二条关于数据删除的规定是什么？", 4),
            # 去尾标点的本地改写变体走同一口径（_es_search 对每个变体各算一次）。
            ("《知识库服务管理办法》第3条规定了什么", 2),
        ],
    )
    def test_msm_excludes_title_tokens(self, query, expected):
        assert _minimum_should_match_for(query) == expected

    def test_msm_stays_percentage_without_title_span(self):
        for q in (
            "审计日志保留多久？",
            "第 9 条对审计日志有什么规定？",
            "第99条对跨境数据传输是怎么规定的？",
        ):
            assert _minimum_should_match_for(q) == "30%", (
                f"无《…》全称的查询必须保持原 '%' 口径（不做近似换算）：{q}"
            )

    def test_token_count_calibrated_against_es_analyzer_examples(self):
        # 校准锚点（与 ES `_analyze`（field=content, analyzer=cjk）逐条一致）：
        assert _count_tokens_cjk("《知识库服务管理办法》第3条规定了什么？") == 15
        assert _count_tokens_cjk("知识库服务管理办法") == 8  # 标题 bigram 数
        assert _count_tokens_cjk("《知识库服务管理办法》第十二条关于数据删除的规定是什么？") == 23
        # 由 _minimum_should_match_for 实测的推导：15−8=7 → floor(0.3×7)=2；
        # 23−8=15 → floor(0.3×15)=4。

    @pytest.mark.asyncio
    async def test_keyword_arm_es_query_carries_the_caliber(self, monkeypatch):
        """端到端判别：`_keyword_hits` 生成的 ES query 真的带口径后的 msm。

        这是评审对 msm 环的闭环建议（「给 _keyword_hits 生成的 ES query 加一条
        stdlib-mock 断言」）：冻结口径后，再把常量或基数改坏都会被本用例抓住。
        """
        captured: list[dict] = []

        async def fake_search(es_query):
            captured.append(es_query)
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)
        # 与默认值显式对齐，隔离 .env / 环境差异对改写条数的扰动。
        monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", True)
        monkeypatch.setattr(settings, "RAG_QUERY_REWRITE_COUNT", 4)

        def _msms():
            return [
                q["query"]["bool"]["must"][0]["bool"]["should"][0]["multi_match"][
                    "minimum_should_match"
                ]
                for q in captured
            ]

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits(
            "《知识库服务管理办法》第3条规定了什么？", [], candidate_size=10
        )
        got = _msms()
        assert got and all(v == 2 for v in got), (
            f"带《…》全称的查询 msm 应为剔除标题后的整数 2，实际 {got}"
        )

        captured.clear()
        await retriever._keyword_hits("审计日志保留多久？", [], candidate_size=10)
        got = _msms()
        assert got and all(v == "30%" for v in got), f"无全称查询应保持 '30%'，实际 {got}"


class TestNumeralClauseNormalization:
    """机制 7（决策 c 新锁）：`第X条` 中文形⇄阿拉伯形互为扩展项（查询侧，不动索引）。

    实验事实（#208 Phase 2）：cjk bigram 下 `第七`≠`第7`，中文数词查询
    （clause_chinese_7 / clause_chinese_12）的 gold 连候选都进不去；语料
    （`backend/evals/corpus/`）条号以阿拉伯写作（第7条/第12条）。修复：
    查询侧把两种写法互为扩展项并入 keyword 臂的变体检索（每变体一次检索、
    合并取最高分）——无需重索引，同物理索引 before/after 可对照。
    """

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("《知识库服务管理办法》第七条规定了什么？", ["《知识库服务管理办法》第7条规定了什么？"]),
            ("《知识库服务管理办法》第7条规定了什么？", ["《知识库服务管理办法》第七条规定了什么？"]),
            ("第十二条关于数据删除的规定是什么？", ["第12条关于数据删除的规定是什么？"]),
            # 空格写法按规范形生成两个方向（第9条 / 第九条）。
            ("第 9 条对审计日志有什么规定？", ["第9条对审计日志有什么规定？", "第九条对审计日志有什么规定？"]),
            # 100+ 只有阿拉伯向（中文形超出 1..99 映射范围，按 None 跳过）。
            ("第一百零五条", ["第105条"]),
            # 多跨度：每个「第X条」都参与转换。
            ("第7条与第十二条冲突吗？", ["第7条与第12条冲突吗？", "第七条与第十二条冲突吗？"]),
            # 等价写法（已是目标形）不产生冗余变体。
            ("第7条", ["第七条"]),
        ],
    )
    def test_mutual_expansion_pairs(self, query, expected):
        assert _numeral_clause_variants(query) == expected

    @pytest.mark.parametrize(
        "query",
        [
            "第一章总则是什么？",  # 「章」不动（须以 条 收尾）
            "第3.5条的规定？",  # 数字段含小数点 → 非「第X条」跨度
            "第零条",  # 0 不转换
            "审计日志保留多久？",  # 无条号
            "",
        ],
    )
    def test_boundaries_generate_no_variant(self, query):
        assert _numeral_clause_variants(query) == []

    def test_roundtrip_1_to_99(self):
        for n in range(1, 100):
            cn = _int_to_cn_clause_number(n)
            assert cn is not None and _parse_clause_number(cn) == n, f"n={n} 往返失败：{cn}"

    def test_chinese_form_out_of_range(self):
        assert _int_to_cn_clause_number(0) is None
        assert _int_to_cn_clause_number(100) is None
        # 解析口径与 evals/clauses.parse_cn_number 对齐（校准锚点）。
        assert _parse_clause_number("一百零五") == 105
        assert _parse_clause_number("两千") == 2000
        assert _parse_clause_number("廿") is None

    @pytest.mark.asyncio
    async def test_keyword_arm_searches_both_numeral_forms(self, monkeypatch):
        """端到端判别：中文数字查询的 keyword 臂真的发出阿拉伯形变体检索。

        这是决策 c 与 keyword 臂的接线闭环——只加函数不接线、或把变体截断丢出，
        本用例都会红（原查询仍必须被检索，变体紧随其后）。
        """
        captured: list[str] = []

        async def fake_search(es_query):
            captured.append(
                es_query["query"]["bool"]["must"][0]["bool"]["should"][0]["multi_match"]["query"]
            )
            return {"hits": {"hits": []}}

        monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", fake_search)
        monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", True)
        monkeypatch.setattr(settings, "RAG_QUERY_REWRITE_COUNT", 4)

        retriever = HybridRetriever(openai_client=None)
        await retriever._keyword_hits(
            "《知识库服务管理办法》第七条规定了什么？", [], candidate_size=10
        )
        assert any("第7条" in q for q in captured), f"keyword 臂应检索阿拉伯形变体，实际 {captured}"
        assert captured[0].startswith("《知识库服务管理办法》第七条"), "原查询仍须被检索"
        assert len(captured) == 3, f"本地 2 条（原查询+去标点）+ 1 条数形变体，实际 {captured}"

        captured.clear()
        await retriever._keyword_hits("审计日志保留多久？", [], candidate_size=10)
        assert captured and all("第" not in q for q in captured), (
            f"无条号查询不得新增数形变体，实际 {captured}"
        )
