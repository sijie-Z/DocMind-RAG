"""T6 回归：vector_search 的 top_k 上限钳制（≤20）。

修复前：vector_search 把调用方传入的 top_k 原样写进 ES 请求的 ``size``，
top_k=1000 时就会向 ES 请求 1000 条命中（工具 schema 也未声明上限）。
修复后：ES 请求 size 被钳制到 20，schema 声明 ``maximum: 20``。

变异点（红→绿判别）：回退 core_tools 的 ``min(top_k, 20)`` 与 schema
``maximum`` → 两条用例均红。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_vector_search_clamps_top_k_to_20_in_es_request():
    """top_k=1000 时，发给 ES 的请求 size 不得超过 20。"""
    from app.agent.core_tools import vector_search
    from app.core.elasticsearch import ElasticsearchTools

    captured: dict = {}

    async def fake_search(es_query):
        captured["es_query"] = es_query
        return {"hits": {"hits": []}}

    pipeline = MagicMock()
    pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])

    with (
        patch("app.dependencies.get_rag_pipeline", return_value=pipeline),
        patch.object(ElasticsearchTools, "search_documents", AsyncMock(side_effect=fake_search)),
    ):
        await vector_search("查询", top_k=1000, organization_id=1)

    assert captured["es_query"]["size"] <= 20, (
        "top_k 超限必须钳制：ES 请求 size 不得随调用方任意放大（T6）"
    )


def test_vector_search_schema_declares_top_k_maximum():
    """工具 schema 必须声明 top_k 上限，避免模型给出超限参数。"""
    from app.agent.core_tools import vector_search  # noqa: F401  （触发工具注册）
    from app.agent.registry import tool_registry

    entry = tool_registry.get("vector_search")
    assert entry is not None
    top_k_schema = entry.parameters["properties"]["top_k"]
    assert top_k_schema.get("maximum") == 20
