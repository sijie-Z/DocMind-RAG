"""A3-P0：统一文档状态写入——INDEXED 前必须回读校验，FAILED 不得覆盖 INDEXED。

覆盖「demo 路径直接写状态」的收口：
- 任何把文档推进到 INDEXED 的写入都必须先经 ``verify_indexed`` 回读确认
  （「状态 = INDEXED ⟹ 可被检索」不变式）；
- 任何 FAILED 写入都必须拒绝覆盖已 INDEXED 的终态（终态回退事故的写侧防线）。

变异点：删掉 ``mark_document_indexed`` 里的 ``verify_indexed`` 等待 →
本文件第一个用例红（ES 零命中仍被写 INDEXED）；删掉 ``mark_document_failed``
的 INDEXED 守卫 → 终态回退用例红。
"""

from types import SimpleNamespace

import pytest

from app.models.document import DocumentStatus


class _FakeIndices:
    def __init__(self):
        self.refreshed = []

    async def refresh(self, index):
        self.refreshed.append(index)


class _FakeES:
    """按调用顺序吐出 count 值的最小 ES 替身（verify 连查三项）。"""

    def __init__(self, counts):
        self._counts = list(counts)
        self.indices = _FakeIndices()

    async def count(self, index=None, query=None):
        return {"count": self._counts.pop(0)}


def _doc(status=DocumentStatus.PENDING, parse_error=None):
    return SimpleNamespace(
        id="doc-a3",
        status=status,
        parse_error=parse_error,
        indexed_at=None,
    )


class TestMarkDocumentIndexed:
    @pytest.mark.asyncio
    async def test_refuses_indexed_when_es_verification_fails(self):
        """ES 查不到任何 chunk → 必须抛错且不写 INDEXED（demo 路径同口径）。"""
        from app.services.document_status import mark_document_indexed

        doc = _doc()
        fake = _FakeES([0])  # 第一项 count（document_id term）= 0

        with pytest.raises(RuntimeError, match="索引校验失败"):
            await mark_document_indexed(
                doc, expected_chunks=2, organization_id=1, verify_client=fake
            )

        assert doc.status == DocumentStatus.PENDING
        assert doc.indexed_at is None

    @pytest.mark.asyncio
    async def test_writes_indexed_only_after_verification_passes(self):
        """三项回读都通过后才写 INDEXED，并刷新 indexed_at。"""
        from app.services.document_status import mark_document_indexed

        doc = _doc(parse_error="旧错误")
        fake = _FakeES([2, 2, 2])

        indexed = await mark_document_indexed(
            doc, expected_chunks=2, organization_id=1, verify_client=fake
        )

        assert indexed == 2
        assert doc.status == DocumentStatus.INDEXED
        assert doc.indexed_at is not None
        assert doc.parse_error is None
        assert fake.indices.refreshed, "校验前必须先 refresh 索引"


class TestMarkDocumentFailed:
    def test_does_not_regress_indexed_document(self):
        """已 INDEXED 的终态不因晚到的失败写入被改写（返回 False 且留痕）。"""
        from app.services.document_status import mark_document_failed

        doc = _doc(status=DocumentStatus.INDEXED, parse_error=None)

        written = mark_document_failed(doc, "late failure")

        assert written is False
        assert doc.status == DocumentStatus.INDEXED
        assert doc.parse_error is None

    def test_marks_non_indexed_document_failed_with_reason(self):
        from app.services.document_status import mark_document_failed

        doc = _doc(status=DocumentStatus.PARSING)

        written = mark_document_failed(doc, "boom")

        assert written is True
        assert doc.status == DocumentStatus.FAILED
        assert doc.parse_error == "boom"
