"""issue #186 回归：LLM 提示词正文单一来源 `prompt_text` + 按语义句截断。

修前两处缺陷（`develop @ e5b2eb1` 起未变）：

① `HybridRetriever._build_snippet(max_len=260)` 找不到 query 字面量时直接
   `text[:max_len]` 硬切 chunk 头部——正确答案在 chunk 后半部分就永远进不了
   提示词；
② `RAGPipeline.chat_stream` 组装提示词用 `(snippet or text)[:3000]`——snippet
   非空即短路，`compress_context_list` 压的是 `text` 全文，产物**从未被使用**；
   chat / agent / evaluator 多个消费点各自 `snippet or text` 分叉。

修复契约（本文件钉住的语义）：

- `HybridRetriever._post_process` 同一份结果同时产出 `prompt_text`（完整 chunk
  正文，提示词唯一来源）与 `snippet`（展示用；超限走 context_compressor 的
  规则式句子抽取，不再硬切头部）；
- `context_compressor.get_prompt_text()` 是提示词正文的唯一读取入口
  （`prompt_text` → `text` → `content` → `snippet` 兼容兜底）；
- `compress_context_list` 读 `prompt_text` 并把压缩结果写回 `prompt_text`
  （`text` 同步保留，兼容旧调用方）——压缩不再被 snippet 短路；
- `chat_stream` 提示词只读 `prompt_text`；超过可配置上限
  `RAG_PROMPT_TEXT_MAX_CHARS` 时按句子抽取（不硬切头部）；
- agent `core_tools` / `loop` 与 `evaluator` 一律 `prompt_text`/全文优先，
  snippet 仅作展示。

变异点（任一还原 → 下列用例红）：

- `_post_process` 不写 `prompt_text` → ``test_post_process_exposes_prompt_text``；
- `_build_snippet` 未命中 query 时还原 `text[:max_len]` →
  ``test_snippet_uses_semantic_sentences_when_query_literal_absent``；
- `chat_stream` 还原 `snippet or text` →
  ``test_chat_stream_prompt_uses_prompt_text_not_snippet``；
- `compress_context_list` 还原只读写 `text` →
  ``test_compressor_reads_prompt_text_and_keeps_snippet``；
- 提示词第二段上限还原硬编码 `[:3000]`（不读配置 / 不按句截断）→
  ``test_prompt_cap_is_configurable_and_sentence_aware``（评审 M1：文本 >3000 字、
  KEY 在 3000 之后、中段标记在 200~3000 之间，硬编码/忽略配置两种回退都红）；
- `compress` 的负切片（max_chars<4）→ ``test_compress_never_exceeds_tiny_caps``；
- 零命中退化回 30% 头部 / 输出出现半句硬切 →
  ``test_compress_without_relevant_sentences_fills_sentence_budget``；
- PII 掩码不跟随 prompt_text / 未压缩路径就地改写调用方 →
  ``test_chat_stream_masks_prompt_text``（enable_compression 参数化，
  False 分支锁浅拷贝）；
- agent 工具还原 snippet 优先 → ``test_agent_tool_prefers_prompt_text``；
- agent loop 还原 snippet 优先 → ``test_agent_loop_context_prefers_prompt_text``；
- evaluator 还原 snippet 优先 → ``test_evaluator_context_prefers_prompt_text``。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import settings
from app.rag.pipeline import RAGPipeline
from app.rag.retriever import HybridRetriever

# 关键句只出现在 chunk 尾部/中段；查询词是它的真子集（查询字面量不在文本中，
# 但查询术语命中），用于验证「未命中字面量时按语义句选句」而不是硬切头部。
KEY = "审计日志的保留期限为五年"
QUERY = "审计日志保留期限"
FILLER = "本节介绍系统总体架构与设计原则，以及部署环境的总体说明。"


# ── 假 LLM 客户端：捕获提示词、返回一个 chunk ─────────────────────────────────


class _FakeCompletions:
    def __init__(self, captured: list[dict]):
        self.captured = captured

    async def create(self, **kwargs):
        self.captured.append(kwargs)

        async def _stream():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="回答"))])

        return _stream()


class _FakeClient:
    def __init__(self, captured: list[dict]):
        self.chat = SimpleNamespace(completions=_FakeCompletions(captured))


def _run_chat_stream(
    context: list[dict],
    *,
    monkeypatch,
    max_chars: int | None = None,
    enable_compression: bool = True,
    enable_masking: bool = False,
    mask_sink: dict | None = None,
) -> str:
    """跑一次 chat_stream，返回实际送给 LLM 的 user message 文本。"""
    if max_chars is not None:
        monkeypatch.setattr(settings, "RAG_PROMPT_TEXT_MAX_CHARS", max_chars, raising=False)
    captured: list[dict] = []
    pipeline = RAGPipeline(openai_client=_FakeClient(captured))
    import asyncio

    async def _consume():
        return [
            chunk
            async for chunk in pipeline.chat_stream(
                QUERY,
                context,
                enable_compression=enable_compression,
                enable_masking=enable_masking,
                mask_mapping_sink=mask_sink,
            )
        ]

    asyncio.run(_consume())
    assert captured, "chat_stream 必须调用 LLM"
    messages = captured[0]["messages"]
    return messages[-1]["content"]


def _hit(chunk_id: str, text: str, score: float = 1.0) -> dict:
    return {
        "_id": chunk_id,
        "_score": score,
        "has_keyword": True,
        "has_vector": True,
        "_source": {
            "content": text,
            "filename": f"{chunk_id}.pdf",
            "document_id": chunk_id,
        },
    }


# ── retriever：单一来源 + snippet 语义选句 ────────────────────────────────────


def test_post_process_exposes_prompt_text():
    """`prompt_text` 必须是完整 chunk 正文（压缩器的输入），snippet 仅展示。"""
    text = KEY + "。" + FILLER * 20
    docs = HybridRetriever()._post_process(QUERY, [_hit("c1", text)], top_k=1)

    assert docs, "质量过滤不应剔掉含查询术语的命中"
    assert docs[0]["prompt_text"] == text, "prompt_text 必须是完整正文，不能是 260 字片段"
    assert docs[0]["text"] == text
    assert len(docs[0]["snippet"]) < len(text), "snippet 仍应为展示用短片段"


def test_snippet_uses_semantic_sentences_when_query_literal_absent():
    """查不到 query 字面量时，snippet 不得只取 chunk 头部（关键句在 260 字之后）。"""
    text = FILLER * 14 + KEY  # KEY 起始位置 > 260 字；查询字面量不在文本中
    assert QUERY not in text

    snippet = HybridRetriever._build_snippet(text, QUERY)

    assert "五年" in snippet, "snippet 必须包含查询相关的关键句，而不是前 260 字"
    assert len(snippet) <= 260, f"snippet 超限: {len(snippet)}"


def test_get_prompt_text_single_source():
    """提示词正文唯一读取入口：prompt_text 优先，其余键仅兼容兜底。"""
    from app.rag.context_compressor import get_prompt_text

    assert get_prompt_text({"prompt_text": "P", "text": "T", "snippet": "S"}) == "P"
    assert get_prompt_text({"text": "T", "snippet": "S"}) == "T"
    assert get_prompt_text({"content": "C", "snippet": "S"}) == "C"
    assert get_prompt_text({"snippet": "S"}) == "S", "旧载荷兜底不得为空"
    assert get_prompt_text({}) == ""


# ── pipeline：压缩读全文、提示词读 prompt_text、上限可配置且按句截断 ──────────


def test_chat_stream_prompt_uses_prompt_text_not_snippet(monkeypatch):
    """snippet 非空不得短路全文：提示词必须包含 prompt_text 里的关键句。"""
    text = KEY + "。" + FILLER * 10
    context = [
        {
            "filename": "f.pdf",
            "text": text,
            "prompt_text": text,
            "snippet": "SNIPPET-ONLY-MARKER",
            "score": 0.9,
        }
    ]

    user_content = _run_chat_stream(context, monkeypatch=monkeypatch)

    assert "五年" in user_content, "提示词必须来自 prompt_text 全文（关键句在 snippet 之外）"
    assert "SNIPPET-ONLY-MARKER" not in user_content, (
        "snippet 是展示层字段，不得作为提示词正文来源（#186 的 or 短路）"
    )


def test_prompt_cap_is_configurable_and_sentence_aware(monkeypatch):
    """上限配置必须生效：>3000 字全文、200 字上限下只留头部上下文 + 相关句。

    变异锁（评审 M1 修正）：
    - 还原 `prompt_text[:3000]`（硬编码 + 头部硬切）→ KEY（位于 3000 字之后）丢失；
    - 改成 `compress(..., 3000)`（忽略配置）→ MID_MARKER（位于 200~3000 之间）
      混入且上下文长度超限；
    - 还原 `snippet or text` → snippet 标记混入 / KEY 丢失。
    """
    mid_marker = "中段唯一标记甲乙丙丁。"
    text = FILLER * 17 + mid_marker + FILLER * 93 + KEY  # KEY 位置 > 3000
    assert len(text) > 3000
    context = [
        {
            "filename": "f.pdf",
            "text": text,
            "prompt_text": text,
            "snippet": "HEAD-ONLY-MARKER",
            "score": 0.9,
        }
    ]

    user_content = _run_chat_stream(
        context, monkeypatch=monkeypatch, max_chars=200, enable_compression=False
    )

    assert "五年" in user_content, "上限内必须保留查询相关句（位于 3000 字之后）"
    assert mid_marker not in user_content, (
        "超出 200 字上限的无关注释句不得混入——说明配置未生效或退化为头部硬切"
    )
    assert "HEAD-ONLY-MARKER" not in user_content
    assert len(user_content) < 500, f"提示词上下文未受 200 字上限约束: {len(user_content)}"
    # 未压缩路径先浅拷贝再掩码/截断，不得就地改写调用方 dict（评审 m4）。
    assert context[0]["prompt_text"] == text
    assert context[0]["text"] == text


@pytest.mark.parametrize("cap", [1, 2, 3, 4, 10])
def test_compress_never_exceeds_tiny_caps(cap):
    """极端小上限（1~3）也不得因负切片越界（评审 m1）。"""
    from app.rag.context_compressor import compress

    text = "架构说明！是否支持？发票必须与消费一致。审计日志的保留期限为五年。"
    out = compress(text, QUERY, max_chars=cap)
    assert len(out) <= cap, f"cap={cap} 时输出了 {len(out)} 字: {out!r}"


def test_compress_without_relevant_sentences_fills_sentence_budget():
    """查询术语零命中时按句界填满预算，不退化成 30% 头部（评审 m2）。"""
    import re

    from app.rag.context_compressor import compress

    text = "".join(f"第{i}段是无关的系统介绍。" for i in range(40))
    out = compress(text, "完全不存在的术语组合", max_chars=300)
    assert len(out) > 200, f"零命中时不应只剩 30% 头部（实际 {len(out)} 字）"
    assert len(out) <= 300
    # 输出必须由完整句子组成（按句界填充）；`text[:300]` 这类硬切会切出
    # 半句，因而不在原文句子集合中（评审 m2 残留 Nit）。
    sentences = {s for s in re.split(r"[。！？\n]", text) if s}
    parts = [p for p in out.split("。") if p]
    assert parts and all(p in sentences for p in parts), (
        f"输出出现非完整句子（硬切迹象）: {parts[-1]!r}"
    )


@pytest.mark.parametrize("enable_compression", [True, False])
def test_chat_stream_masks_prompt_text(monkeypatch, enable_compression):
    """PII 掩码跟随提示词正文来源：LLM 收到占位符，原文可经 sink 还原。"""
    monkeypatch.setattr(settings, "ENABLE_PII_MASKING", True, raising=False)
    phone = "13812345678"
    text = f"审计日志的保留期限为五年。联系人手机号 {phone}。"
    context = [
        {
            "filename": "f.pdf",
            "text": text,
            "prompt_text": text,
            "snippet": text,
            "score": 0.9,
        }
    ]
    sink: dict = {}

    user_content = _run_chat_stream(
        context,
        monkeypatch=monkeypatch,
        enable_masking=True,
        mask_sink=sink,
        enable_compression=enable_compression,
    )

    assert phone not in user_content, "原始 PII 不得进入提示词"
    assert "[PHONE_" in user_content, "提示词应包含掩码占位符"
    assert phone in sink.values(), "掩码映射必须交给调用方用于还原引用"
    # 掩码只作用于提示词副本：调用方 dict 保持原文。enable_compression=False
    # 时依赖 chat_stream 的浅拷贝，删掉它本用例即红（评审 m4）。
    assert context[0]["prompt_text"] == text
    assert context[0]["text"] == text


def test_compressor_reads_prompt_text_and_keeps_snippet():
    """压缩器读 prompt_text 全文并写回；snippet 保持展示原文。"""
    from app.rag.context_compressor import compress_context_list

    long_text = FILLER * 10 + KEY  # 关键句在 300 字之后
    ctx = {"prompt_text": long_text, "snippet": "展示片段", "filename": "a.pdf"}

    out = compress_context_list([ctx], QUERY, max_context_chars=200)

    assert len(out) == 1
    item = out[0]
    assert item["original_length"] == len(long_text), "original_length 必须按 prompt_text 计"
    assert item["is_compressed"] is True
    assert len(item["prompt_text"]) <= 200
    assert "五年" in item["prompt_text"], "压缩必须覆盖全文（关键句在头部之外）"
    assert item["snippet"] == "展示片段", "snippet 只用于展示，压缩器不得覆盖"
    assert item["text"] == item["prompt_text"], "text 兼容键同步为压缩结果"


# ── agent / evaluator：同病的消费点一律全文优先 ──────────────────────────────


@pytest.mark.asyncio
async def test_agent_tool_prefers_prompt_text():
    """agent 检索工具的输出正文必须来自 prompt_text，而非 260 字 snippet。"""
    from app.agent.core_tools import search_knowledge_base

    full = FILLER * 20 + KEY
    docs = [
        {
            "score": 0.9,
            "filename": "real.pdf",
            "snippet": "SNIPPET-ONLY",
            "text": full,
            "prompt_text": full,
            "document_id": "d1",
        }
    ]
    pipeline = MagicMock()
    pipeline.search_knowledge_base = AsyncMock(return_value=(docs, None))

    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        tool_output = await search_knowledge_base(QUERY, organization_id=1)

    payload = json.loads(tool_output)
    assert "五年" in payload["content"], "工具正文必须保留关键句（snippet 之外）"
    assert "SNIPPET-ONLY" not in payload["content"]


def test_agent_loop_context_prefers_prompt_text():
    """agent loop 拼接参考文档时必须全文优先，snippet 仅兜底。"""
    from app.agent.config import AgentConfig
    from app.agent.loop import PERAgentLoop

    full = FILLER * 20 + KEY
    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1, user_id=0)

    messages = loop._build_messages(
        QUERY,
        None,
        [
            {
                "filename": "f.pdf",
                "snippet": "SNIPPET-ONLY",
                "text": full,
                "prompt_text": full,
            }
        ],
    )

    system = messages[0]["content"]
    assert "五年" in system, "agent 初始上下文必须包含全文关键句"
    assert "SNIPPET-ONLY" not in system


def test_evaluator_context_prefers_prompt_text():
    """RAG 评估的 judge 上下文与提示词同源：prompt_text 优先。"""
    from app.rag.evaluator import _build_context_str

    item = {"snippet": "SNIPPET-ONLY", "prompt_text": FILLER * 5 + KEY, "text": ""}
    context_str = _build_context_str([item])

    assert "五年" in context_str
    assert "SNIPPET-ONLY" not in context_str
