"""issue #250 第 1/2 条的收账测试：retriever 站的 LLM 改写计数。

- **过计修复（第 1 条）**：`_keyword_hits` 里 LLM 改写变体此前在 `rewrite_limit`
  截断**之前**计数 —— 被截掉、从未执行的变体也入了账（单次最多过计 2 条），
  与本地站（`query_processor.rewrite_query_candidates` 在自身截断后计数）
  口径不自洽。修复后按"截断后仍存活、且由 LLM 追加的变体数"计数。
- **站覆盖（第 2 条）**：此前把 retriever 侧 inc 整个删掉，全量套件仍全绿
  （无任何用例经过该路径）。本文件以假 openai_client + 假 ES 检索桩补齐
  「合并 → 截断 → 计数」路径的覆盖，并顺带钉住"实际执行的检索次数 =
  截断后的最终条数"这一现场事实。
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.rag.retriever as retriever_mod
from app.core.config import settings
from app.rag.retriever import HybridRetriever

# 本地改写站对该查询产出 2 条候选（原查询 + 1 个去停用词变体）——
# 与 test_metrics_contracts_alignment 的用例前置一致。
QUERY = "请 帮我 总结一下这份文档"


def _fake_llm_client(variants: list[str]):
    """假 OpenAI 兼容客户端：chat.completions.create 返回 json_object 形态。"""
    client = MagicMock()

    async def _create(*, model, messages, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.content = json.dumps({"queries": variants}, ensure_ascii=False)
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _patch_metric_and_es(monkeypatch):
    spy = MagicMock()
    monkeypatch.setattr(retriever_mod, "RAG_QUERY_REWRITE_TOTAL", spy)
    search = AsyncMock(return_value={"hits": {"hits": []}})
    monkeypatch.setattr(retriever_mod.ElasticsearchTools, "search_documents", search)
    # 与默认值显式对齐，隔离 .env / 环境差异对截断边界的扰动。
    monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", True)
    monkeypatch.setattr(settings, "RAG_QUERY_REWRITE_COUNT", 4)
    return spy, search


@pytest.mark.asyncio
async def test_llm_rewrite_counted_after_truncation(monkeypatch):
    """截断场景：本地 2 条 + LLM 3 条新增 → 截到 4 条 → 只计存活的 2 条。

    变异点：把计数挪回截断前（旧 `added` 累计行为）→ inc 收到 3，本用例红。
    """
    spy, search = _patch_metric_and_es(monkeypatch)
    retriever = HybridRetriever(
        openai_client=_fake_llm_client(["变体一", "变体二", "变体三"]),
    )

    await retriever._keyword_hits(QUERY, [], candidate_size=10)

    assert search.await_count == 4, f"实际执行的检索次数应为截断后的 4 次，实际 {search.await_count}"
    assert spy.inc.call_args_list == [((2,), {})], (
        f"只应计入存活的新增变体 2 条（5 条候选被截到 4），实际调用 {spy.inc.call_args_list}"
    )


@pytest.mark.asyncio
async def test_llm_rewrite_counted_without_truncation(monkeypatch):
    """未触发截断：本地 2 条 + LLM 1 条新增 → 计 1，检索执行 3 次。"""
    spy, search = _patch_metric_and_es(monkeypatch)
    retriever = HybridRetriever(openai_client=_fake_llm_client(["变体一"]))

    await retriever._keyword_hits(QUERY, [], candidate_size=10)

    assert search.await_count == 3, f"应执行 3 次检索，实际 {search.await_count}"
    spy.inc.assert_called_once_with(1)


@pytest.mark.asyncio
async def test_llm_rewrite_duplicate_and_failure_not_counted(monkeypatch):
    """LLM 返回的全是本地已有候选/重复项时，一条都不计（也不多检索）。"""
    spy, search = _patch_metric_and_es(monkeypatch)
    retriever = HybridRetriever(
        # 原查询本身与本地第二个候选都在 LLM 返回里 —— 合并去重后零新增
        openai_client=_fake_llm_client([QUERY, "总结一下这份文档"]),
    )

    await retriever._keyword_hits(QUERY, [], candidate_size=10)

    assert search.await_count == 2, f"应执行 2 次检索，实际 {search.await_count}"
    spy.inc.assert_not_called()
