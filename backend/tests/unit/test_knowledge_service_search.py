"""搜索服务必须解包 pipeline 的 (results, debug_info) 元组（issue #219）。

修复前：`knowledge_service.search_knowledge` 把 `rag_service.search_knowledge_base`
返回的**元组**当列表迭代，`isinstance(res, dict)` 检查把「hits 列表」与 `None`
两个元素全部跳过 → 恒返回 `[]`。后果不是报错而是**静默**：
`POST /api/v1/knowledge/search` 永远搜不到东西，索引里有数据时诊断层还会报
`index_status="ok"`（"有数据、没匹配到"），把代码缺陷呈现成合法业务状态。

本文件不依赖 ES / DB / LLM：只替换 `RagServiceFacade.search_knowledge_base`，
其余（解包 → 格式化 → 返回）走真实代码路径。
"""
from unittest.mock import AsyncMock, patch

import pytest

from app.services.knowledge_service import KnowledgeService

HITS = [
    {
        "document_id": "doc-1",
        "text": "第七条 消费者在购买、使用商品时，其合法权益受到损害的……",
        "score": 0.87,
        "filename": "消费者权益保护法.pdf",
        "snippet": "第七条 消费者在购买……",
        "overlap_score": 0.5,
        "mmr_score": 0.4,
    }
]


def _patch_pipeline(return_value):
    return patch(
        "app.services.rag_service.RagServiceFacade.search_knowledge_base",
        new=AsyncMock(return_value=return_value),
    )


@pytest.mark.asyncio
async def test_returns_hits_from_pipeline_tuple():
    """(hits, None) → 命中必须被格式化返回（修复前恒为 []）。"""
    with _patch_pipeline((HITS, None)):
        out = await KnowledgeService().search_knowledge("第七条", "1", top_k=5)

    assert len(out) == 1
    assert out[0]["id"] == "doc-1"
    assert out[0]["text"].startswith("第七条")
    assert out[0]["score"] == 0.87
    assert out[0]["source"]["filename"] == "消费者权益保护法.pdf"
    assert out[0]["metadata"]["snippet"].startswith("第七条")


@pytest.mark.asyncio
async def test_debug_info_dict_is_not_mistaken_for_a_hit():
    """元组第二项是 dict 时（debug=True 形态），不得被当成一条检索结果。"""
    with _patch_pipeline((HITS, {"total_results": 1, "elapsed_ms": 12.3})):
        out = await KnowledgeService().search_knowledge("第七条", "1", top_k=5)

    assert len(out) == 1
    assert out[0]["id"] == "doc-1"


@pytest.mark.asyncio
async def test_empty_hits_return_empty_list():
    with _patch_pipeline(([], None)):
        out = await KnowledgeService().search_knowledge("无命中查询", "1", top_k=5)

    assert out == []


@pytest.mark.asyncio
async def test_pipeline_failure_still_returns_empty_list():
    """下层抛错时保持既有契约：吞掉并返回 []（不把异常抛给调用方）。"""
    with patch(
        "app.services.rag_service.RagServiceFacade.search_knowledge_base",
        new=AsyncMock(side_effect=RuntimeError("ES down")),
    ):
        out = await KnowledgeService().search_knowledge("x", "1", top_k=5)

    assert out == []
