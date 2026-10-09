"""#208 Phase 2 判别测试：实验锁现状条目 + 决策 a 落地后的换锁。

把 2026-10-09 逐阶段 trace 实验（结论见 backend/docs/eval-gate-phase2-experiment.md）
证实、且当前代码仍在执行的机制钉成可执行断言——防止在修复决策落地前，
这些机制被无声地漂移或"顺手改掉"。**修复落地的条目在本文件内显式换锁**：

1. `_post_process` 的 doc 帽：同一文档至多 2 个 chunk（即使 top_k 尚有余量）。
   —— 2026-10-09 决策 a 落地（本 PR）：帽保留，但计数移到质量过滤之后，
   被任一质量闸门剔除的块不占帽位（旧行为「先占槽后剔除」已被换下，见
   `TestPostProcessDocCap` 的旧/新锁对照注释）；
2. keyword_only 路径的 raw hit 不带 `has_keyword` 标记（该键只由 `_rrf_fuse` 写）
   → 终滤按最严的 `ov >= 0.15` 档；同一 chunk 在 hybrid 序里带标记（0.10 档）
   就能通过。实测 gold overlap = 0.1167 恰好落在两档之间（未修，仍锁现状）；
3. `extract_query_terms` 截断前 12 个 bigram 且标题 bigram 优先——
   带文档全称的查询里，标题 8 个 bigram 占位，内容词（数据/删除）被截断出列表
   （未修，仍锁现状）；
4. `rewrite_query_candidates` 保留条号文案（实验否证了「改写丢条号」假设，
   作为正向契约锁定）；
5. 4 条失败 query 现状路由 `keyword_only`（实验同时证明改路由不是修复点：
   hybrid 反事实 4/4 仍 miss——锁住是为了让未来的路由变更显式发生）。

**除 a 外的其余环（b：msm 口径；c：数形归一化；d：has_keyword 臂标记；
e：MMR 适用性）仍受本闸门约束**——任何修复其机制的 PR 必须显式更新本文件：
这是刻意的「变更前先测量、变更要自觉」闸门。
"""

import pytest

from app.rag.query_processor import (
    QueryComplexityClassifier,
    extract_query_terms,
    rewrite_query_candidates,
)
from app.rag.retriever import HybridRetriever

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
            _hit("docA_c2", "alpha bravo 填充正文", 0.5),  # 2 命中 → ov=0.1167：过前置闸门、死于终滤
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
    """机制 2：keyword_only raw hit 无臂标记 → 终滤按 0.15 档（hybrid 0.10 档）。"""

    def test_markerless_hit_with_overlap_0117_is_dropped(self):
        retriever = HybridRetriever()
        # 命中 2 个术语 → overlap = 2/12×0.7 = 0.1167（落于 0.10 与 0.15 之间）。
        docs = retriever._post_process(
            TERM_QUERY, [_hit("docA_c1", "alpha bravo 填充正文", 0.5)], top_k=5
        )
        assert docs == [], "keyword_only 路径无 has_keyword 标记 → 0.15 档终滤削掉 0.1167"

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
