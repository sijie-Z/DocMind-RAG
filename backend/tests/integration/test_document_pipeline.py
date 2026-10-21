"""End-to-end document pipeline test.

Requires MySQL/ES/Redis (and optionally Kafka). Skipped unless
``RUN_INTEGRATION=1`` is set, because these tests need real services.
"""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="set RUN_INTEGRATION=1 to run with MySQL/ES/Redis/Kafka",
)


@pytest.mark.asyncio
async def test_missing_document_returns_true_terminal():
    """#301 批 D（#30）：对象已不存在 = 消息已了结（响亮日志），返回 True。

    修前返回 False 与"处理失败"无法区分；consumer 侧对 False 走受限重投——
    文档已不存在时重投永远失败，只会把 worker 卡进重启循环。此处语义：
    "无需（也不可能）重投" → True（consumer 可安全提交）。
    """
    from app.worker.doc_processor import processor

    assert await processor.process("missing-document-id") is True
