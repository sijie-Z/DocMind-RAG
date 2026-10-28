"""搜索 REST 面的原文溯源：O 正文透传 + strict 拒答不吞成空数组。"""

from unittest.mock import AsyncMock

import pytest

from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services.knowledge_service import KnowledgeService


@pytest.mark.asyncio
async def test_search_formats_content_and_provenance(monkeypatch):
    fake = AsyncMock(
        return_value=(
            [
                {
                    "document_id": "doc-1",
                    "text": "【文档背景：生成摘要】原文甲",
                    "snippet": "原文甲",
                    "text_provenance": "parser_original",
                    "score": 1.0,
                    "filename": "a.md",
                },
                {
                    "document_id": "doc-2",
                    "text": "【文档背景：生成摘要】旧文乙",
                    "snippet": "【文档背景：生成摘要】旧文乙",
                    "text_provenance": "legacy_unverified",
                    "score": 0.5,
                    "filename": "b.md",
                },
            ],
            None,
        )
    )
    monkeypatch.setattr("app.services.rag_service.rag_service.search_knowledge_base", fake)

    out = await KnowledgeService().search_knowledge("问题", 1)

    assert out[0]["content"] == "原文甲"
    assert out[0]["text_provenance"] == "parser_original"
    assert out[0]["text"] == "【文档背景：生成摘要】原文甲"
    assert out[1]["content"].startswith("【文档背景")
    assert out[1]["text_provenance"] == "legacy_unverified"


@pytest.mark.asyncio
async def test_strict_provenance_refusal_propagates(monkeypatch):
    fake = AsyncMock(side_effect=SourceProvenanceUnavailableError(["doc-1"], unverified_count=3))
    monkeypatch.setattr("app.services.rag_service.rag_service.search_knowledge_base", fake)

    with pytest.raises(SourceProvenanceUnavailableError):
        await KnowledgeService().search_knowledge("问题", 1)


@pytest.mark.asyncio
async def test_suggestions_degrade_but_main_search_refuses(monkeypatch):
    fake = AsyncMock(side_effect=SourceProvenanceUnavailableError(["doc-1"], unverified_count=1))
    monkeypatch.setattr("app.services.rag_service.rag_service.search_knowledge_base", fake)

    assert await KnowledgeService().get_search_suggestions("问题", "1") == []
