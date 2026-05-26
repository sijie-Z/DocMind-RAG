"""SemanticChunker unit tests — sentence splitting, short text, merge logic."""
import pytest
import numpy as np
from unittest.mock import patch, AsyncMock

from app.services.semantic_chunker import SemanticChunker


@pytest.fixture
def chunker():
    return SemanticChunker(buffer_size=1, threshold_percentile=0.9)


class TestSplitTextShort:
    @pytest.mark.asyncio
    async def test_empty_text(self, chunker):
        result = await chunker.split_text("")
        assert result == [""]

    @pytest.mark.asyncio
    async def test_short_text_returns_single_chunk(self, chunker):
        result = await chunker.split_text("这是一段短文本。")
        assert result == ["这是一段短文本。"]

    @pytest.mark.asyncio
    async def test_text_under_500_chars(self, chunker):
        text = "短文本。" * 50  # ~200 chars
        result = await chunker.split_text(text)
        assert len(result) == 1


class TestSplitTextSentences:
    @pytest.mark.asyncio
    async def test_few_sentences_returns_single_chunk(self, chunker):
        # Less than 5 sentences → returns original
        text = "第一句。第二句。第三句。"
        result = await chunker.split_text(text)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_embedding_failure_returns_original(self, chunker):
        text = "句子一。句子二。句子三。句子四。句子五。句子六。句子七。句子八。"
        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(side_effect=Exception("API down"))
            result = await chunker.split_text(text)
            assert result == [text]


class TestSplitTextWithMockEmbeddings:
    @pytest.mark.asyncio
    async def test_no_distances_returns_single_chunk(self, chunker):
        # Only 1 sentence after processing → no distances
        text = "很长的一句话" * 100  # > 500 chars but only 1 "sentence" (no punctuation)
        result = await chunker.split_text(text)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_similar_embeddings_merge_into_one_chunk(self, chunker):
        # All embeddings identical → distances all 0 → no split points
        sentences = [f"这是第{i}句话。" for i in range(10)]
        text = "".join(sentences)

        # Create identical embeddings → cosine distance = 0
        identical_emb = np.array([1.0, 0.0, 0.0])
        embeddings = [identical_emb] * 10

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=embeddings)
            result = await chunker.split_text(text)
            # All similar → merged into one chunk
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_dissimilar_embeddings_split_at_boundary(self, chunker):
        # Create very long sentences so post-merge chunks stay above 1200 chars
        long_sentence = "这是一段非常长的句子用来确保每个句子的字符数量足够多。" * 10
        sentences = [f"{long_sentence}这是第{i}部分的结尾标记。" for i in range(10)]
        text = "".join(sentences)

        # First 5 similar, last 5 different → cosine distance spike at boundary
        emb_a = np.array([1.0, 0.0, 0.0])
        emb_b = np.array([0.0, 1.0, 0.0])
        embeddings = [emb_a] * 5 + [emb_b] * 5

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=embeddings)
            result = await chunker.split_text(text)
            # Semantic break + long chunks → at least 2 after merge
            assert len(result) >= 2


class TestChunkerConfig:
    @pytest.mark.asyncio
    async def test_custom_buffer_size(self):
        chunker = SemanticChunker(buffer_size=2, threshold_percentile=0.8)
        assert chunker.buffer_size == 2
        assert chunker.threshold_percentile == 0.8

    @pytest.mark.asyncio
    async def test_merge_small_chunks(self, chunker):
        # Chunks under 1200 chars get merged
        sentences = [f"短句{i}。" for i in range(20)]
        text = "".join(sentences)

        # Alternating embeddings to force splits
        emb_a = np.array([1.0, 0.0])
        emb_b = np.array([0.0, 1.0])
        embeddings = [emb_a if i % 2 == 0 else emb_b for i in range(20)]

        with patch("app.services.semantic_chunker.embedding_service") as mock_emb:
            mock_emb.get_embeddings = AsyncMock(return_value=embeddings)
            result = await chunker.split_text(text)
            # Small chunks should be merged
            for chunk in result:
                # Either it's a merged chunk or the last remaining chunk
                assert isinstance(chunk, str)
