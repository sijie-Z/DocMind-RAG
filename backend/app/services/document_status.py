"""文档索引状态统一写入（A3-P0 一致性收口）。

两条写侧不变式：

1. **INDEXED 只能在校验通过之后写入**：状态 = INDEXED 必须意味着该文档真的
   可被检索（``verify_indexed`` 回读三条：有 chunk、有带 embedding 的 chunk、
   organization_id 一致——issue #181 / #224）。
2. **FAILED 不得覆盖 INDEXED**：终态回退事故的写侧防线。晚到的失败（重投、
   兜底异常、并发 run）只能把非终态文档推进为 FAILED。

worker 主链（``app.worker.doc_processor``）复用 ``verify_indexed``；demo 种子
路径此前直接赋值 ``doc.status``，现在统一走本模块（``mark_document_indexed`` /
``mark_document_failed``），不再有绕过校验的旁路。
"""

import logging
from datetime import UTC, datetime

from app.core.config import settings
from app.core.elasticsearch import get_elasticsearch
from app.models.document import Document, DocumentStatus

logger = logging.getLogger(__name__)


async def verify_indexed(
    client, document_id: str, expected: int, *, organization_id: str
) -> int:
    """确认该文档的 chunk **真的**能被检索到；不能就抛错。

    三条不变式（issue #181 / #224）——「状态 = INDEXED」必须意味着文档可被检索：

    1. 该文档至少有一条 chunk 落在索引里（#181）；
    2. 其中至少一条带 **embedding 字段**（#224；`exists` 判据只证"存在"，
       不证"非退化" —— 零向量由写入路径保证不产生）：空值在 ES 里 `exists=false`，
       而向量通道硬过滤 `exists: embedding` —— 一条都没有 = 混合检索的向量臂
       永远查不到它；
    3. chunk 的 `organization_id` 与该文档一致（#224）：检索通道硬过滤组织 ——
       org 写错 = 谁都搜不到。

    为什么必须校验（issue #181）：**`_mark_success` 是无条件把状态置成 `INDEXED` 的** ——
    它不看 ES 里到底有没有东西。而「已索引」这个状态是运维、前端与监控判断
    「知识库到底能不能用」的唯一依据。

    **不要用「库会抛错」当判据。** 我最初在这里写的是"bulk 不抛错 ≠ 数据进去了"，
    但**实测不成立**：`async_bulk` 默认 `raise_on_error=True`，遇到 mapping 冲突会抛
    `BulkIndexError`（对抗评审 F1 指出，已实测复现）。那条因果故事当时没验证过。

    真正的理由是**不变式要在它被断言的地方强制**：
    「状态 = INDEXED ⟹ 这篇文档在 ES 里查得到」这条不变式，此前唯一的执行者是
    「bulk 库大概会抛错」这个假设。回读校验把它变成一条可执行的断言 ——
    库的行为变了、mapping 改了、将来有人把 bulk 换成别的写法，都不再能让它悄悄失效。

    返回实际索引到的条数（便于调用方记录）。
    """
    index_name = settings.ELASTICSEARCH_INDEX_NAME
    await client.indices.refresh(index=index_name)

    res = await client.count(
        index=index_name, query={"term": {"document_id": document_id}}
    )
    indexed = int(res.get("count", 0))

    if indexed == 0:
        raise RuntimeError(
            f"索引校验失败：文档 {document_id} 的 {expected} 个 chunk 已提交给 Elasticsearch，"
            f"但刷新后在索引 {index_name} 里查不到任何一条。"
            f"拒绝把该文档标记为已索引 —— 否则会出现「状态已索引、检索却查不到」。"
        )

    res_emb = await client.count(
        index=index_name,
        query={
            "bool": {
                "filter": [
                    {"term": {"document_id": document_id}},
                    {"exists": {"field": "embedding"}},
                ]
            }
        },
    )
    with_embedding = int(res_emb.get("count", 0))
    if with_embedding == 0:
        raise RuntimeError(
            f"索引校验失败：文档 {document_id} 在索引 {index_name} 里有 {indexed} 条 chunk，"
            f"但**没有任何一条带 embedding 字段** —— 向量通道硬过滤 exists:embedding，"
            f"这些内容对混合检索的向量臂永远不可见（#224）。拒绝把该文档标记为已索引。"
        )

    res_org = await client.count(
        index=index_name,
        query={
            "bool": {
                "filter": [
                    {"term": {"document_id": document_id}},
                    {"term": {"organization_id": str(organization_id)}},
                ]
            }
        },
    )
    with_org = int(res_org.get("count", 0))
    if with_org == 0:
        raise RuntimeError(
            f"索引校验失败：文档 {document_id} 的 chunk 的 organization_id 与文档不一致"
            f"（期望 {organization_id!r}）—— 检索通道硬过滤组织，谁都搜不到它（#224）。"
            f"拒绝把该文档标记为已索引。"
        )

    if indexed != expected:
        # 部分落库：文档确实可被检索到，但内容不完整。不阻断，但必须留痕。
        logger.warning(
            "[DOC_STATE] 索引条数与预期不符 document_id=%s 预期=%d 实际=%d",
            document_id,
            expected,
            indexed,
        )
    if with_embedding < indexed or with_org < indexed:
        # 部分 chunk 对某条检索引擎通道不可见：文档整体可检索（有带向量的、
        # 组织匹配的 chunk），但不完整。不阻断，必须留痕。
        logger.warning(
            "[DOC_STATE] 索引可检索性不完整 document_id=%s 总数=%d 带embedding=%d 组织匹配=%d",
            document_id,
            indexed,
            with_embedding,
            with_org,
        )
    return indexed


async def mark_document_indexed(
    document: Document,
    *,
    expected_chunks: int,
    organization_id: int | str,
    verify_client=None,
) -> int:
    """回读校验通过后把文档写为 INDEXED；校验失败抛错且不改状态。

    ``verify_client`` 仅用于测试注入；缺省取全局 ES 客户端。
    """
    client = verify_client if verify_client is not None else await get_elasticsearch()
    indexed = await verify_indexed(
        client, document.id, expected_chunks, organization_id=str(organization_id)
    )
    document.status = DocumentStatus.INDEXED
    document.indexed_at = datetime.now(UTC)
    document.parse_error = None
    return indexed


def mark_document_failed(document: Document, error: str) -> bool:
    """把文档如实推进为 FAILED；已 INDEXED 的终态不覆盖（返回 False 并留痕）。"""
    if document.status == DocumentStatus.INDEXED:
        logger.warning(
            "[DOC_STATE] 拒绝 FAILED 写入：文档已是 INDEXED document_id=%s 原因=%s",
            document.id,
            error,
        )
        return False
    document.status = DocumentStatus.FAILED
    document.parse_error = error
    return True
