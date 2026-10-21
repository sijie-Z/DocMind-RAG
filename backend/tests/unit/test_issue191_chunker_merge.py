"""#191 判别测试：跨条合并 + 标题继承错误。

修前缺陷（分诊报告 #191，origin/develop 实测）：
1. `_merge_small_tails` 对**扁平化后的全部 chunk** 合并——跨条款分区无阻；
   且仅当前块无标题时才继承，前块有标题时被并正文永久留在前块标题下；
2. `detect_section_title` 的 80 字护栏恒真（`stripped[:80]` 后判断 `len<=80`），
   条号与正文同行时「标题」= 正文前 80 字；
3. `find_clause_boundaries` 只认行首条号——条号不在行首时整篇单一分区
   （`boundaries == [0]`），整篇交给 p90 语义切分。

本 PR 的修法（分诊推荐）：
- 合并只在**同一分区**内进行（跨条小尾块保留独立：宁可短块，不可错标题）；
- 标题真正限长（≤50 字）并在首个句读处截断；
- 边界识别允许行首空白，并对「句读后紧跟第X条」补边界；
- 边界判定改用「条款标记」而不是「标题检测」——修标题截断不得再影响边界识别。

变异点（红→绿判别）：
- `_merge_small_tails` 去掉分区判定 → `test_cross_clause_small_tail_stays_independent` /
  `test_merge_requires_same_partition` 红；
- `detect_section_title` 恢复 80 字恒真护栏 → `test_title_length_is_bounded` 红；
- `find_clause_boundaries` 只扫行首 → `test_detects_clause_after_sentence_mark` 红；
- 边界判定回退为「依赖 detect_section_title」→ 长正文行的条号不再开分区，
  `test_midline_clause_gets_own_partition` 红。
"""

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.services.semantic_chunker import (
    SemanticChunker,
    detect_section_title,
    find_clause_boundaries,
)


def _identical_embeddings(count: int = 200):
    return [np.array([1.0, 0.0])] * count


class TestDetectSectionTitle:
    def test_title_length_is_bounded(self):
        # 条号与正文同行：旧实现 stripped[:80] 恒过 len<=80，返回 80 字正文
        line = "第一条 " + "正文内容" * 30  # 123 字
        title = detect_section_title(line)
        assert title is not None
        assert len(title) <= 50, f"标题不得超过 50 字（旧 80 字护栏恒真），实际 {len(title)} 字"

    def test_title_cut_at_first_sentence_mark(self):
        line = "第二条 数据分级。数据分为一般、重要和核心三级。"
        assert detect_section_title(line) == "第二条 数据分级"

    def test_marker_line_with_full_sentence_still_returns_title(self):
        title = detect_section_title("第三条 本制度自发布之日起施行。")
        assert title == "第三条 本制度自发布之日起施行"

    def test_short_headings_unchanged(self):
        assert detect_section_title("第二十条 数据安全责任") == "第二十条 数据安全责任"
        assert detect_section_title("一、总则") == "一、总则"
        assert detect_section_title("（一）适用范围") == "（一）适用范围"
        assert detect_section_title("A. 总体要求") == "A. 总体要求"
        assert detect_section_title("这是普通正文，不是标题") is None
        assert detect_section_title("") is None


class TestFindClauseBoundaries:
    def test_detects_clause_after_sentence_mark(self):
        """条号不在行首：句号后紧跟「第X条」也是条款起点（旧实现只认行首）。"""
        text = "前言内容。第二条 数据定义\n正文内容"
        bounds = find_clause_boundaries(text)
        assert text.index("第二条") in bounds, f"句读后的条号必须成为边界，实际 {bounds}"

    def test_does_not_split_on_body_mentions(self):
        """句读后的「依据第7条」不是行首定义位——不得误开分区。"""
        text = "总则内容。依据第7条，本制度适用于全部部门。"
        assert find_clause_boundaries(text) == [0]

    def test_citation_sentence_after_period_is_not_boundary(self):
        """正文引用句（标记后直接接正文）不得开分区，否则引用会变成 chunk 首行
        定义位（精确腿误召回）。标题形态（标记后有空白/分隔符）才开分区。"""
        text = "总则内容，适用于全部部门。第7条另有规定的除外。申请流程详见附件。"
        assert find_clause_boundaries(text) == [0], (
            "「第7条另有规定的除外」是引用句，不是标题；只有「第7条 另有规定…」这类"
            "标题形态才应成为分区边界"
        )

    def test_indented_clause_still_detected(self):
        text = "第一条 总则\n内容\n   第二条 定义\n内容"
        bounds = find_clause_boundaries(text)
        assert any(text[b:].lstrip().startswith("第二条") for b in bounds), (
            f"行首空白不得妨碍条款识别，实际 {bounds}"
        )

    def test_always_includes_zero(self):
        assert find_clause_boundaries("随便什么文本") == [0]


class TestMergeBoundaries:
    """_merge_small_tails 的分区边界（直接锁函数级语义）。"""

    def test_merge_requires_same_partition(self):
        chunker = SemanticChunker()
        chunks = [
            {"text": "A" * 250, "metadata": {"section_title": "第一条 总则", "_partition": 0}},
            {"text": "短尾块", "metadata": {"section_title": "第二条 附则", "_partition": 1}},
        ]
        out = chunker._merge_small_tails(chunks)
        assert len(out) == 2, "跨条小尾块必须保留独立（宁可短块，不可错标题）"
        assert out[1]["metadata"]["section_title"] == "第二条 附则"

    def test_same_partition_small_tail_still_merges(self):
        chunker = SemanticChunker()
        chunks = [
            {"text": "A" * 250, "metadata": {"section_title": "第一条 总则", "_partition": 0}},
            {"text": "短尾块", "metadata": {"section_title": "第一条 总则", "_partition": 0}},
        ]
        out = chunker._merge_small_tails(chunks)
        assert len(out) == 1 and "短尾块" in out[0]["text"], (
            "同一条款分区内的小尾块仍应合并（既有行为不劣化）"
        )

    def test_internal_partition_key_not_leaked(self):
        chunker = SemanticChunker()
        chunks = [
            {"text": "A" * 250, "metadata": {"section_title": "第一条 总则", "_partition": 0}},
            {"text": "短尾块", "metadata": {"section_title": "第一条 总则", "_partition": 0}},
        ]
        out = chunker._merge_small_tails(chunks)
        assert all("_partition" not in c["metadata"] for c in out)


class TestSplitTextCrossClause:
    @pytest.mark.asyncio
    async def test_cross_clause_small_tail_stays_independent(self):
        """端到端复现：长「第一条」+ 短「第二条」不得合并成一块（旧：并块且标题是第一条）。"""
        chunker = SemanticChunker(buffer_size=1, threshold_percentile=0.9, chunk_max_chars=1200)
        clause1 = "第一条 总则\n" + "为规范数据安全管理，本制度适用于全部部门。" * 24
        clause2 = "\n第二条 附则\n本制度自发布之日起施行。"
        text = clause1 + clause2
        assert len(text) > 500

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            chunks = await chunker.split_text(text)

        tail = [c for c in chunks if "第二条" in c["text"]]
        assert len(tail) == 1, "第二条必须作为独立块存在"
        assert "第一条" not in tail[0]["text"], "第二条正文不得留在第一条的块里"
        assert tail[0]["metadata"]["section_title"].startswith("第二条"), (
            f"标题必须随所属条（旧：保留前块第一条），实际 {tail[0]['metadata']['section_title']!r}"
        )
        for c in chunks:
            if "第一条" in c["text"]:
                assert "第二条" not in c["text"], "跨条合并必须消失"

    @pytest.mark.asyncio
    async def test_midline_clause_gets_own_partition(self):
        """条号不在行首：旧实现整篇单分区（boundaries==[0]），第二条被并进第一条块。"""
        chunker = SemanticChunker(buffer_size=1, threshold_percentile=0.9, chunk_max_chars=1200)
        text = (
            "第一条 总则\n"
            + "为规范数据安全管理，本制度适用于全部部门。" * 26
            + "第二条 附则 本制度自发布之日起施行。"
        )
        assert len(text) > 500

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=_identical_embeddings())
            chunks = await chunker.split_text(text)

        clause2_chunks = [c for c in chunks if "第二条" in c["text"]]
        assert clause2_chunks, "第二条内容不得丢失"
        assert all("第一条" not in c["text"] for c in clause2_chunks), (
            "句读后条号必须开新分区：第二条正文不得并入第一条块"
        )
        assert clause2_chunks[0]["metadata"]["section_title"].startswith("第二条")
