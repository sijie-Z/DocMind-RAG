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
from typing import Any

from app.agent.registry import register_tool

logger = logging.getLogger(__name__)


# ─── Search Tools ────────────────────────────────────────────────────────────

@register_tool(
    name="search_knowledge_base",
    description=(
        "Search the enterprise knowledge base using hybrid retrieval "
        "(keyword + vector + RRF fusion). Returns ranked document snippets "
        "with relevance scores and source attribution."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default 5, max 20)",
                "default": 5,
            },
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: restrict search to specific document IDs",
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
    organization_id: int = 1,
    **_: Any,
) -> str:
    from app.dependencies import get_rag_pipeline
    pipeline = get_rag_pipeline()
    results = await pipeline.search_knowledge_base(
        query=query,
        organization_id=organization_id,
        top_k=min(top_k, 20),
        document_ids=document_ids,
    )
    if not results:
        return "No relevant documents found for this query."

    output = []
    for i, doc in enumerate(results, 1):
        score = doc.get("score", 0)
        filename = doc.get("filename", "Unknown")
        snippet = doc.get("snippet", doc.get("text", "")[:300])
        output.append(f"[{i}] ({score:.2f}) {filename}\n{snippet}")
    return "\n\n".join(output)


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
                "description": "Number of results (default 5)",
                "default": 5,
            },
        },
        "required": ["query"],
    },
    tags=["search"],
)
async def vector_search(
    query: str,
    top_k: int = 5,
    organization_id: int = 1,
    **_: Any,
) -> str:
    from app.dependencies import get_rag_pipeline
    pipeline = get_rag_pipeline()
    query_vector = await pipeline.get_embedding(query)
    if not query_vector:
        return "Embedding service unavailable."

    from app.core.elasticsearch import ElasticsearchTools
    es_query = {
        "size": top_k,
        "min_score": 1.15,
        "query": {
            "script_score": {
                "query": {"bool": {"filter": [{"term": {"organization_id": str(organization_id)}}]}},
                "script": {
                    "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                    "params": {"query_vector": query_vector},
                },
            }
        },
        "_source": ["chunk_text", "filename", "document_id"],
    }
    res = await ElasticsearchTools.search_documents(es_query)
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        return "No semantically similar documents found."

    output = []
    for i, hit in enumerate(hits, 1):
        src = hit.get("_source", {})
        score = hit.get("_score", 0)
        output.append(f"[{i}] ({score:.2f}) {src.get('filename', '?')}\n{src.get('content', '')[:300]}")
    return "\n\n".join(output)


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
)
async def summarize_document(
    document_id: str,
    organization_id: int = 1,
    **_: Any,
) -> str:
    from app.core.elasticsearch import ElasticsearchTools
    from app.dependencies import get_rag_pipeline

    es_query = {
        "size": 50,
        "query": {"term": {"document_id": document_id}},
        "sort": [{"chunk_index": {"order": "asc"}}],
        "_source": ["chunk_text", "filename"],
    }
    res = await ElasticsearchTools.search_documents(es_query)
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        return f"Document {document_id} not found in index."

    full_text = "\n".join(h.get("_source", {}).get("chunk_text", "") for h in hits)
    filename = hits[0].get("_source", {}).get("filename", "Unknown")

    # Use LLM to summarize
    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        return f"Document: {filename}\nContent preview: {full_text[:1000]}..."

    try:
        from app.core.config import settings
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是文档摘要专家。请用中文生成结构化摘要。"},
                {"role": "user", "content": f"请为以下文档生成摘要，包含：1)主题 2)关键要点(3-5条) 3)结论\n\n文档：{full_text[:6000]}"},
            ],
            temperature=0.1,
            max_tokens=500,
        )
        summary = resp.choices[0].message.content or "Summary generation failed."
        return f"Document: {filename}\n\n{summary}"
    except Exception as e:
        return f"Document: {filename}\nSummary failed: {e}"


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
)
async def extract_keywords(text: str, max_keywords: int = 10, **_: Any) -> str:
    from app.rag.query_processor import extract_query_terms
    terms = extract_query_terms(text)
    return json.dumps(terms[:max_keywords], ensure_ascii=False)


@register_tool(
    name="extract_insights",
    description=(
        "Deep analysis of a single document to extract structured insights: "
        "key entities (people, organizations, dates, locations), metrics and statistics, "
        "main claims and findings, and document section structure. "
        "Use this for in-depth document understanding beyond simple summarization."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The document ID to analyze",
            },
            "aspects": {
                "type": "string",
                "description": "Comma-separated aspects: entities,metrics,claims,structure,all",
                "default": "all",
            },
        },
        "required": ["document_id"],
    },
    tags=["analysis", "deep"],
)
async def extract_insights(
    document_id: str,
    aspects: str = "all",
    organization_id: int = 1,
    **_: Any,
) -> str:
    from app.core.config import settings
    from app.core.elasticsearch import ElasticsearchTools
    from app.dependencies import get_rag_pipeline

    es_query = {
        "size": 50,
        "query": {"term": {"document_id": document_id}},
        "sort": [{"chunk_index": {"order": "asc"}}],
        "_source": ["chunk_text", "filename", "section_title"],
    }
    res = await ElasticsearchTools.search_documents(es_query)
    hits = res.get("hits", {}).get("hits", [])
    if not hits:
        return json.dumps({"error": f"Document {document_id} not found in index."}, ensure_ascii=False)

    filename = hits[0].get("_source", {}).get("filename", "Unknown")
    full_text = "\n".join(
        h.get("_source", {}).get("chunk_text", "") for h in hits
    )
    text_sample = full_text[:8000]

    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        return json.dumps({
            "filename": filename,
            "document_id": document_id,
            "insights": {"error": "LLM unavailable"},
            "raw_preview": text_sample[:2000],
        }, ensure_ascii=False)

    aspect_instructions = {
        "entities": "提取文档中的关键实体：人物、组织、地点、日期、专有名词。",
        "metrics": "提取文档中的所有数据指标、统计数字、量化结果。",
        "claims": "提取文档的核心观点、关键结论、主要论据。",
        "structure": "分析文档的章节结构、层次关系、段落组织方式。",
    }

    selected = [a.strip() for a in aspects.split(",")]
    if "all" in selected:
        instructions = "\n".join(aspect_instructions.values())
    else:
        instructions = "\n".join(
            v for k, v in aspect_instructions.items() if k in selected
        )

    try:
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是文档分析专家。分析文档内容，提取结构化信息。只返回 JSON，不要包含任何解释。"},
                {"role": "user", "content": (
                    f"分析以下文档，{instructions}\n\n"
                    f"文档：{filename}\n\n"
                    f"内容：{text_sample}\n\n"
                    "返回 JSON 格式：\n"
                    '{"entities": {"people": [], "organizations": [], "locations": [], "dates": []}, '
                    '"metrics": [{"name": "...", "value": "..."}], '
                    '"claims": [{"claim": "...", "confidence": "high/medium/low"}], '
                    '"structure": [{"level": 1, "heading": "..."}]}'
                )},
            ],
            temperature=0.1,
            max_tokens=1500,
            timeout=30,
        )
        insights_raw = resp.choices[0].message.content or "{}"
        # Try to parse as JSON; fallback to raw string
        try:
            insights = json.loads(insights_raw)
        except json.JSONDecodeError:
            insights = {"raw": insights_raw}

        return json.dumps({
            "filename": filename,
            "document_id": document_id,
            "chunk_count": len(hits),
            "content_length": len(full_text),
            "insights": insights,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "filename": filename,
            "document_id": document_id,
            "error": f"Analysis failed: {e}",
            "raw_preview": text_sample[:2000],
        }, ensure_ascii=False, indent=2)


@register_tool(
    name="cross_document_analysis",
    description=(
        "Analyze patterns, themes, and relationships across multiple documents. "
        "Finds common themes, unique aspects, contradictions, and complementary information. "
        "Use this for multi-document research, literature review, and comparative analysis."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of document IDs to analyze (2-10 documents)",
            },
            "analysis_type": {
                "type": "string",
                "description": "Analysis focus: common_themes, differences, trends, comprehensive",
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
    organization_id: int = 1,
    **_: Any,
) -> str:
    if not document_ids or len(document_ids) < 2:
        return json.dumps({"error": "Please provide at least 2 document IDs."}, ensure_ascii=False)
    if len(document_ids) > 10:
        return json.dumps({"error": "Maximum 10 documents allowed."}, ensure_ascii=False)

    from app.core.config import settings
    from app.core.elasticsearch import ElasticsearchTools
    from app.dependencies import get_rag_pipeline

    docs_data = []
    for doc_id in document_ids:
        es_query = {
            "size": 20,
            "query": {"term": {"document_id": doc_id}},
            "_source": ["chunk_text", "filename", "upload_time", "document_id"],
        }
        try:
            res = await ElasticsearchTools.search_documents(es_query)
            hits = res.get("hits", {}).get("hits", [])
            # Fallback: try filename match if ID lookup returned nothing
            if not hits:
                fallback_query = {
                    "size": 20,
                    "query": {"term": {"filename": doc_id}},
                    "_source": ["chunk_text", "filename", "upload_time", "document_id"],
                }
                res = await ElasticsearchTools.search_documents(fallback_query)
                hits = res.get("hits", {}).get("hits", [])
            if hits:
                text = " ".join(
                    h.get("_source", {}).get("chunk_text", "")[:500]
                    for h in hits[:10]
                )
                real_id = hits[0].get("_source", {}).get("document_id", doc_id)
                docs_data.append({
                    "id": real_id,
                    "filename": hits[0].get("_source", {}).get("filename", doc_id),
                    "text": text[:3000],
                    "chunks": len(hits),
                })
            else:
                docs_data.append({"id": doc_id, "filename": "Not found", "text": "", "chunks": 0})
        except Exception as e:
            docs_data.append({"id": doc_id, "filename": f"Error: {e}", "text": "", "chunks": 0})

    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        return json.dumps({"documents": docs_data, "analysis": "LLM unavailable"}, ensure_ascii=False)

    doc_list_text = "\n\n".join(
        f"--- 文档 {i+1}: {d['filename']} ---\n{d['text']}"
        for i, d in enumerate(docs_data) if d['text']
    )

    analysis_prompts = {
        "common_themes": "找出所有文档共同的主题、重复出现的关键词和共享的观点。",
        "differences": "找出文档之间的主要差异、独特观点和矛盾之处。",
        "trends": "分析文档之间的时间趋势、演进关系和变化模式。",
        "comprehensive": "全面分析：共同主题、差异、趋势、互补关系和整体知识图谱。",
    }

    try:
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是跨文档分析专家。分析多个文档之间的关系和模式。只返回 JSON。"},
                {"role": "user", "content": (
                    f"{analysis_prompts.get(analysis_type, analysis_prompts['comprehensive'])}\n\n"
                    f"文档列表：\n{doc_list_text[:10000]}\n\n"
                    "返回 JSON 格式：\n"
                    '{"summary": "整体分析总结", '
                    '"common_themes": [{"theme": "...", "evidence": ["...", "..."], "documents": [0, 1]}], '
                    '"unique_findings": [{"document_index": 0, "finding": "..."}], '
                    '"contradictions": [{"description": "...", "between": [0, 2]}], '
                    '"complementary_info": [{"description": "..."}], '
                    '"conclusion": "综合分析结论"}'
                )},
            ],
            temperature=0.1,
            max_tokens=2000,
            timeout=60,
        )
        analysis_raw = resp.choices[0].message.content or "{}"
        try:
            analysis = json.loads(analysis_raw)
        except json.JSONDecodeError:
            analysis = {"raw": analysis_raw}

        return json.dumps({
            "document_count": len(docs_data),
            "analysis_type": analysis_type,
            "documents": [{"id": d["id"], "filename": d["filename"], "chunks": d["chunks"]} for d in docs_data],
            "analysis": analysis,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({
            "document_count": len(docs_data),
            "error": f"Analysis failed: {e}",
            "documents": [{"filename": d["filename"]} for d in docs_data],
        }, ensure_ascii=False, indent=2)


@register_tool(
    name="generate_report",
    description=(
        "Generate a structured analysis report in markdown format. "
        "Takes analysis findings and produces a professional report with "
        "executive summary, detailed findings, tables, and conclusions. "
        "Use this to produce polished, shareable analysis output."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Report title",
            },
            "sections": {
                "type": "string",
                "description": (
                    "JSON array of section objects. Each section: "
                    '{"heading": "...", "content": "...", "type": "text|table|list"}'
                ),
            },
            "format": {
                "type": "string",
                "description": "Output format (markdown, json)",
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
    **_: Any,
) -> str:
    from app.core.config import settings
    from app.dependencies import get_rag_pipeline

    # Parse sections
    try:
        parsed_sections = json.loads(sections)
        if not isinstance(parsed_sections, list):
            return json.dumps({"error": "sections must be a JSON array"}, ensure_ascii=False)
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"Invalid sections JSON: {e}"}, ensure_ascii=False)

    if format == "json":
        return json.dumps({
            "title": title,
            "sections": parsed_sections,
            "generated_at": __import__("datetime").datetime.now().isoformat(),
        }, ensure_ascii=False, indent=2)

    # Use LLM to generate a polished markdown report
    pipeline = get_rag_pipeline()
    if not pipeline.openai_client:
        return _generate_markdown_fallback(title, parsed_sections)

    sections_text = "\n\n".join(
        f"## {s.get('heading', 'Section ' + str(i+1))}\n"
        f"{s.get('content', '')}"
        for i, s in enumerate(parsed_sections)
    )

    try:
        resp = await pipeline.openai_client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": "你是报告生成专家。根据提供的内容生成结构清晰、专业的 Markdown 报告。"},
                {"role": "user", "content": (
                    f"请根据以下内容生成一份专业的分析报告。使用 Markdown 格式，包含：\n"
                    f"1. 标题和元数据\n"
                    f"2. 执行摘要\n"
                    f"3. 详细分析（用表格、列表、引用等丰富格式）\n"
                    f"4. 结论与建议\n\n"
                    f"报告标题：{title}\n\n"
                    f"内容：\n{sections_text[:8000]}"
                )},
            ],
            temperature=0.2,
            max_tokens=3000,
            timeout=60,
        )
        report = resp.choices[0].message.content or ""
        if not report.strip():
            return _generate_markdown_fallback(title, parsed_sections)
        return report
    except Exception as e:
        logger.warning(f"Report generation LLM call failed: {e}")
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
        content = section.get("chunk_text", "")
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
    description=(
        "List all documents in the knowledge base for the current organization. "
        "Returns document IDs, filenames, status, and upload dates."
    ),
    parameters={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum documents to return (default 20)",
                "default": 20,
            },
            "status": {
                "type": "string",
                "description": "Filter by status: indexed, parsing, failed, etc.",
            },
        },
    },
    tags=["management"],
    requires_auth=True,
)
async def list_documents(
    limit: int = 20,
    status: str | None = None,
    organization_id: int = 1,
    **_: Any,
) -> str:
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
        created = doc.created_at.strftime('%Y-%m-%d') if doc.created_at else '?'
        title = doc.title or doc.filename
        output.append(f"- ID: {doc.id} | 文件名: {doc.filename} | 标题: {title} | 状态: {status_val} | 创建: {created}")
    return "\n".join(output)


@register_tool(
    name="get_document_info",
    description=(
        "Get detailed information about a specific document including "
        "its status, chunk count, file size, and metadata."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_id": {
                "type": "string",
                "description": "The document ID",
            },
        },
        "required": ["document_id"],
    },
    tags=["management"],
)
async def get_document_info(document_id: str, **_: Any) -> str:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.document import Document

    async with AsyncSessionLocal() as session:
        stmt = select(Document).where(Document.id == document_id)
        result = await session.execute(stmt)
        doc = result.scalar_one_or_none()

    if not doc:
        return f"Document {document_id} not found."

    status_val = doc.status.value if hasattr(doc.status, "value") else str(doc.status)
    return json.dumps({
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
    }, ensure_ascii=False, indent=2)


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
)
async def list_conversations(
    limit: int = 10,
    user_id: int = 0,
    organization_id: int = 1,
    **_: Any,
) -> str:
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
)
async def get_conversation_history(
    session_id: str,
    limit: int = 20,
    user_id: int = 0,
    **_: Any,
) -> str:
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

        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        messages = result.scalars().all()

    if not messages:
        return f"Conversation '{chat_session.title or session_id}' has no messages."

    output = []
    for msg in messages:
        role = msg.message_type.value if hasattr(msg.message_type, "value") else str(msg.message_type)
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
)
async def list_prompt_templates(
    category: str | None = None,
    limit: int = 20,
    **_: Any,
) -> str:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.prompt import PromptTemplate

    async with AsyncSessionLocal() as session:
        stmt = select(PromptTemplate).where(PromptTemplate.is_active)
        if category:
            stmt = stmt.where(PromptTemplate.category == category)
        stmt = stmt.order_by(PromptTemplate.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        templates = result.scalars().all()

    if not templates:
        return "No prompt templates found."

    output = []
    for tpl in templates:
        scope = "system" if tpl.is_system else "user"
        output.append(f"- [{tpl.category}] {tpl.name} ({scope}) — {tpl.description or 'No description'}")
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
)
async def get_prompt_template(name: str, **_: Any) -> str:
    from sqlalchemy import select

    from app.core.database import AsyncSessionLocal
    from app.models.prompt import PromptTemplate

    async with AsyncSessionLocal() as session:
        stmt = select(PromptTemplate).where(PromptTemplate.name == name)
        result = await session.execute(stmt)
        tpl = result.scalar_one_or_none()

    if not tpl:
        return f"Prompt template '{name}' not found."

    return json.dumps({
        "name": tpl.name,
        "content": tpl.content,
        "description": tpl.description,
        "category": tpl.category,
        "is_system": tpl.is_system,
    }, ensure_ascii=False, indent=2)


# ─── Utility Tools ───────────────────────────────────────────────────────────

@register_tool(
    name="get_current_time",
    description="Get the current date and time. Useful for time-sensitive queries.",
    parameters={"type": "object", "properties": {}},
    tags=["utility"],
)
async def get_current_time(**_: Any) -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ── Import extended tools to trigger registration ──
try:
    import app.agent.tools  # noqa: F401
except ImportError:
    pass
