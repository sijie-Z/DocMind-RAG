"""索引回读校验（issue #181 建立，issue #224 加严）。

## 背景

`DocumentProcessor._replace_document_state` 在 `bulk_index_documents` 之后
调用 `_mark_success`；A3-P0 起 `_mark_success` 为条件写入（已 INDEXED 不覆盖），
修前它**无条件**把文档状态置成 `INDEXED`。

三条不变式（「状态 = INDEXED ⟹ 文档可被检索到」）：
1. 至少一条 chunk 落了（#181）；
2. 至少一条带 **embedding 字段**（#224；`exists` 判据只证"存在"，不证"非退化"）——
   向量通道硬过滤 `exists: embedding`，没有 embedding 的 chunk 对混合检索的向量臂永远不可见；
3. `organization_id` 与文档一致（#224）—— 检索通道硬过滤组织，org 写错 = 谁都搜不到。

> **一处更正（#181 轮）**：我最初写过「`bulk` 不抛错 ≠ 数据进去了」，那条因果故事
> **实测不成立** —— `async_bulk` 默认 `raise_on_error=True`，遇到 mapping 冲突会抛
> `BulkIndexError`（对抗评审 F1 指出，已实测复现）。
> 回读校验的价值不在"抓 bulk 的静默失败"，而在**把不变式从"依赖库的行为"
> 变成"可执行的断言"**。

## 本文件钉住的（真实 Elasticsearch，`RUN_INTEGRATION=1` 门控）

- 一条都没落 → 抛错（调用方 `except` 会把文档标 FAILED 并带原因）；
- **他人文档的 chunk 不得为本文档"挡枪"** —— 校验必须按 `document_id` 过滤（#224）；
- 一条带 embedding 的都没有 → 抛错（#224）；
- `organization_id` 全不匹配 → 抛错（#224）；
- 全部落库 → 通过并返回条数；
- 部分落库 → 不阻断但 WARNING；部分缺向量 → 不阻断但 WARNING（#224）。

## 变异验证（判定力，构造性论证 + CI 执行）

- 删掉 `if indexed == 0: raise ...` → 「一条都没落」用例必红；
- 删掉 embedding 段 → 「无 embedding」用例必红：**旧实现只按 document_id 计数，
  该场景 count=3≠0，必不抛错，`pytest.raises` 收不到异常即红**；
- 删掉 org 段 → 「org 不匹配」用例同理必红。

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


def _es_reachable() -> bool:
    """CI 的 integration job 只提供部分外部服务；#217 后已带 ES 服务容器。

    保留可达性守卫：在无 ES 的环境上整体 skip，而不是把文件跑成 ERROR（ERROR 是红的）。
    `RUN_INTEGRATION` 门只说明"允许跑集成测试"，不等于"ES 一定在"。
    """
    import socket
    from urllib.parse import urlparse

    for host in settings.ELASTICSEARCH_HOSTS:
        u = urlparse(host)
        try:
            with socket.create_connection((u.hostname, u.port or 9200), timeout=2):
                return True
        except OSError:
            continue
    return False


if not _es_reachable():
    pytestmark = [
        pytestmark,
        pytest.mark.skip(reason="Elasticsearch 不可达（本地未起 ES 时整体跳过）"),
    ]

_DOC_ID = "doc-under-test"
_ORG = "1"


def _embedding() -> list[float]:
    """与索引 mapping 的 dense_vector dims 一致的向量（维度必须匹配，否则 ES 拒绝写入）。"""
    return [0.1] * int(settings.VECTOR_DIMENSION)


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
    assert await client.indices.exists(index=index_name), "initialize 应当已经建好索引"

    async def _seed(
        n: int,
        *,
        doc_id: str = _DOC_ID,
        organization_id: str | None = _ORG,
        with_embedding: bool = True,
        start: int = 0,
    ) -> None:
        """写入形态与 `_replace_document_state` 的真实写入保持一致（#224）。
        `start` 用于在同一个文档下追加第二段（如"部分缺向量"的构造）。"""
        for i in range(start, start + n):
            src: dict = {"document_id": doc_id, "chunk_index": i}
            if organization_id is not None:
                src["organization_id"] = organization_id
            if with_embedding:
                src["embedding"] = _embedding()
            await client.index(
                index=index_name,
                id=f"{doc_id}-{i}",
                document=src,
                refresh=True,
            )

    try:
        yield client, index_name, _seed
    finally:
        if await client.indices.exists(index=index_name):
            await client.indices.delete(index=index_name)


# ══════════════════════════════════════════════════════════════════════
# 不变式 1：一条都没落 → 必须抛错
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_raises_when_nothing_landed(test_index):
    client, index_name, _seed = test_index

    # 索引存在但没有任何属于该文档的 chunk —— 正是 bulk 静默失败的形态
    with pytest.raises(RuntimeError) as ei:
        await _verify_indexed(client, _DOC_ID, expected=3, organization_id=_ORG)

    msg = str(ei.value)
    assert "索引校验失败" in msg
    assert _DOC_ID in msg, "错误信息要能定位到是哪篇文档"
    assert index_name in msg, "错误信息要带上索引名"
    assert "拒绝把该文档标记为已索引" in msg, "要说清后果，而不是只报一个计数"


@pytest.mark.asyncio
async def test_other_documents_do_not_mask_missing_chunks(test_index):
    """#224：索引里已有别人的文档时，本文档一条没落也必须抛错
    —— 校验必须按 document_id 过滤，不能被「索引非空」蒙混过关。"""
    client, _index_name, seed = test_index
    await seed(3, doc_id="other-document", organization_id="2")

    with pytest.raises(RuntimeError) as ei:
        await _verify_indexed(client, _DOC_ID, expected=2, organization_id=_ORG)
    assert "查不到任何一条" in str(ei.value)


# ══════════════════════════════════════════════════════════════════════
# 不变式 2（#224）：没有任何一条带 embedding → 必须抛错
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_raises_when_no_chunk_has_embedding(test_index):
    client, _index_name, seed = test_index
    await seed(3, with_embedding=False)  # 落库了，但没有任何向量

    with pytest.raises(RuntimeError) as ei:
        await _verify_indexed(client, _DOC_ID, expected=3, organization_id=_ORG)
    assert "embedding" in str(ei.value), "错误信息要说明是向量缺失"


# ══════════════════════════════════════════════════════════════════════
# 不变式 3（#224）：organization_id 全不匹配 → 必须抛错
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_raises_when_organization_mismatch(test_index):
    client, _index_name, seed = test_index
    await seed(3, organization_id="999")  # 组织写错 = 谁都搜不到

    with pytest.raises(RuntimeError) as ei:
        await _verify_indexed(client, _DOC_ID, expected=3, organization_id=_ORG)
    assert "organization_id" in str(ei.value)


# ══════════════════════════════════════════════════════════════════════
# 通过路径：全部落库 → 通过并返回条数
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_passes_when_all_chunks_present(test_index):
    client, _index_name, seed = test_index
    await seed(3)

    assert await _verify_indexed(client, _DOC_ID, expected=3, organization_id=_ORG) == 3


@pytest.mark.asyncio
async def test_passes_when_actual_exceeds_expected(test_index):
    """实际比预期多不该算失败 —— 校验的目标是"有没有落进去"，不是精确对账。"""
    client, _index_name, seed = test_index
    await seed(5)

    assert await _verify_indexed(client, _DOC_ID, expected=3, organization_id=_ORG) == 5


# ══════════════════════════════════════════════════════════════════════
# 部分落库 / 部分可检索 → 不阻断，但要留痕
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_partial_index_warns_but_does_not_block(test_index, caplog):
    client, _index_name, seed = test_index
    await seed(2)

    import logging

    with caplog.at_level(logging.WARNING, logger="app.worker.doc_processor"):
        result = await _verify_indexed(client, _DOC_ID, expected=3, organization_id=_ORG)

    assert result == 2, "部分落库不该抛错 —— 文档确实是可检索的"
    assert any("索引条数与预期不符" in r.getMessage() for r in caplog.records), (
        "部分落库必须留 WARNING，否则没人知道索引不完整"
    )


@pytest.mark.asyncio
async def test_partial_missing_embedding_warns_but_does_not_block(test_index, caplog):
    """#224：一半 chunk 没向量 —— 文档整体可检索（有带向量的 chunk），不阻断但留痕，
    且**不能误报**成"条数与预期不符"（条数是对的，缺的是向量）。"""
    client, _index_name, seed = test_index
    await seed(2)
    await seed(2, with_embedding=False, start=2)

    import logging

    with caplog.at_level(logging.WARNING, logger="app.worker.doc_processor"):
        result = await _verify_indexed(client, _DOC_ID, expected=4, organization_id=_ORG)

    assert result == 4, "部分缺向量不该抛错 —— 还有带向量的 chunk 可被向量臂检索"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("可检索性不完整" in m for m in msgs), "部分缺向量必须留痕"
    assert not any("索引条数与预期不符" in m for m in msgs), (
        "条数本身是对的（4/4），不该误报条数不符 —— 两种告警的触发条件必须分开"
    )
