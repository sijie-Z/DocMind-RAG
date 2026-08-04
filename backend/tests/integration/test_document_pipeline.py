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
async def test_missing_document_returns_false():
    from app.worker.doc_processor import processor

    assert await processor.process("missing-document-id") is False
