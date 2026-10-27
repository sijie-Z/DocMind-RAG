"""Built-in tools for the DocMind agent.

Tools are organized by capability:
- search:  Knowledge base retrieval (hybrid search, vector, keyword)
- analyze: Document analysis (summarize, extract, compare)
- manage:  Session management (create session, bind documents)
- sql:     Text-to-SQL for structured data queries
"""

import contextlib
import json
import logging
import time
from typing import Any

from app.agent.registry import (
    ToolResult,
    extended_surface_enabled,
    register_tool,
)
from app.agent.run_context import RunContext
from app.agent.usage import ctx_from_run_context, record_llm_call_error, record_llm_usage
from app.core.config import ai as ai_config
from app.core.config import settings
from app.rag.source_provenance import (
    LEGACY_UNVERIFIED,
    SourceProvenanceUnavailableError,
    filter_original_results,
    provenance_of,
)

logger = logging.getLogger(__name__)


def _missing_organization_result(tool_name: str) -> ToolResult:
    """A2 P0（fail-closed）：工具缺少组织上下文时拒绝执行。

    这些工具直接以 organization_id 作为 ES / DB 的租户过滤条件；默认值从
    1 改为 None 后，任何漏传组织的调用必须显式失败，绝不静默落到 1 号组织。
    """
    return ToolResult.fail(
        code="organization_required",
        message=(
            f"工具 '{tool_name}' 缺少组织上下文（organization_id），已拒绝执行以免跨租户读写。"
        ),
    )


# ── 结构化检索结果信封（T4：来源伪造隔离）───────────────────────────────────
# 检索工具此前把来源行（"[i] (score) filename"）与文档正文拼进**同一个**
# 字符串，消费端只能对整段输出跑逐行正则——文档正文里构造一行
# "[3] (0.99) 假.pdf" 就能伪造一张引用卡（documentId 为空）。
# 现在来源元数据只经信封的 ``sources`` 段传递，正文一律放进 ``content``
# 字段（JSON 字符串内部，不可越界成顶层键）；消费端只读 ``sources`` 段。
# ``kind`` 是信封判别标记，避免把其他工具的合法 JSON 输出误当来源。
SEARCH_RESULTS_PAYLOAD_KIND = "docmind.search_results/v1"
# 与 Chat 的 compress_context_list 总预算一致；只计算正文证据，来源元数据不随截断丢失。
_AGENT_SEARCH_TOTAL_EVIDENCE_MAX_CHARS = 8000


def _provenance_failure(error: SourceProvenanceUnavailableError) -> ToolResult:
    """Use the existing ToolErrorType while preserving machine-readable provenance state."""
    return ToolResult.fail(
        code="invalid_configuration",
        message=str(error),
        raw=error.as_dict(),
    )


def _original_hits(hits: list[dict], *, query: str) -> list[dict]:
    """Apply the shared O gate while retaining full text for direct Agent readers."""
    return filter_original_results(
        hits,
        query=query,
        mode=settings.RAG_SOURCE_PROVENANCE_MODE,
        snippet_builder=lambda text, _query: text,
    )


def _entry_parts(entry: tuple) -> list:
    """Support new seven-field entries and legacy five-field entries honestly."""
    parts = list(entry)
    if len(parts) == 5:
        parts.extend([LEGACY_UNVERIFIED, ""])
    if len(parts) != 7:
        raise ValueError("检索 entry 必须为5或7个字段")
    return parts


def _build_search_payload(
    entries: list[tuple],
    *,
    include_evidence: bool = False,
) -> str:
    """Pack score/relevance/filename/document/text/provenance/chunk into a JSON envelope.

    Legacy five-field entries remain accepted and are marked legacy_unverified.
    Text stays inside content/evidence strings and can never forge source metadata.
    """
    normalized_entries = [_entry_parts(entry) for entry in entries]
    sources = [
        {
            "index": index,
            "score": score,
            "relevanceScore": relevance_score,
            "filename": filename,
            "documentId": document_id or "",
            "textProvenance": text_provenance,
            "chunkId": chunk_id or "",
        }
        for index, (
            score,
            relevance_score,
            filename,
            document_id,
            _snippet,
            text_provenance,
            chunk_id,
        ) in enumerate(normalized_entries, 1)
    ]
    content = "\n\n".join(
        f"[{index}] ({score:.2f}) {filename}\n{snippet}"
        for index, (
            score,
            _relevance_score,
            filename,
            _document_id,
            snippet,
            _text_provenance,
            _chunk_id,
        ) in enumerate(normalized_entries, 1)
    )
    payload: dict[str, Any] = {
        "kind": SEARCH_RESULTS_PAYLOAD_KIND,
        "sources": sources,
        "content": content,
    }
    if include_evidence:
        # 与 sources 同序、使用原始 index；不从 content 正文反推来源。
        payload["evidence"] = [
            {"index": index, "content": snippet}
            for index, (
                _score,
                _relevance_score,
                _filename,
                _document_id,
                snippet,
                _text_provenance,
                _chunk_id,
            ) in enumerate(normalized_entries, 1)
        ]
    return json.dumps(payload, ensure_ascii=False)


# ─── Search Tools ────────────────────────────────────────────────────────────


@register_tool(
    name="search_knowledge_base",
    description=(
        "Search the enterprise knowledge base (hybrid or mode=vector). "
        "Returns ranked snippets with sources."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query",
            },
            "top_k": {
                "type": "integer",
                "description": "Results (default 5, max 20)",
                "default": 5,
            },
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Document IDs",
            },
            "mode": {
                "type": "string",
                "enum": ["hybrid", "vector"],
                "description": "hybrid|vector",
                "default": "hybrid",
            },
        },
        "required": ["query"],
    },
    tags=["search", "retrieval"],
)
async def search_knowledge_base(
    query: str,
    top_k: int = 5,
    document_ids: list[str] | None = None,
    mode: str = "hybrid",
    organization_id: int | None = None,
    **_: Any,
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("search_knowledge_base")
    # issue #509：vector_search 合并进本工具（mode=vector）。纯向量臂的
    # min_score=1.15 / 空结果哨兵 / 失败语义原样保留在 _vector_search_impl。
    mode_norm = str(mode or "").strip().lower()
    if mode_norm not in ("hybrid", "vector"):
        return ToolResult.fail(
            code="validation_error", message=f"未知 mode: {mode}（支持 hybrid/vector）"
        )
    if mode_norm == "vector":
        return await _vector_search_impl(query, top_k, organization_id, document_ids)

    from app.core.config import settings
    from app.dependencies import get_rag_pipeline
    from app.rag.context_compressor import compress, get_prompt_text
    from app.rag.errors import RetrievalUnavailableError

    pipeline = get_rag_pipeline()
    try:
        results, _ = await pipeline.search_knowledge_base(
            query=query,
            organization_id=organization_id,
            top_k=min(top_k, 20),
            document_ids=document_ids,
            search_mode="hybrid",
        )
    except SourceProvenanceUnavailableError as e:
        return _provenance_failure(e)
    except RetrievalUnavailableError as e:
        # issue #292：检索故障不得伪装成「没有相关内容」——返回结构化失败，
        # 走 executor 的 tool_error / run_report failures 通道（SSE 发 tool_error，
        # 指标记 error），而不是被 legacy 字符串路径归一化成 success=True。
        state = "超时" if e.kind == "timeout" else "暂不可用"
        return ToolResult.fail(
            code="knowledge_base_unavailable",
            message=(
                f"知识库检索服务{state}（kind={e.kind}），本次无法判断知识库中是否有"
                "相关内容；请稍后重试，或将本次查询改用其他信息来源完成。"
            ),
            raw=e,
        )
    if not results:
        return "No relevant documents found for this query."

    prompt_max_chars = max(1, int(settings.RAG_PROMPT_TEXT_MAX_CHARS))
    remaining_evidence_chars = _AGENT_SEARCH_TOTAL_EVIDENCE_MAX_CHARS
    entries = []
    for index, doc in enumerate(results):
        score = float(doc.get("score") or 0.0)
        relevance = doc.get("relevance_score")
        filename = doc.get("filename", "Unknown")
        # issue #186：正文单一来源 prompt_text（snippet 仅展示）。Agent 与 Chat
        # 共用每源 prompt 上限及 8000 字总证据预算；剩余预算按剩余来源数分配，
        # 前序长 chunk 不得独占预算，短来源未用的额度自然留给后续来源。
        remaining_sources = len(results) - index
        source_max_chars = min(prompt_max_chars, remaining_evidence_chars // remaining_sources)
        if source_max_chars > 0:
            snippet = compress(
                get_prompt_text(doc),
                query,
                max_chars=source_max_chars,
            )
        else:
            snippet = ""
        remaining_evidence_chars -= len(snippet)
        entries.append(
            (
                score,
                float(relevance) if relevance is not None else None,
                filename,
                doc.get("document_id", ""),
                snippet,
                provenance_of(doc),
                doc.get("chunk_id") or "",
            )
        )
    return _build_search_payload(
        entries,
        include_evidence=ai_config.unified_final_enabled(),
    )


async def _vector_search_impl(
    query: str,
    top_k: int,
    organization_id: int,
    document_ids: list[str] | None = None,
) -> str | ToolResult:
    """纯向量检索（原 vector_search 实现，issue #509 原样迁入）。"""
    # T6：与姊妹检索工具一致，钳制到 20 以内，避免 ES 请求被调用方无限放大
    top_k = min(top_k, 20)

    from app.dependencies import get_rag_pipeline

    pipeline = get_rag_pipeline()
    query_vector = await pipeline.get_embedding(query)
    if not query_vector:
        return "Embedding service unavailable."

    from app.core.elasticsearch import ElasticsearchTools
    from app.rag.context_compressor import compress

    # issue #509 评审修正：规范入口同时暴露 document_ids，vector 模式不得静默
    # 丢弃它（否则“限定这些文档”实际变成全组织检索）。
    filters: list[dict[str, Any]] = [{"term": {"organization_id": str(organization_id)}}]
    if document_ids:
        filters.append({"terms": {"document_id": list(document_ids)}})

    es_query = {
        "size": top_k,
        "min_score": 1.15,
        "query": {
            "script_score": {
                "query": {"bool": {"filter": filters}},
                "script": {
                    "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                    "params": {"query_vector": query_vector},
                },
            }
        },
        "_source": [
            "content",
            "original_text",
            "text_provenance",
            "filename",
            "document_id",
            "chunk_id",
        ],
    }
    res = await ElasticsearchTools.search_documents(es_query)
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        return "No semantically similar documents found."

    candidates = [
        {
            **(hit.get("_source") or {}),
            "_score": hit.get("_score"),
            "_id": hit.get("_id"),
        }
        for hit in hits
    ]
    try:
        original_hits = _original_hits(candidates, query=query)
    except SourceProvenanceUnavailableError as e:
        return _provenance_failure(e)

    entries = []
    for src in original_hits:
        entries.append(
            (
                float(src.get("_score") or 0.0),
                # 纯向量臂的分数是 ES script_score（cosine+1），与重排分不同尺度；
                # 不冒充 relevance，来源卡片按序位展示。
                None,
                src.get("filename", "?"),
                src.get("document_id", ""),
                # issue #186：300 字上限按语义句抽取，不硬切 chunk 头部。
                compress(src.get("prompt_text") or "", query, max_chars=300),
                provenance_of(src),
                src.get("chunk_id") or src.get("_id") or "",
            )
        )
    return _build_search_payload(
        entries,
        include_evidence=ai_config.unified_final_enabled(),
    )


@register_tool(
    name="vector_search",
    description=(
        "Pure vector (semantic) search over the knowledge base. "
        "Best for conceptual queries where exact keywords may not match."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Semantic search query",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results (default 5, max 20)",
                "default": 5,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
    tags=["search"],
    # issue #509：能力已合并进 search_knowledge_base(mode=vector)；保留注册与
    # handler（不删工具、旧名可用），但不再进 schema；旧名经 TOOL_ALIASES 路由。
    hidden=True,
    superseded_by="search_knowledge_base",
)
async def vector_search(
    query: str,
    top_k: int = 5,
    organization_id: int | None = None,
    **_: Any,
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("vector_search")
    return await _vector_search_impl(query, top_k, organization_id, None)


# ─── Analysis Tools ──────────────────────────────────────────────────────────


@register_tool(
    name="summarize_document",
    description=(
        "Generate a concise summary of a specific document by its ID. "
        "Extracts key points, main topics, and conclusions."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The document ID to summarize",
            },
        },
        "required": ["document_id"],
    },
    tags=["analysis"],
    # issue #509：能力已合并进 extract_insights(mode=summary)；保留注册与 handler
    # （不删工具、旧名可用），但不再进 schema；旧名经 TOOL_ALIASES 路由。
    hidden=True,
    superseded_by="extract_insights",
)
async def summarize_document(
    document_id: str,
    organization_id: int | None = None,
    run_context: RunContext | None = None,
    **_: Any,
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("summarize_document")
    return await _summarize_document_impl(document_id, organization_id, run_context)


async def _summarize_document_impl(
    document_id: str,
    organization_id: int,
    run_context: RunContext | None = None,
) -> str | ToolResult:
    """文档摘要（原 summarize_document 实现，issue #509 原样迁入）。

    无 LLM / 空正文 / 异常三条路径都保留原文预览兜底——不得因合并丢失。
    """
    from app.core.elasticsearch import ElasticsearchTools
    from app.dependencies import get_rag_pipeline

    es_query = {
        "size": 50,
        "query": {
            "bool": {
                "must": [
                    {"term": {"document_id": document_id}},
                    {"term": {"organization_id": str(organization_id)}},
                ]
            }
        },
        "sort": [{"chunk_index": {"order": "asc"}}],
        "_source": ["content", "original_text", "text_provenance", "filename"],
    }
    res = await ElasticsearchTools.search_documents(es_query)
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        return f"Document {document_id} not found in index."

    try:
        original_chunks = _original_hits(
            [h.get("_source") or {} for h in hits],
            query="",
        )
    except SourceProvenanceUnavailableError as e:
        return _provenance_failure(e)
    full_text = "\n".join(chunk.get("prompt_text") or "" for chunk in original_chunks)
    filename = original_chunks[0].get("filename", "Unknown") if original_chunks else "Unknown"

    # Use LLM to summarize
    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        return f"Document: {filename}\nContent preview: {full_text[:1000]}..."

    llm_messages = [
        {"role": "system", "content": "你是文档摘要专家。请用中文生成结构化摘要。"},
        {
            "role": "user",
            "content": f"请为以下文档生成摘要，包含：1)主题 2)关键要点(3-5条) 3)结论\n\n文档：{full_text[:6000]}",
        },
    ]
    llm_started = time.perf_counter()
    llm_recorded = False
    call_attempted = False
    model_name: str | None = None
    try:
        from app.core.config import settings

        call_attempted = True
        model_name = settings.DEEPSEEK_MODEL
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=llm_messages,
            temperature=0.1,
            max_tokens=500,
        )
        # 工具内 LLM 记账（issue #239 minor-2）：此前直连调用不入账，
        # 任务级 total_tokens 不含工具内部用量。
        # issue #510：同入口写调用级 JSONL（延迟 + 提示词摘要，不落正文）。
        record_llm_usage(
            resp,
            ctx_from_run_context(run_context),
            latency_ms=(time.perf_counter() - llm_started) * 1000,
            messages=llm_messages,
        )
        llm_recorded = True
        summary = (resp.choices[0].message.content or "").strip()
        if not summary:
            # 第四波实机诊断（复验于 e5b5e5b）：deepseek-v4-flash 为推理模型——
            # max_tokens=500 时 reasoning_content 吃满预算、正文为空
            # （finish_reason=length）。空正文必须走"不可用 → 原文预览"兜底
            # （与下方异常路径同款），绝不把 "Summary generation failed." 当
            # 工具结果喂回模型——executor 会据此放弃总结（实机"没有拿到可总结
            # 的正文"的最后一厘米）。
            raise RuntimeError("empty summary content (reasoning consumed budget)")
        return f"Document: {filename}\n\n{summary}"
    except Exception as e:
        logger.warning(f"LLM summarization failed for {document_id}: {e}")
        # issue #510：失败调用留一行（成功记账后的空正文/解析异常不重复记）。
        if call_attempted and not llm_recorded:
            record_llm_call_error(
                ctx_from_run_context(run_context),
                model=model_name,
                error=e,
                latency_ms=(time.perf_counter() - llm_started) * 1000,
                messages=llm_messages,
            )
        # Fallback: return raw content truncated
        raw_preview = full_text[:2000]
        return f"Document: {filename}\n(LLM summarization unavailable, showing raw content)\n\n{raw_preview}"


@register_tool(
    name="extract_keywords",
    description=(
        "Extract key terms and entities from a piece of text. "
        "Useful for understanding document topics and building search queries."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to extract keywords from",
            },
            "max_keywords": {
                "type": "integer",
                "description": "Maximum number of keywords to extract (default 10)",
                "default": 10,
            },
        },
        "required": ["text"],
    },
    tags=["analysis"],
    enabled_predicate=extended_surface_enabled,
)
async def extract_keywords(text: str, max_keywords: int = 10, **_: Any) -> str:
    from app.rag.query_processor import extract_query_terms

    terms = extract_query_terms(text)
    return json.dumps(terms[:max_keywords], ensure_ascii=False)


# ── Domain analytical frameworks ─────────────────────────────────

ANALYSIS_FRAMEWORKS: dict[str, str] = {
    "dupont": (
        "请使用杜邦分析法对财务数据进行分析，按以下框架拆解：\n"
        "1. 净资产收益率(ROE) = 销售净利率 × 总资产周转率 × 权益乘数\n"
        "2. 销售净利率: 净利润/营业收入 → 判断盈利能力变化\n"
        "3. 总资产周转率: 营业收入/总资产 → 判断运营效率变化\n"
        "4. 权益乘数: 总资产/股东权益 → 判断财务杠杆变化\n"
        "5. 综合判断: ROE 变化的根本驱动因素是哪个？\n"
        "输出 JSON，包含: overall_roe, profit_margin, asset_turnover, "
        "equity_multiplier, primary_driver, trend_assessment"
    ),
    "swot": (
        "请使用 SWOT 框架进行分析：\n"
        "1. 优势(Strengths): 内部有利因素、核心能力、资源优势\n"
        "2. 劣势(Weaknesses): 内部不足、能力短板、资源约束\n"
        "3. 机会(Opportunities): 外部有利条件、市场趋势、政策利好\n"
        "4. 威胁(Threats): 外部风险、竞争压力、政策变化\n"
        "输出 JSON，包含: strengths, weaknesses, opportunities, threats, overall_strategy"
    ),
    "pest": (
        "请使用 PEST 宏观环境分析框架：\n"
        "1. 政治(Political): 政策法规、政府干预、贸易政策\n"
        "2. 经济(Economic): 经济增长、利率、通胀、就业\n"
        "3. 社会(Social): 人口结构、消费趋势、文化因素\n"
        "4. 技术(Technological): 技术创新、研发投入、数字化\n"
        "输出 JSON，包含: political, economic, social, technological, key_risks"
    ),
    "risk_assessment": (
        "请进行结构化风险评估：\n"
        "1. 识别风险: 列出文档中提到的所有风险因素\n"
        "2. 分类: 按类型分类（市场/运营/合规/财务/战略）\n"
        "3. 影响程度: 每项风险的可能影响（高/中/低）\n"
        "4. 发生概率: 每项风险的发生概率（高/中/低）\n"
        "5. 综合风险评分: 影响 × 概率 的加权汇总\n"
        "输出 JSON，包含: risk_matrix, overall_score, top_risks, mitigation_suggestions"
    ),
    "financial_health": (
        "请进行财务健康度分析，按以下维度：\n"
        "1. 盈利能力: 净利润率、毛利率、ROE 趋势\n"
        "2. 偿债能力: 资产负债率、流动比率、利息保障倍数\n"
        "3. 运营能力: 存货周转率、应收账款周转率、总资产周转率\n"
        "4. 现金流: 经营活动现金流、自由现金流趋势\n"
        "5. 成长性: 收入增长率、利润增长率、市场份额变化\n"
        "输出 JSON，包含: profitability, solvency, efficiency, cash_flow, growth, overall_health"
    ),
    "contract_risk": (
        "请进行合同风险分析，按以下框架：\n"
        "1. 条款模糊度: 是否存在模糊不清的表述\n"
        "2. 赔偿条款: 违约赔偿是否合理、是否有上限\n"
        "3. 履约条件: 履约条件是否明确、是否存在不平等条款\n"
        "4. 终止条款: 合同终止条件和通知期是否合理\n"
        "5. 争议解决: 管辖法院、仲裁条款是否有利\n"
        "输出 JSON，包含: clause_ambiguity, indemnification, performance_conditions, "
        "termination, dispute_resolution, overall_risk_level"
    ),
}


@register_tool(
    name="extract_insights",
    description=(
        "Analyze a document: structured insights (entities/metrics/claims/structure) "
        "or mode=summary."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Document ID",
            },
            "aspects": {
                "type": "string",
                "description": "entities,metrics,claims,structure,all",
                "default": "all",
            },
            "framework": {
                "type": "string",
                "description": "dupont/swot/pest/risk/financial/contract",
                "default": "",
            },
            "mode": {
                "type": "string",
                "enum": ["insights", "summary"],
                "description": "insights (default) | summary",
                "default": "insights",
            },
        },
        "required": ["document_id"],
    },
    tags=["analysis", "deep"],
)
async def extract_insights(
    document_id: str,
    aspects: str = "all",
    framework: str = "",
    mode: str = "insights",
    organization_id: int | None = None,
    run_context: RunContext | None = None,
    **_: Any,
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("extract_insights")
    # issue #509：summarize_document 合并进本工具（mode=summary）。摘要的
    # 无 LLM / 空正文 / 异常兜底原样保留在 _summarize_document_impl。
    mode_norm = str(mode or "").strip().lower()
    if mode_norm not in ("insights", "summary"):
        return ToolResult.fail(
            code="validation_error", message=f"未知 mode: {mode}（支持 insights/summary）"
        )
    if mode_norm == "summary":
        # issue #509 评审修正：summary 不是 insights 的参数子集——显式传
        # aspects/framework 时拒绝，避免“以为按框架分析、实际只生成摘要”。
        if str(aspects or "all").strip().lower() != "all" or str(framework or "").strip():
            return ToolResult.fail(
                code="validation_error",
                message="mode=summary 不支持 aspects/framework 参数（请改用默认 insights 模式）",
            )
        return await _summarize_document_impl(document_id, organization_id, run_context)
    from app.core.config import settings
    from app.core.elasticsearch import ElasticsearchTools
    from app.dependencies import get_rag_pipeline

    es_query = {
        "size": 50,
        "query": {
            "bool": {
                "must": [
                    {"term": {"document_id": document_id}},
                    {"term": {"organization_id": str(organization_id)}},
                ]
            }
        },
        "sort": [{"chunk_index": {"order": "asc"}}],
        "_source": ["content", "original_text", "text_provenance", "filename", "section_title"],
    }
    res = await ElasticsearchTools.search_documents(es_query)
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        # issue #301 #12：失败必须结构化（#293 契约）—— 旧行为返回
        # {"error": ...} 的"正常"JSON 字符串，消费端（skills.py）大小写不匹配
        # 漏检后把它当"分析结果"写进报告。
        return ToolResult.fail(
            code="document_not_found",
            message=f"Document {document_id} not found in index.",
        )

    try:
        original_chunks = _original_hits(
            [h.get("_source") or {} for h in hits],
            query="",
        )
    except SourceProvenanceUnavailableError as e:
        return _provenance_failure(e)
    filename = original_chunks[0].get("filename", "Unknown") if original_chunks else "Unknown"
    full_text = "\n".join(chunk.get("prompt_text") or "" for chunk in original_chunks)
    text_sample = full_text[:8000]

    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        # issue #301 #12：无 LLM 就没有洞察——这是工具失败，不是结果。
        # 旧行为返回 insights={"error": "LLM unavailable"} 的正常 JSON，
        # 会被消费端原样写进报告（"分析结果"里出现 error 载荷）。
        return ToolResult.fail(
            code="llm_unavailable",
            message="LLM unavailable",
            raw={
                "filename": filename,
                "document_id": document_id,
                "raw_preview": text_sample[:2000],
            },
        )

    aspect_instructions = {
        "entities": "提取文档中的关键实体：人物、组织、地点、日期、专有名词。",
        "metrics": "提取文档中的所有数据指标、统计数字、量化结果。",
        "claims": "提取文档的核心观点、关键结论、主要论据。",
        "structure": "分析文档的章节结构、层次关系、段落组织方式。",
    }

    # Use domain framework if specified
    framework_key = framework.strip().lower() if framework else ""
    if framework_key in ANALYSIS_FRAMEWORKS:
        instructions = ANALYSIS_FRAMEWORKS[framework_key]
        system_prompt = (
            f"你是{framework_key}分析专家。严格按照提供的框架进行分析，输出结构化 JSON 格式。"
        )
        logger.info("Using domain framework '%s' for document analysis", framework_key)
    else:
        selected = [a.strip() for a in aspects.split(",")]
        if "all" in selected:
            instructions = "\n".join(aspect_instructions.values())
        else:
            instructions = "\n".join(v for k, v in aspect_instructions.items() if k in selected)
        system_prompt = (
            "你是文档分析专家。分析文档内容，提取结构化信息。只返回 JSON，不要包含任何解释。"
        )

    llm_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"分析以下文档，{instructions}\n\n"
                f"文档：{filename}\n\n"
                f"内容：{text_sample}\n\n"
                "返回 JSON 格式：\n"
                '{"entities": {"people": [], "organizations": [], "locations": [], "dates": []}, '
                '"metrics": [{"name": "...", "value": "..."}], '
                '"claims": [{"claim": "...", "confidence": "high/medium/low"}], '
                '"structure": [{"level": 1, "heading": "..."}]}'
            ),
        },
    ]
    llm_started = time.perf_counter()
    llm_recorded = False
    call_attempted = False
    model_name: str | None = None
    try:
        call_attempted = True
        model_name = settings.DEEPSEEK_MODEL
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=llm_messages,
            temperature=0.1,
            max_tokens=1500,
            timeout=30,
        )
        # issue #510：调用级 JSONL（延迟 + 提示词摘要，不落正文）。
        record_llm_usage(
            resp,
            ctx_from_run_context(run_context),
            latency_ms=(time.perf_counter() - llm_started) * 1000,
            messages=llm_messages,
        )  # 工具内记账（#239 minor-2）
        llm_recorded = True
        insights_raw = resp.choices[0].message.content or "{}"
        # Try to parse as JSON; fallback to raw string
        try:
            insights = json.loads(insights_raw)
        except json.JSONDecodeError:
            insights = {"raw": insights_raw}

        return json.dumps(
            {
                "filename": filename,
                "document_id": document_id,
                "chunk_count": len(hits),
                "content_length": len(full_text),
                "insights": insights,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        # issue #301 #12：抽取失败的错误 JSON 不得再伪装成"分析结果"——
        # 旧形态 {"error": ...} 被复合技能漏检后原样写进报告。结构化失败传播。
        # issue #510：失败调用留一行（成功记账后的解析异常不重复记）。
        if call_attempted and not llm_recorded:
            record_llm_call_error(
                ctx_from_run_context(run_context),
                model=model_name,
                error=e,
                latency_ms=(time.perf_counter() - llm_started) * 1000,
                messages=llm_messages,
            )
        return ToolResult.fail(
            code="analysis_failed",
            message=f"Analysis failed: {e}",
            raw={
                "filename": filename,
                "document_id": document_id,
                "raw_preview": text_sample[:2000],
            },
        )


@register_tool(
    name="cross_document_analysis",
    description=("Analyze themes, patterns, and relationships across multiple documents."),
    parameters={
        "type": "object",
        "properties": {
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Document IDs (2-10)",
            },
            "analysis_type": {
                "type": "string",
                "description": "themes,differences,trends,comprehensive",
                "default": "comprehensive",
            },
        },
        "required": ["document_ids"],
    },
    tags=["analysis", "deep"],
)
async def cross_document_analysis(
    document_ids: list[str],
    analysis_type: str = "comprehensive",
    organization_id: int | None = None,
    run_context: RunContext | None = None,
    **_: Any,
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("cross_document_analysis")
    if not document_ids or len(document_ids) < 2:
        # issue #301 批次 2：历史 {"error": ...} JSON 出口迁移到结构化失败 ——
        # 旧的"正常 JSON"形态对消费端的小写键检查隐身（trustB 评审现场复现：
        # 错误载荷经 document_comparison Step 3 漏进报告）。
        return ToolResult.fail(
            code="validation_error",
            message="Please provide at least 2 document IDs.",
        )
    if len(document_ids) > 10:
        return ToolResult.fail(
            code="validation_error",
            message="Maximum 10 documents allowed.",
        )

    from app.core.config import settings
    from app.core.elasticsearch import ElasticsearchTools
    from app.dependencies import get_rag_pipeline

    docs_data = []
    for doc_id in document_ids:
        es_query = {
            "size": 20,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"document_id": doc_id}},
                        {"term": {"organization_id": str(organization_id)}},
                    ]
                }
            },
            "_source": [
                "content",
                "original_text",
                "text_provenance",
                "filename",
                "upload_time",
                "document_id",
            ],
        }
        try:
            res = await ElasticsearchTools.search_documents(es_query)
            hits = res.get("hits", {}).get("hits", [])
            # Fallback: try filename match if ID lookup returned nothing
            if not hits:
                fallback_query = {
                    "size": 20,
                    "query": {
                        "bool": {
                            "must": [
                                {"term": {"filename": doc_id}},
                                {"term": {"organization_id": str(organization_id)}},
                            ]
                        }
                    },
                    "_source": [
                        "content",
                        "original_text",
                        "text_provenance",
                        "filename",
                        "upload_time",
                        "document_id",
                    ],
                }
                res = await ElasticsearchTools.search_documents(fallback_query)
                hits = res.get("hits", {}).get("hits", [])
            if hits:
                original_chunks = _original_hits(
                    [h.get("_source") or {} for h in hits],
                    query=analysis_type,
                )
                text = " ".join(
                    (chunk.get("prompt_text") or "")[:500] for chunk in original_chunks[:10]
                )
                real_id = (
                    original_chunks[0].get("document_id", doc_id)
                    if original_chunks
                    else hits[0].get("_source", {}).get("document_id", doc_id)
                )
                docs_data.append(
                    {
                        "id": real_id,
                        "filename": (
                            original_chunks[0].get("filename", doc_id)
                            if original_chunks
                            else hits[0].get("_source", {}).get("filename", doc_id)
                        ),
                        "text": text[:3000],
                        "chunks": len(original_chunks),
                        "textProvenance": (
                            provenance_of(original_chunks[0])
                            if original_chunks
                            else LEGACY_UNVERIFIED
                        ),
                    }
                )
            else:
                docs_data.append({"id": doc_id, "filename": "Not found", "text": "", "chunks": 0})
        except SourceProvenanceUnavailableError as e:
            return _provenance_failure(e)
        except Exception as e:
            docs_data.append({"id": doc_id, "filename": f"Error: {e}", "text": "", "chunks": 0})

    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        # issue #301 批次 2：无 LLM 就没有跨文档分析 —— 这是工具失败，不是结果。
        # 旧行为返回 {"documents": ..., "analysis": "LLM unavailable"} 的正常
        # JSON，消费端会把它当分析结果消费。
        return ToolResult.fail(
            code="llm_unavailable",
            message="LLM unavailable",
            raw={
                "documents": [
                    {"id": d["id"], "filename": d["filename"], "chunks": d["chunks"]}
                    for d in docs_data
                ],
            },
        )

    doc_list_text = "\n\n".join(
        f"--- 文档 {i + 1}: {d['filename']} ---\n{d['text']}"
        for i, d in enumerate(docs_data)
        if d["text"]
    )

    analysis_prompts = {
        "common_themes": "找出所有文档共同的主题、重复出现的关键词和共享的观点。",
        "differences": "找出文档之间的主要差异、独特观点和矛盾之处。",
        "trends": "分析文档之间的时间趋势、演进关系和变化模式。",
        "comprehensive": "全面分析：共同主题、差异、趋势、互补关系和整体知识图谱。",
    }

    llm_messages = [
        {
            "role": "system",
            "content": "你是跨文档分析专家。分析多个文档之间的关系和模式。只返回 JSON。",
        },
        {
            "role": "user",
            "content": (
                f"{analysis_prompts.get(analysis_type, analysis_prompts['comprehensive'])}\n\n"
                f"文档列表：\n{doc_list_text[:10000]}\n\n"
                "返回 JSON 格式：\n"
                '{"summary": "整体分析总结", '
                '"common_themes": [{"theme": "...", "evidence": ["...", "..."], "documents": [0, 1]}], '
                '"unique_findings": [{"document_index": 0, "finding": "..."}], '
                '"contradictions": [{"description": "...", "between": [0, 2]}], '
                '"complementary_info": [{"description": "..."}], '
                '"conclusion": "综合分析结论"}'
            ),
        },
    ]
    llm_started = time.perf_counter()
    llm_recorded = False
    call_attempted = False
    model_name: str | None = None
    try:
        call_attempted = True
        model_name = settings.DEEPSEEK_MODEL
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=llm_messages,
            temperature=0.1,
            max_tokens=2000,
            timeout=60,
        )
        # issue #510：调用级 JSONL（延迟 + 提示词摘要，不落正文）。
        record_llm_usage(
            resp,
            ctx_from_run_context(run_context),
            latency_ms=(time.perf_counter() - llm_started) * 1000,
            messages=llm_messages,
        )  # 工具内记账（#239 minor-2）
        llm_recorded = True
        analysis_raw = resp.choices[0].message.content or "{}"
        try:
            analysis = json.loads(analysis_raw)
        except json.JSONDecodeError:
            analysis = {"raw": analysis_raw}

        return json.dumps(
            {
                "document_count": len(docs_data),
                "analysis_type": analysis_type,
                "documents": [
                    {"id": d["id"], "filename": d["filename"], "chunks": d["chunks"]}
                    for d in docs_data
                ],
                "analysis": analysis,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as e:
        # issue #301 批次 2：异常不再包成 {"error": ...} 的"正常" JSON 返回 ——
        # 结构化失败传播（判别权在工具内部，#297/#293 契约）。
        # issue #510：失败调用留一行（成功记账后的解析异常不重复记）。
        if call_attempted and not llm_recorded:
            record_llm_call_error(
                ctx_from_run_context(run_context),
                model=model_name,
                error=e,
                latency_ms=(time.perf_counter() - llm_started) * 1000,
                messages=llm_messages,
            )
        return ToolResult.fail(
            code="analysis_failed",
            message=f"Analysis failed: {e}",
            raw={
                "documents": [{"filename": d["filename"]} for d in docs_data],
            },
        )


@register_tool(
    name="generate_report",
    description=("Generate a polished markdown report from findings."),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Report title",
            },
            "sections": {
                "type": "string",
                "description": ('JSON array of {"heading","content","type":"text|table|list"}'),
            },
            "format": {
                "type": "string",
                "description": "markdown, json",
                "default": "markdown",
            },
        },
        "required": ["title", "sections"],
    },
    tags=["analysis", "report"],
)
async def generate_report(
    title: str,
    sections: str,
    format: str = "markdown",
    run_context: RunContext | None = None,
    **_: Any,
) -> str | ToolResult:
    from app.core.config import settings
    from app.dependencies import get_rag_pipeline

    # Parse sections
    try:
        parsed_sections = json.loads(sections)
        if not isinstance(parsed_sections, list):
            # issue #301 批次 2：入参校验失败 = 工具失败，旧 {"error": ...} 出口
            # 迁移到结构化失败（消费端曾漏检小写键）。
            return ToolResult.fail(
                code="validation_error",
                message="sections must be a JSON array",
            )
    except json.JSONDecodeError as e:
        return ToolResult.fail(
            code="validation_error",
            message=f"Invalid sections JSON: {e}",
        )

    if format == "json":
        return json.dumps(
            {
                "title": title,
                "sections": parsed_sections,
                "generated_at": __import__("datetime").datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )

    # Use LLM to generate a polished markdown report
    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        return _generate_markdown_fallback(title, parsed_sections)

    sections_text = "\n\n".join(
        f"## {s.get('heading', 'Section ' + str(i + 1))}\n{s.get('content', '')}"
        for i, s in enumerate(parsed_sections)
    )

    llm_messages = [
        {
            "role": "system",
            "content": "你是报告生成专家。根据提供的内容生成结构清晰、专业的 Markdown 报告。",
        },
        {
            "role": "user",
            "content": (
                f"请根据以下内容生成一份专业的分析报告。使用 Markdown 格式，包含：\n"
                f"1. 标题和元数据\n"
                f"2. 执行摘要\n"
                f"3. 详细分析（用表格、列表、引用等丰富格式）\n"
                f"4. 结论与建议\n\n"
                f"报告标题：{title}\n\n"
                f"内容：\n{sections_text[:8000]}"
            ),
        },
    ]
    llm_started = time.perf_counter()
    llm_recorded = False
    call_attempted = False
    model_name: str | None = None
    try:
        call_attempted = True
        model_name = settings.DEEPSEEK_MODEL
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=llm_messages,
            temperature=0.2,
            max_tokens=3000,
            timeout=60,
        )
        # issue #510：调用级 JSONL（延迟 + 提示词摘要，不落正文）。
        record_llm_usage(
            resp,
            ctx_from_run_context(run_context),
            latency_ms=(time.perf_counter() - llm_started) * 1000,
            messages=llm_messages,
        )  # 工具内记账（#239 minor-2）
        llm_recorded = True
        report = resp.choices[0].message.content or ""
        if not report.strip():
            return _generate_markdown_fallback(title, parsed_sections)
        return report
    except Exception as e:
        logger.warning(f"Report generation LLM call failed: {e}")
        # issue #510：失败调用留一行（成功记账后的空正文/解析异常不重复记）。
        if call_attempted and not llm_recorded:
            record_llm_call_error(
                ctx_from_run_context(run_context),
                model=model_name,
                error=e,
                latency_ms=(time.perf_counter() - llm_started) * 1000,
                messages=llm_messages,
            )
        return _generate_markdown_fallback(title, parsed_sections)


def _generate_markdown_fallback(title: str, sections: list) -> str:
    """Fallback: generate a clean markdown report without LLM."""
    from datetime import datetime

    lines = [
        f"# {title}",
        f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*",
        "",
    ]
    for section in sections:
        heading = section.get("heading", "")
        content = section.get("content", "")
        stype = section.get("type", "text")
        if heading:
            lines.append(f"## {heading}")
        if stype == "table" and content:
            try:
                rows = json.loads(content) if isinstance(content, str) else content
                if rows:
                    header = "| " + " | ".join(rows[0]) + " |"
                    sep = "| " + " | ".join(["---"] * len(rows[0])) + " |"
                    lines.extend([header, sep])
                    for row in rows[1:]:
                        lines.append("| " + " | ".join(row) + " |")
            except (json.JSONDecodeError, IndexError, KeyError):
                lines.append(content)
        elif stype == "list" and isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    lines.append(f"- **{item.get('label', '')}**: {item.get('value', '')}")
                else:
                    lines.append(f"- {item}")
        else:
            lines.append(str(content))
        lines.append("")
    return "\n".join(lines)


# ─── Document Management Tools ───────────────────────────────────────────────


@register_tool(
    name="list_documents",
    description=("List documents (IDs, filenames, status, dates)."),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max documents (default 20)",
                "default": 20,
            },
            "status": {
                "type": "string",
                "description": "indexed, parsing, failed, ...",
            },
        },
    },
    tags=["management"],
    requires_auth=True,
)
async def list_documents(
    limit: int = 20,
    status: str | None = None,
    organization_id: int | None = None,
    **_: Any,
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("list_documents")
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.document import Document, DocumentStatus

    async with AsyncSessionLocal() as session:
        stmt = select(Document).where(Document.organization_id == organization_id)
        if status:
            with contextlib.suppress(ValueError):
                stmt = stmt.where(Document.status == DocumentStatus(status))
        stmt = stmt.order_by(Document.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        docs = result.scalars().all()

    if not docs:
        return "No documents found."

    output = []
    for doc in docs:
        status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
        created = doc.created_at.strftime("%Y-%m-%d") if doc.created_at else "?"
        title = doc.title or doc.filename
        output.append(
            f"- ID: {doc.id} | 文件名: {doc.filename} | 标题: {title} | 状态: {status_val} | 创建: {created}"
        )
    return "\n".join(output)


@register_tool(
    name="get_document_info",
    description=("Get document status, chunk count, file size, and metadata."),
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "Document ID",
            },
        },
        "required": ["document_id"],
    },
    tags=["management"],
)
async def get_document_info(
    document_id: str, organization_id: int | None = None, **_: Any
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("get_document_info")
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.document import Document

    async with AsyncSessionLocal() as session:
        stmt = select(Document).where(
            Document.id == document_id,
            Document.organization_id == organization_id,
        )
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()

    if not doc:
        # issue #301 批次 2：文档不存在 = 工具失败。旧行为返回 "Document ... not
        # found." 普通字符串，消费端只能靠 `"not found" not in info` 内容启发式
        # 判断（既漏又可能误伤含该词的合法文件名/字段）。
        return ToolResult.fail(
            code="not_found",
            message=f"Document {document_id} not found.",
        )

    status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    return json.dumps(
        {
            "id": doc.id,
            "filename": doc.filename,
            "title": doc.title,
            "status": status_val,
            "file_size": doc.file_size,
            "chunk_count": doc.chunk_count,
            "description": doc.description,
            "keywords": doc.keywords or [],
            "created_at": doc.created_at.isoformat() if doc.created_at else None,
            "parsed_at": doc.parsed_at.isoformat() if doc.parsed_at else None,
            "indexed_at": doc.indexed_at.isoformat() if doc.indexed_at else None,
        },
        ensure_ascii=False,
        indent=2,
    )


# ─── Conversation Management Tools ──────────────────────────────────────────


@register_tool(
    name="list_conversations",
    description=(
        "List recent chat conversations for the current user. "
        "Returns session IDs, titles, message counts, and last activity."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum conversations to return (default 10)",
                "default": 10,
            },
        },
    },
    tags=["conversation", "management"],
    requires_auth=True,
    enabled_predicate=extended_surface_enabled,
)
async def list_conversations(
    limit: int = 10,
    user_id: int = 0,
    organization_id: int | None = None,
    **_: Any,
) -> str | ToolResult:
    if organization_id is None:
        return _missing_organization_result("list_conversations")
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.chat import ChatSession

    async with AsyncSessionLocal() as session:
        stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .where(ChatSession.organization_id == organization_id)
            .order_by(ChatSession.updated_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        sessions = result.scalars().all()

    if not sessions:
        return "No conversations found."

    output = []
    for s in sessions:
        title = s.title or "Untitled"
        count = s.message_count or 0
        updated = s.updated_at.strftime("%Y-%m-%d %H:%M") if s.updated_at else "?"
        output.append(f"- [{s.id[:8]}...] {title} | {count} msgs | {updated}")
    return "\n".join(output)


@register_tool(
    name="get_conversation_history",
    description=(
        "Retrieve the message history of a specific chat conversation. "
        "Returns messages in chronological order with role and content."
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "The chat session ID",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum messages to return (default 20)",
                "default": 20,
            },
        },
        "required": ["session_id"],
    },
    tags=["conversation"],
    requires_auth=True,
    enabled_predicate=extended_surface_enabled,
)
async def get_conversation_history(
    session_id: str,
    limit: int = 20,
    user_id: int = 0,
    organization_id: int | None = None,
    **_: Any,
) -> str:
    """返回指定会话的消息历史。

    口径（T4）：按会话的 ``organization_id`` 严格校验（历史 NULL 组织会话
    属本人，仍可读）。本工具**不随 API 的超管旁路放宽**——与同目录
    ``list_conversations`` 工具的严格组织口径保持一致。
    """
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.chat import ChatMessage, ChatSession

    async with AsyncSessionLocal() as session:
        # Verify ownership
        stmt = select(ChatSession).where(ChatSession.id == session_id)
        result = await session.execute(stmt)
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return f"Conversation {session_id} not found."
        if chat_session.user_id != user_id:
            return "Access denied: you do not own this conversation."
        # T4：按 id 读取与列表工具同一组织口径——不得读取其他组织的会话；
        # 历史 NULL 组织会话属本人，仍可读。
        if (
            chat_session.organization_id is not None
            and chat_session.organization_id != organization_id
        ):
            return "Access denied: conversation belongs to another organization."

        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq.asc(), ChatMessage.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        messages = result.scalars().all()

    if not messages:
        return f"Conversation '{chat_session.title or session_id}' has no messages."

    output = []
    for msg in messages:
        role = (
            msg.message_type.value if hasattr(msg.message_type, "value") else str(msg.message_type)
        )
        content = msg.content[:500] if msg.content else ""
        output.append(f"[{role}] {content}")
    return "\n\n".join(output)


# ─── Prompt Template Tools ──────────────────────────────────────────────────


@register_tool(
    name="list_prompt_templates",
    description=(
        "List available prompt templates. "
        "Returns template names, categories, and descriptions. "
        "Useful for finding reusable prompts for specific tasks."
    ),
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Filter by category (e.g. general, translation, summary)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum templates to return (default 20)",
                "default": 20,
            },
        },
    },
    tags=["prompts"],
    requires_auth=True,
    enabled_predicate=extended_surface_enabled,
)
async def list_prompt_templates(
    category: str | None = None,
    limit: int = 20,
    organization_id: int | None = None,
    user_id: int | None = None,
    **_: Any,
) -> str | ToolResult:
    from sqlalchemy import and_, or_, select

    from app.core.database import AsyncSessionLocal
    from app.models.prompt import PromptTemplate

    # A2 P0（租户边界）：提示词已按组织私有（org=NULL 仅系统模板只读可见），
    # 该工具默认对 Agent 开放，必须同源过滤，否则可枚举其他组织的私有模板。
    # B4（D6）：个人模板只对创建者可见，不得经 Agent 工具泄露给组织其他成员。
    if organization_id is None:
        return _missing_organization_result("list_prompt_templates")

    org_visible = and_(
        PromptTemplate.organization_id == organization_id,
        or_(
            PromptTemplate.scope == "organization",
            and_(
                PromptTemplate.scope == "personal",
                PromptTemplate.creator_id == user_id,
            ),
        )
        if user_id is not None
        else PromptTemplate.scope == "organization",
    )

    async with AsyncSessionLocal() as session:
        stmt = select(PromptTemplate).where(
            PromptTemplate.is_active,
            or_(
                org_visible,
                and_(
                    PromptTemplate.is_system.is_(True),
                    PromptTemplate.organization_id.is_(None),
                ),
            ),
        )
        if category:
            stmt = stmt.where(PromptTemplate.category == category)
        stmt = stmt.order_by(PromptTemplate.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        templates = result.scalars().all()

    if not templates:
        return "No prompt templates found."

    output = []
    for tpl in templates:
        if tpl.is_system:
            scope = "system"
        elif tpl.scope == "personal":
            scope = "personal"
        else:
            scope = "organization"
        output.append(
            f"- [{tpl.category}] {tpl.name} ({scope}) — {tpl.description or 'No description'}"
        )
    return "\n".join(output)


@register_tool(
    name="get_prompt_template",
    description=(
        "Get the full content of a specific prompt template by name. "
        "Use this to retrieve and apply a reusable prompt."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The prompt template name",
            },
        },
        "required": ["name"],
    },
    tags=["prompts"],
    requires_auth=True,
    enabled_predicate=extended_surface_enabled,
)
async def get_prompt_template(
    name: str,
    organization_id: int | None = None,
    user_id: int | None = None,
    **_: Any,
) -> str | ToolResult:
    from sqlalchemy import and_, or_, select

    from app.core.database import AsyncSessionLocal
    from app.models.prompt import PromptTemplate

    # A2 P0（租户边界）：只能读取本组织模板或系统内置模板，禁止跨租户读取全文。
    # B4（D6）：个人模板仅创建者可读。
    if organization_id is None:
        return _missing_organization_result("get_prompt_template")

    org_visible = and_(
        PromptTemplate.organization_id == organization_id,
        or_(
            PromptTemplate.scope == "organization",
            and_(
                PromptTemplate.scope == "personal",
                PromptTemplate.creator_id == user_id,
            ),
        )
        if user_id is not None
        else PromptTemplate.scope == "organization",
    )

    async with AsyncSessionLocal() as session:
        stmt = (
            select(PromptTemplate)
            .where(
                PromptTemplate.name == name,
                or_(
                    org_visible,
                    and_(
                        PromptTemplate.is_system.is_(True),
                        PromptTemplate.organization_id.is_(None),
                    ),
                ),
            )
            # 同名时优先个人模板（最具体）→ 组织模板 → 系统模板；limit(1) 防止
            # 历史重名触发 MultipleResultsFound（复合唯一约束只保证同组织内不重名）。
            .order_by(
                PromptTemplate.organization_id.is_(None),
                PromptTemplate.scope.desc(),
                PromptTemplate.id,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        tpl = result.scalars().first()

    if not tpl:
        return f"Prompt template '{name}' not found."

    return json.dumps(
        {
            "name": tpl.name,
            "content": tpl.content,
            "description": tpl.description,
            "category": tpl.category,
            "is_system": tpl.is_system,
            "scope": tpl.scope,
        },
        ensure_ascii=False,
        indent=2,
    )


# ─── Utility Tools ───────────────────────────────────────────────────────────


@register_tool(
    name="get_current_time",
    description="Get the current date and time. Useful for time-sensitive queries.",
    parameters={"type": "object", "properties": {}},
    tags=["utility"],
    # issue #509：能力已合并进 utility(operation=get_current_time)；保留注册与
    # handler（不删工具、旧名可用），但不再进 schema。
    hidden=True,
    superseded_by="utility",
)
async def get_current_time(**_: Any) -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Import extended tools to trigger registration ──
try:
    import app.agent.tools  # noqa: F401
except ImportError:
    pass
