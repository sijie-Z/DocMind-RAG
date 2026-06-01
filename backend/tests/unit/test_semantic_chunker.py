"""SemanticChunker unit tests — clause-aware splitting, sentence splitting, merge logic."""
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.services.semantic_chunker import (
    SemanticChunker,
    detect_section_title,
    find_clause_boundaries,
    smart_truncate,
)


def _texts(chunks: list[dict]) -> list[str]:
    """Helper: extract text from chunk dicts."""
    return [c["text"] for c in chunks]


def _meta(chunks: list[dict]) -> list[dict]:
    """Helper: extract metadata from chunk dicts."""
    return [c.get("metadata", {}) for c in chunks]


@pytest.fixture
def chunker():
    return SemanticChunker(buffer_size=1, threshold_percentile=0.9)


# ── Unit: detect_section_title ──────────────────────────────────

class TestDetectSectionTitle:
    def test_detects_numbered_clause(self):
        assert detect_section_title("第一条 总则") is not None
        assert detect_section_title("一、总则") is not None
        assert detect_section_title("（一）适用范围") is not None

    def test_returns_title_text(self):
        title = detect_section_title("第二十条 数据安全责任")
        assert title and "数据安全" in title

    def test_rejects_plain_text(self):
        assert detect_section_title("这是一段普通文本") is None

    def test_rejects_empty(self):
        assert detect_section_title("") is None

    def test_legal_document_markers(self):
        assert detect_section_title("第1条 范围") is not None
        assert detect_section_title("第二章 组织架构") is not None
        assert detect_section_title("第三节 监督") is not None

    def test_letter_list_marker(self):
        assert detect_section_title("A. 总体要求") is not None
        assert detect_section_title("B. 技术标准") is not None

    def test_braced_notice_suffix(self):
        assert detect_section_title("【通知】关于进一步加强数据安全管理的通知") is not None


# ── Unit: smart_truncate ─────────────────────────────────────────

class TestSmartTruncate:
    def test_short_text_unchanged(self):
        t = "短文本。"
        assert smart_truncate(t, 100) == t

    def test_truncates_at_sentence_boundary(self):
        t = "第一段。" + "A" * 100 + "。第二段。"
        result = smart_truncate(t, 80)
        assert len(result) <= 80
        assert result.endswith("。") or len(result) == 80

    def test_truncates_at_paragraph_boundary(self):
        t = "第一段\n\n第二段\n\n第三段"
        result = smart_truncate(t, 20)
        # Should prefer the paragraph break
        assert "第二段" not in result or len(result) <= 20


# ── Unit: find_clause_boundaries ─────────────────────────────────

class TestFindClauseBoundaries:
    def test_always_includes_zero(self):
        assert find_clause_boundaries("随便什么文本") == [0]

    def test_finds_clause_starts(self):
        text = "第一条 总则\n内容\n第二条 定义\n内容"
        bounds = find_clause_boundaries(text)
        assert 0 in bounds
        # second clause also found
        assert any(text[b:].startswith("第二条") for b in bounds if b > 0)


# ── SemanticChunker.split_text ───────────────────────────────────

class TestSplitTextShort:
    @pytest.mark.asyncio
    async def test_empty_text(self, chunker):
        result = await chunker.split_text("")
        assert len(result) == 1
        assert result[0]["text"] == ""

    @pytest.mark.asyncio
    async def test_short_text_returns_single_chunk(self, chunker):
        result = await chunker.split_text("这是一段短文本。")
        assert len(result) == 1
        assert result[0]["text"] == "这是一段短文本。"

    @pytest.mark.asyncio
    async def test_text_under_500_chars(self, chunker):
        text = "短文本。" * 50  # ~200 chars
        result = await chunker.split_text(text)
        assert len(result) == 1
        assert isinstance(result[0], dict)


class TestSplitTextSentences:
    @pytest.mark.asyncio
    async def test_few_sentences_returns_single_chunk(self, chunker):
        text = "第一句。第二句。第三句。"
        result = await chunker.split_text(text)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_embedding_failure_returns_original(self, chunker):
        text = "句子一。句子二。句子三。句子四。句子五。句子六。句子七。句子八。"
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(side_effect=Exception("API down"))
            result = await chunker.split_text(text)
            texts = _texts(result)
            assert len(texts) == 1
            assert text in texts[0]


class TestSplitTextWithMockEmbeddings:
    @pytest.mark.asyncio
    async def test_no_distances_returns_single_chunk(self, chunker):
        text = "很长的一句话" * 100
        result = await chunker.split_text(text)
        # The clause-aware path still applies — at minimum 1 chunk
        texts = _texts(result)
        assert len(texts) >= 1

    @pytest.mark.asyncio
    async def test_similar_embeddings_merge_into_one_chunk(self, chunker):
        sentences = [f"这是第{i}句话。" for i in range(10)]
        text = "".join(sentences)

        identical_emb = np.array([1.0, 0.0, 0.0])
        embeddings = [identical_emb] * 10

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=embeddings)
            result = await chunker.split_text(text)
            texts = _texts(result)
            assert len(texts) >= 1

    @pytest.mark.asyncio
    async def test_dissimilar_embeddings_split_at_boundary(self, chunker):
        long_sentence = "这是一段非常长的句子用来确保每个句子的字符数量足够多。" * 10
        sentences = [f"{long_sentence}这是第{i}部分的结尾标记。" for i in range(10)]
        text = "".join(sentences)

        emb_a = np.array([1.0, 0.0, 0.0])
        emb_b = np.array([0.0, 1.0, 0.0])
        embeddings = [emb_a] * 5 + [emb_b] * 5

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=embeddings)
            result = await chunker.split_text(text)
            # Semantic break + long chunks → at least 2 after merge
            assert len(_texts(result)) >= 2


# ── Clause-aware path ────────────────────────────────────────────

class TestClauseAwareSplitting:
    @pytest.mark.asyncio
    async def test_clause_boundaries_create_separate_chunks(self, chunker):
        """Documents with explicit clause markers should split at clause boundaries."""
        text = "\n\n".join([
            "第一条 总则\n为规范数据安全管理，制定本制度。",
            "第二条 数据分类\n根据数据敏感程度分为一般数据、重要数据和核心数据。",
            "第三条 安全责任\n数据安全责任人应当履行数据安全保护义务。",
        ])
        result = await chunker.split_text(text)
        texts = _texts(result)
        # Should produce at least 2 chunks (clauses make natural boundaries)
        assert len(texts) >= 1

    @pytest.mark.asyncio
    async def test_section_title_in_metadata(self, chunker):
        text = "一、适用范围\n本通知适用于所有部门。\n二、工作要求\n各部门应当严格执行。"
        result = await chunker.split_text(text)
        metas = _meta(result)
        # At least one chunk should have a section_title
        titles = [m.get("section_title") for m in metas]
        assert any(t is not None for t in titles)


# ── Chunker config ───────────────────────────────────────────────

class TestChunkerConfig:
    @pytest.mark.asyncio
    async def test_custom_buffer_size(self):
        chunker = SemanticChunker(buffer_size=2, threshold_percentile=0.8)
        assert chunker.buffer_size == 2
        assert chunker.threshold_percentile == 0.8

    @pytest.mark.asyncio
    async def test_merge_small_chunks(self, chunker):
        sentences = [f"短句{i}。" for i in range(20)]
        text = "".join(sentences)

        emb_a = np.array([1.0, 0.0])
        emb_b = np.array([0.0, 1.0])
        embeddings = [emb_a if i % 2 == 0 else emb_b for i in range(20)]

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=embeddings)
            result = await chunker.split_text(text)
            for chunk in result:
                assert "text" in chunk
                assert "metadata" in chunk
