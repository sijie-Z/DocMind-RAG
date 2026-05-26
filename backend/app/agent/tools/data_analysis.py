"""Data analysis tools — statistics, document comparison, structured analysis."""

import json
import logging
from typing import Any

from app.agent.registry import register_tool

logger = logging.getLogger(__name__)


@register_tool(
    name="analyze_data",
    description=(
        "Perform statistical analysis on numeric data. Input a list of numbers "
        "(JSON array) to get: count, sum, mean, median, min, max, range, "
        "variance, standard deviation, quartiles (Q1, Q2, Q3), and trend "
        "direction if the data is a time series."
    ),
    parameters={
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "description": "JSON array of numbers, e.g. [1, 2, 3, 4, 5], or JSON array of [label, value] pairs",
            },
        },
        "required": ["data"],
    },
    tags=["analysis", "data"],
)
async def analyze_data(data: str, **_: Any) -> str:
    try:
        values = json.loads(data)
    except json.JSONDecodeError:
        return "Error: Invalid JSON. Please provide a JSON array of numbers."

    if not isinstance(values, list) or not values:
        return "Error: Please provide a non-empty JSON array."

    # Handle [label, value] pairs
    if isinstance(values[0], list) and len(values[0]) == 2:
        [v[0] for v in values]
        numbers = []
        for v in values:
            try:
                numbers.append(float(v[1]))
            except (TypeError, ValueError):
                return f"Error: '{v[1]}' is not a valid number."
    else:
        try:
            numbers = [float(v) for v in values]
        except (TypeError, ValueError):
            return "Error: All array elements must be numbers."

    try:
        import statistics

        n = len(numbers)
        total = sum(numbers)
        mean = statistics.mean(numbers)
        median = statistics.median(numbers)
        sorted_nums = sorted(numbers)
        q1 = statistics.median(sorted_nums[:n // 2])
        q3 = statistics.median(sorted_nums[(n + 1) // 2:])

        result = {
            "count": n,
            "sum": round(total, 4),
            "mean": round(mean, 4),
            "median": round(median, 4),
            "min": round(min(numbers), 4),
            "max": round(max(numbers), 4),
            "range": round(max(numbers) - min(numbers), 4),
            "q1": round(q1, 4),
            "q3": round(q3, 4),
            "iqr": round(q3 - q1, 4),
        }

        if n >= 2:
            result["variance"] = round(statistics.variance(numbers) if n > 1 else 0, 4)
            result["std_dev"] = round(statistics.stdev(numbers) if n > 1 else 0, 4)

        # Trend detection
        if n >= 3:
            first_half = statistics.mean(numbers[:n // 2])
            second_half = statistics.mean(numbers[n // 2:])
            if second_half > first_half * 1.05:
                result["trend"] = "上升 ↗"
            elif second_half < first_half * 0.95:
                result["trend"] = "下降 ↘"
            else:
                result["trend"] = "平稳 →"

        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Analysis error: {type(e).__name__}: {e}"


@register_tool(
    name="compare_documents",
    description=(
        "Compare two or more documents from the knowledge base. "
        "Finds similarities, differences, and key themes across documents. "
        "Use this when users ask to 'compare', 'contrast', or 'find differences between' documents."
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of document IDs to compare (2-5 documents)",
            },
            "aspect": {
                "type": "string",
                "description": "Comparison aspect: general, topics, statistics, methodology, conclusions",
                "default": "general",
            },
        },
        "required": ["document_ids"],
    },
    tags=["analysis", "documents"],
)
async def compare_documents(
    document_ids: list[str],
    aspect: str = "general",
    **_: Any,
) -> str:
    if not document_ids or len(document_ids) < 2:
        return "Error: Please provide at least 2 document IDs to compare."
    if len(document_ids) > 5:
        return "Error: Maximum 5 documents can be compared at once."

    from app.core.elasticsearch import ElasticsearchTools

    summaries = []
    for doc_id in document_ids:
        es_query = {
            "size": 30,
            "query": {"term": {"document_id": doc_id}},
            "_source": ["chunk_text", "filename"],
        }
        try:
            res = await ElasticsearchTools.search_documents(es_query)
            hits = res.get("hits", {}).get("hits", [])
            if hits:
                filename = hits[0].get("_source", {}).get("filename", doc_id)
                text = " ".join(h.get("_source", {}).get("chunk_text", "")[:500] for h in hits[:10])
                summaries.append({
                    "id": doc_id,
                    "filename": filename,
                    "text": text[:2000],
                    "chunk_count": len(hits),
                })
            else:
                summaries.append({
                    "id": doc_id,
                    "filename": "Not found",
                    "text": "",
                    "chunk_count": 0,
                })
        except Exception as e:
            summaries.append({
                "id": doc_id,
                "filename": f"Error: {e}",
                "text": "",
                "chunk_count": 0,
            })

    # Build comparison output
    aspect_prompts = {
        "general": "请对比以下文档的主要内容、关键发现和结论",
        "topics": "请对比以下文档的核心主题和关键词",
        "statistics": "请对比以下文档中的数据、统计和量化指标",
        "methodology": "请对比以下文档的方法论、研究方法和分析框架",
        "conclusions": "请对比以下文档的结论、建议和行动计划",
    }

    output_parts = [
        f"## 文档对比分析 ({aspect_prompts.get(aspect, aspect_prompts['general'])})",
    ]

    for s in summaries:
        output_parts.append(f"\n### {s['filename']} ({s['id'][:8]}...)\n"
                          f"Chunks: {s['chunk_count']}\n"
                          f"Preview: {s['text'][:300]}...")

    output_parts.append("\n---")
    output_parts.append(
        "\n请基于以上文档内容进行对比分析。提示：可以进一步使用 summarize_document "
        "工具获取每个文档的详细摘要。"
    )

    return "\n".join(output_parts)
