"""issue #193 回归：MMR 不得静默丢弃无 embedding 候选；debug 留计数。

修前行为（`develop @ e5b2eb1` 起未变）：

- `retrieve()` 的 MMR 候选列表用 `if h.get("_source", {}).get("embedding")` 过滤
  ——缺向量的候选被**静默剔除**；
- MMR 结果**整表替换** fused，无任何命中/丢弃计数——一次检索的结果集被谁改过、
  改了哪几条，debug 载荷里查不到。

修复契约（本文件钉住的语义）：

- 缺 embedding 的候选不再消失：MMR 选中项在前、无向量候选按原 fused 序**确定性
  追加**在后（它们仍带 keyword 臂信息），由 `_post_process` 决定是否保留；
- `debug_info["mmr"]` 记录 `{applied, reason, lambda, candidates, with_embedding,
  selected, no_embedding_appended, not_selected}`；
- 条号直查跳过 MMR / MMR 关闭 / 无任何带向量候选时，fused 序保持修复前语义。

变异点（任一还原 → 下列用例红）：

- 恢复「缺 embedding 即过滤 + 整表替换」→
  ``test_embeddingless_candidate_survives_mmr``、
  ``test_embeddingless_candidates_are_appended_in_fused_order``；
- 删掉 `debug_info["mmr"]` 计数 → ``test_mmr_debug_counts``；
- 无向量候选插到选中项之前 / 排序不稳定 →
  ``test_embeddingless_candidates_are_appended_in_fused_order``；
- 全部候选都无向量时不再保留原 fused 序 →
  ``test_all_candidates_without_embedding_keep_fused_order``。
"""
import pytest

from app.core.config import settings
from app.rag.query_processor import QueryComplexityClassifier
from app.rag.retriever import HybridRetriever

TERM_QUERY = "报销流程说明"  # 非条号直查；查询术语与三条正文都强匹配


def _hit(chunk_id: str, text: str, score: float, embedding: list[float] | None,
         doc_id: str) -> dict:
    source = {"content": text, "filename": f"{chunk_id}.pdf", "document_id": doc_id}
    if embedding is not None:
        source["embedding"] = embedding
    return {"_id": chunk_id, "_score": score, "_source": source}


def _wire_hybrid(monkeypatch, retriever, hits, query_vector):
    async def fake_kw(query, filters, candidate_size):
        return [dict(h) for h in hits]

    async def fake_vec(query, filters, candidate_size, enable_hyde=True):
        # 生产形态：缺 embedding 的 chunk 不可能被向量臂召回（ES 查询带
        # exists(embedding) 过滤），向量臂只返回带向量的命中（评审 m5）。
        embedded = [dict(h) for h in hits if h.get("_source", {}).get("embedding")]
        return embedded, query_vector, None

    monkeypatch.setattr(retriever, "_keyword_hits", fake_kw)
    monkeypatch.setattr(retriever, "_vector_hits", fake_vec)
    monkeypatch.setattr(
        QueryComplexityClassifier,
        "get_strategy",
        classmethod(lambda cls, _q: "hybrid"),
    )
    monkeypatch.setattr(settings, "RAG_ENABLE_MMR", True)


def _three_hits(mode: str = "partial") -> list[dict]:
    """mode="partial"：c1/c3 有向量、中间 c2 无向量；mode="none"：全部无向量。"""
    assert mode in {"partial", "none"}
    c1_emb = [1.0, 0.0, 0.0, 0.0] if mode == "partial" else None
    c3_emb = [0.0, 1.0, 0.0, 0.0] if mode == "partial" else None
    return [
        _hit("c1", "报销流程说明：员工先提交申请，再由主管审批。", 0.9, c1_emb, "docA"),
        # 中间这条没有向量：修复前会被 MMR 过滤整条消失（gold 场景）。
        _hit("c2", "报销流程说明：发票必须与实际消费一致。", 0.8, None, "docB"),
        _hit("c3", "报销流程说明：财务复核通过后安排付款。", 0.7, c3_emb, "docC"),
    ]


@pytest.mark.asyncio
async def test_embeddingless_candidate_survives_mmr(monkeypatch):
    """缺 embedding 的候选不得因 MMR 而整条消失。"""
    retriever = HybridRetriever()
    _wire_hybrid(monkeypatch, retriever, _three_hits(), [1.0, 0.0, 0.0, 0.0])

    docs, _qv, _dbg, _deg = await retriever.retrieve(TERM_QUERY, 1, top_k=3)

    chunk_ids = [d["chunk_id"] for d in docs]
    assert "c2" in chunk_ids, (
        f"无向量候选被 MMR 静默剔除（当前 {chunk_ids}）；应作为确定性回退保留"
    )


@pytest.mark.asyncio
async def test_embeddingless_candidates_are_appended_in_fused_order(monkeypatch):
    """选中项在前、无向量候选按原 fused 序追加（确定性回退，不参与 MMR 打分）。"""
    retriever = HybridRetriever()
    _wire_hybrid(monkeypatch, retriever, _three_hits(), [1.0, 0.0, 0.0, 0.0])

    docs, _qv, _dbg, _deg = await retriever.retrieve(TERM_QUERY, 1, top_k=3)

    assert [d["chunk_id"] for d in docs] == ["c1", "c3", "c2"], (
        "MMR 选中项（c1/c3）必须在前，无向量候选（c2）按 fused 序追加在后"
    )


@pytest.mark.asyncio
async def test_mmr_debug_counts(monkeypatch):
    """debug 载荷必须能看出 MMR 是否生效、输入/选中/保留各多少。"""
    retriever = HybridRetriever()
    _wire_hybrid(monkeypatch, retriever, _three_hits(), [1.0, 0.0, 0.0, 0.0])

    _docs, _qv, dbg, _deg = await retriever.retrieve(TERM_QUERY, 1, top_k=3, debug=True)

    assert dbg is not None and "mmr" in dbg, "debug 载荷缺少 mmr 计数（#193）"
    mmr = dbg["mmr"]
    assert mmr["applied"] is True
    assert mmr["candidates"] == 3
    assert mmr["with_embedding"] == 2
    assert mmr["selected"] == 2
    assert mmr["no_embedding_appended"] == 1
    assert mmr["not_selected"] == 0
    assert mmr["lambda"] == pytest.approx(max(0.0, min(1.0, settings.RAG_MMR_LAMBDA)))


@pytest.mark.asyncio
async def test_all_candidates_without_embedding_keep_fused_order(monkeypatch):
    """全部候选都无向量：MMR 无输入，保持原 fused 序（与修复前一致）。"""
    retriever = HybridRetriever()
    _wire_hybrid(monkeypatch, retriever, _three_hits(mode="none"),
                 [1.0, 0.0, 0.0, 0.0])

    docs, _qv, dbg, _deg = await retriever.retrieve(TERM_QUERY, 1, top_k=3, debug=True)

    assert [d["chunk_id"] for d in docs] == ["c1", "c2", "c3"]
    assert dbg["mmr"]["applied"] is False
    assert dbg["mmr"]["reason"] == "no_embedded_candidates"
    assert dbg["mmr"]["no_embedding_appended"] == 3


@pytest.mark.asyncio
async def test_clause_lookup_skip_is_visible_in_debug(monkeypatch):
    """条号直查跳过 MMR 是既有决策；debug 必须如实给出跳过原因。"""
    retriever = HybridRetriever()
    _wire_hybrid(monkeypatch, retriever, _three_hits(), [1.0, 0.0, 0.0, 0.0])

    _docs, _qv, dbg, _deg = await retriever.retrieve("第3条规定了什么？", 1, top_k=3, debug=True)

    assert dbg["mmr"]["applied"] is False
    assert dbg["mmr"]["reason"] == "clause_lookup"
