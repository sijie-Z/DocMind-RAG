"""Unit tests for the unified document processor."""

import pytest

from app.worker.doc_processor import DocumentProcessor


@pytest.mark.asyncio
async def test_get_embeddings_preserves_length(monkeypatch):
    async def fake_get_embeddings(texts):
        return [[1.0]] * len(texts)

    monkeypatch.setattr(
        "app.worker.doc_processor.embedding_service.get_embeddings",
        fake_get_embeddings,
    )

    processor = DocumentProcessor()
    embeddings = await processor._get_embeddings(["hello", "", "world"])

    assert len(embeddings) == 3
