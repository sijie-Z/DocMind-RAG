"""R3 回归测试：语义分块器不得静默丢弃超长块内容。

修复前：当 Embedding 距离无断点（重复文本/表格/列表等）时，整个分区被打成
一块，第 8 步 smart_truncate 直接截断到 chunk_max_chars，分区里约 95% 的
内容被无报错丢弃（Document.status 仍为 INDEXED）。
"""

from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.services.semantic_chunker import SemanticChunker, _split_overlong_text


class TestOverlongChunkNotTruncated:
    @pytest.mark.asyncio
    async def test_uniform_text_is_split_not_truncated(self):
        chunker = SemanticChunker(buffer_size=1, threshold_percentile=0.9, chunk_max_chars=1200)

        unit = "重复测试内容" * 30 + "。"  # 每个句子 181 字
        text = "".join(f"第{i:03d}段" + unit for i in range(100))  # ~18600 字
        assert len(text) > 15000

        # 所有窗口 embedding 相同 → 距离全为 0 → 无任何语义断点（复现触发条件）
        identical = np.array([1.0, 0.0, 0.0])
        embeddings = [identical] * 100
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=embeddings)
            chunks = await chunker.split_text(text)

        texts = [c["text"] for c in chunks]
        joined = "".join(texts)

        assert len(texts) > 1, "超长块必须被切成多块而不是截断"
        assert joined == text, "切分不得丢失任何内容（含顺序）"
        assert joined.endswith(text[-200:]), "输出必须包含输入尾部（修复前此处丢失约 19k 字符）"
        assert max(len(t) for t in texts) <= chunker.chunk_max_chars + 200, (
            "单块不得远超上限（尾块合并容差）"
        )

    @pytest.mark.asyncio
    async def test_uniform_text_with_clause_partition_keeps_tail(self):
        """带条款标记的超长分区内部无语义断点时，尾部内容同样不能丢。"""
        chunker = SemanticChunker(buffer_size=1, threshold_percentile=0.9, chunk_max_chars=800)
        text = "第一条 总则" + ("本条款内容重复出现。" * 100)  # 单一超长分区，约 1000 字
        assert len(text) > 800

        identical = np.array([0.5, 0.5, 0.0])
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=[identical] * 100)
            chunks = await chunker.split_text(text)

        joined = "".join(c["text"] for c in chunks)
        assert len(chunks) > 1
        assert joined == text
        assert joined.endswith(text[-120:])


class TestSplitOverlongHelper:
    def test_split_preserves_content_exactly(self):
        text = "句子一。句子二。" * 500
        pieces = _split_overlong_text(text, 100)
        assert len(pieces) > 1
        assert "".join(pieces) == text

    def test_split_respects_max_length(self):
        text = "A" * 2500
        pieces = _split_overlong_text(text, 1000)
        assert [len(p) for p in pieces] == [1000, 1000, 500]

    def test_short_text_untouched(self):
        assert _split_overlong_text("短文。", 100) == ["短文。"]
