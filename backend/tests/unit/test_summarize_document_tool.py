"""summarize_document 工具：空正文（推理预算吃满）必须回退原文预览。

第四波实机诊断（复验于 e5b5e5b）：deepseek-v4-flash 为推理模型——max_tokens=500
时 reasoning_content 吃满预算、正文为空（实测 finish_reason=length、
content=''、reasoning_content 1010 字符）。修前空正文被当成摘要直返
"Summary generation failed."，executor 据此放弃总结（@引用链路最后一厘米：
"没有拿到可总结的正文"）。修后：空正文视为失败 → 与异常路径同款的原文预览
兜底（诚实标注不可用 + 给内容），executor 仍能完成总结。

变异点：空正文不再走兜底（还原 "Summary generation failed." 直返）→ 红。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent import core_tools
from app.core.elasticsearch import ElasticsearchTools

HITS = {"hits": {"hits": [
    {"_source": {"content": "# 贡献指南正文第一段", "filename": "CONTRIBUTING.md"}},
    {"_source": {"content": "## 行为准则：Contributor Covenant", "filename": "CONTRIBUTING.md"}},
]}}


@pytest.fixture(autouse=True)
def _stub_es(monkeypatch):
    monkeypatch.setattr(ElasticsearchTools, "search_documents", AsyncMock(return_value=HITS))


def _pipeline_with(content: str, finish_reason: str = "stop"):
    resp = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=content, reasoning_content="推理……"),
            finish_reason=finish_reason,
        )],
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=500),
    )
    pipeline = MagicMock()

    async def _create(**kwargs):
        return resp

    pipeline.openai_client.chat.completions.create = _create
    return pipeline


@pytest.mark.asyncio
async def test_empty_llm_content_falls_back_to_raw_preview(monkeypatch):
    import app.dependencies as deps

    monkeypatch.setattr(
        deps, "get_rag_pipeline", lambda: _pipeline_with("", finish_reason="length"),
    )

    out = await core_tools.summarize_document(document_id="d-x", organization_id=1)

    assert "Summary generation failed." not in out, "空正文不得当摘要直返"
    assert "贡献指南正文第一段" in out, "空正文必须回退到原文预览（保住可总结性）"
    assert "summarization unavailable" in out, "兜底必须诚实标注降级"


@pytest.mark.asyncio
async def test_normal_summary_returned(monkeypatch):
    import app.dependencies as deps

    monkeypatch.setattr(
        deps, "get_rag_pipeline", lambda: _pipeline_with("主题：贡献指南；要点：行为准则。"),
    )

    out = await core_tools.summarize_document(document_id="d-x", organization_id=1)

    assert "主题：贡献指南；要点：行为准则。" in out
    assert "CONTRIBUTING.md" in out
