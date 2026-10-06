"""索引回读校验（issue #181）。

## 背景

`DocumentProcessor._replace_document_state` 在 `bulk_index_documents` 之后
**无条件进入 `_mark_success`**，而 `_mark_success` 又**无条件**把文档状态置成 `INDEXED`。

问题是：**`bulk` 不抛错 ≠ 数据进去了。** bulk 的部分失败、mapping 冲突、
写入被拒，都可能让 chunk 一条都没落 —— 而状态照样显示"已索引"。
真实的临床表现就是：**状态说索引成功，检索却恒返回 0 条，且没有任何信号。**

## 本文件钉住的（真实 Elasticsearch，`RUN_INTEGRATION=1` 门控）

1. **一条都没落时必须抛错** —— 抛错会让调用方的 `except` 分支把文档标成 FAILED
   并带上原因（`doc_processor.process` 的 `except Exception → _mark_failed`）。
2. 全部落库时正常通过，并返回实际条数。
3. 部分落库时不阻断（文档确实可检索），但要留 WARNING。

## 变异验证（证明用例 1 有鉴别力）

把 `_verify_indexed` 里 `if indexed == 0: raise RuntimeError(...)` 整段删掉
（即退回"不校验"的旧行为），用例 1 必须变红：它将无法捕捉到
"预期抛错却没抛"。

用独立索引名跑，绝不碰真实索引。
"""

import os
import uuid

import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.elasticsearch import _holder, get_elasticsearch
from app.worker.doc_processor import _verify_indexed

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 Elasticsearch（设置 RUN_INTEGRATION=1，并确保 ES 可达）",
)

_DOC_ID = "doc-under-test"


@pytest_asyncio.fixture
async def test_index(monkeypatch):
    """建一个空的独立索引；用完删掉。

    每个用例都重建 ES 客户端 —— pytest-asyncio 每用例一个新事件循环，
    客户端连接跨 loop 复用会 `RuntimeError: Event loop is closed`
    （同 DB 侧用 NullPool 的理由，见 `test_delete_document_children.py:53`）。
    """
    monkeypatch.setattr(_holder, "_client", None)

    index_name = f"docmind_test_verify_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "ELASTICSEARCH_INDEX_NAME", index_name)

    client = await get_elasticsearch()
    # 不显式 create：`get_elasticsearch()` 会触发 `_holder.initialize()`，
    # 而 initialize 内部本身就会调 `create_index_if_not_exists` 把索引建出来。
    # 自动建的 mapping 已含 `document_id: keyword`，足够本文件用。
    assert await client.indices.exists(index=index_name), "initialize 应当已经建好索引"

    async def _seed(n: int) -> None:
        for i in range(n):
            await client.index(
                index=index_name,
                id=f"{_DOC_ID}-{i}",
                document={"document_id": _DOC_ID, "chunk_index": i},
                refresh=True,
            )

    try:
        yield client, index_name, _seed
    finally:
        if await client.indices.exists(index=index_name):
            await client.indices.delete(index=index_name)


# ══════════════════════════════════════════════════════════════════════
# 不变式 1（核心）：一条都没落 → 必须抛错
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_raises_when_nothing_landed(test_index):
    client, index_name, _seed = test_index

    # 索引存在但没有任何属于该文档的 chunk —— 正是 bulk 静默失败的形态
    with pytest.raises(RuntimeError) as ei:
        await _verify_indexed(client, _DOC_ID, expected=3)

    msg = str(ei.value)
    assert "索引校验失败" in msg
    assert _DOC_ID in msg, "错误信息要能定位到是哪篇文档"
    assert index_name in msg, "错误信息要带上索引名"
    assert "拒绝把该文档标记为已索引" in msg, "要说清后果，而不是只报一个计数"


# ══════════════════════════════════════════════════════════════════════
# 不变式 2：全部落库 → 通过并返回条数
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_passes_when_all_chunks_present(test_index):
    client, _index_name, seed = test_index
    await seed(3)

    assert await _verify_indexed(client, _DOC_ID, expected=3) == 3


@pytest.mark.asyncio
async def test_passes_when_actual_exceeds_expected(test_index):
    """实际比预期多不该算失败 —— 校验的目标是"有没有落进去"，不是精确对账。"""
    client, _index_name, seed = test_index
    await seed(5)

    assert await _verify_indexed(client, _DOC_ID, expected=3) == 5


# ══════════════════════════════════════════════════════════════════════
# 不变式 3：部分落库 → 不阻断，但要留痕
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_partial_index_warns_but_does_not_block(test_index, caplog):
    client, _index_name, seed = test_index
    await seed(2)

    import logging

    with caplog.at_level(logging.WARNING, logger="app.worker.doc_processor"):
        result = await _verify_indexed(client, _DOC_ID, expected=3)

    assert result == 2, "部分落库不该抛错 —— 文档确实是可检索的"
    assert any("索引条数与预期不符" in r.getMessage() for r in caplog.records), (
        "部分落库必须留 WARNING，否则没人知道索引不完整"
    )
