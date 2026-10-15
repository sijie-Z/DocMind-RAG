"""Skill tools — high-level workflow tools that orchestrate multiple atomic tools.

Each Skill is registered as a regular @register_tool. Internally it calls
tool_registry.execute_detailed() to compose multiple atomic operations and
gates every consumed output through a structured consumption check (issue #301
批次 2：字符串启发式升级为结构化判定).

This is V1 — Skills appear in the LLM's tool list alongside atomic tools,
and the LLM decides when to use them. No automatic Planner matching yet.
"""

import json
import logging
from typing import Any

from app.agent.registry import register_tool, tool_registry

logger = logging.getLogger(__name__)


def _validated_insight_payload(raw: str | None) -> dict[str, Any] | None:
    """校验 extract_insights 的返回后才允许消费（issue #301 #12）。

    错误形态一律返回 None —— 不得被当成"分析结果"写进报告/叙述：
      - ToolResult.fail 的字符串形态 "Error: [code] ..."（#293 契约）；
      - 带顶层 "error" 键的 JSON 载荷（历史错误形态）；
      - 无法解析 / 非对象 / 缺 "insights" 字段的未知形态。

    判别只做结构判定（能否解析 + 字段形态），不做大小写无关的子串匹配 ——
    旧检查 `"Error" not in insight` 既漏（生产者发小写 "error" 键）又可能
    误伤（合法的分析正文里出现 "Error rate..." 等词；见 #297 教训：内容
    启发式的误判无法根除）。成功载荷的结构锚点 = 顶层 "insights" 字段
    （extract_insights 成功路径恒有该字段）。
    """
    if not raw:
        return None
    if raw.lstrip().startswith("Error:"):
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("error"):
        return None
    if "insights" not in parsed:
        return None
    return parsed


def _legacy_error_envelope(raw: str | None) -> bool:
    """字符串载荷是否是历史遗留的 ``{"error": ...}`` 错误信封（issue #301 批次 2）。

    部分工具（如 cross_document_analysis / generate_report 的旧出口）历史上把
    失败包成"正常" JSON 返回（顶层小写 error 键）—— 旧的 ``"Error" not in x``
    子串启发式对小写键漏检，错误载荷会被当结果写进复合技能的报告（trustB
    评审现场复现：cross 错误载荷经 document_comparison Step 3 漏进报告）。

    判别只做结构判定：能解析为 dict 且 error 键为真值。合法正文即便含
    "Error..." 字样、或把 error 作为嵌套/列表内字段，都不在此拒收
    （#297 教训：内容启发式的误判无法根除）。
    """
    if not raw:
        return False
    stripped = raw.lstrip()
    if not stripped.startswith("{"):
        return False
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict) and bool(parsed.get("error"))


async def _consume_tool_output(
    name: str, arguments: dict[str, Any], **context: Any
) -> tuple[bool, str]:
    """执行工具并返回 ``(是否可消费, 字符串形态)`` —— 结构化消费闸门。

    issue #301 批次 2：旧 ``"Error" not in x`` 字符串启发式升级为结构化判定：
      - ``ToolResult.success=False``（#293/#297 契约，经 #297/#12 迁移的
        工具家族失败即此形态）→ 拒收；
      - success 但字符串载荷是历史 ``{"error": ...}`` 错误信封 → 拒收；
      - 其余（含正文出现 "Error" 字样的合法输出）→ 可消费。

    只做结构化判定，绝不按字符串内容猜失败 —— 内容透传型工具的成功输出
    可以合法地长得像错误串（如含 "Error rate..." 字样的分析正文），内容
    启发式的误判无法根除（#297 教训）。
    """
    result = await tool_registry.execute_detailed(name, arguments, **context)
    text = str(result)
    if not result.success:
        return False, text
    if _legacy_error_envelope(text):
        return False, text
    return True, text


@register_tool(
    name="deep_research",
    description=(
        "对某个主题进行深度研究：先搜索外部信息，再检索知识库，"
        "然后提取结构化摘要。适用于需要综合外部和内部信息的研究类问题。"
        "参数: query(研究主题), depth(深度: basic/detailed, 默认detailed)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "研究主题或问题",
            },
            "depth": {
                "type": "string",
                "description": "研究深度: basic(快速摘要) 或 detailed(详细分析)",
                "enum": ["basic", "detailed"],
                "default": "detailed",
            },
        },
        "required": ["query"],
    },
    tags=["skill", "research"],
)
async def deep_research(
    query: str,
    depth: str = "detailed",
    organization_id: int = 1,
    run_context=None,
    **_: Any,
) -> str:
    """深度研究：搜索 → 知识库检索 → 结构化摘要"""
    parts: list[str] = [f"# 深度研究: {query}\n"]

    # Step 1: 搜索外部信息
    try:
        ok, web_result = await _consume_tool_output(
            "web_search",
            {"query": query, "max_results": 5},
            organization_id=organization_id,
            run_context=run_context,
        )
        if ok:
            parts.append("## 外部搜索结果\n" + web_result[:1500])
        else:
            # 结构化失败（如 rate_limited）不再被静默丢弃：如实标注。
            logger.warning(
                "deep_research: web_search returned no usable output: %s", web_result[:120]
            )
            parts.append("## 外部搜索结果\n(搜索服务暂不可用)\n")
    except Exception as e:
        logger.warning(f"deep_research: web_search failed: {e}")
        parts.append("## 外部搜索结果\n(搜索服务暂不可用)\n")

    # Step 2: 搜索内部知识库
    try:
        ok, kb_result = await _consume_tool_output(
            "search_knowledge_base",
            {"query": query, "top_k": 5},
            organization_id=organization_id,
            run_context=run_context,
        )
        # "No relevant ..." 是 search_knowledge_base 成功态的固有空结果哨兵
        # （非失败信号），空结果不设"知识库结果"节。
        if ok and "No relevant" not in kb_result:
            parts.append("## 知识库结果\n" + kb_result[:1500])
        elif not ok:
            logger.warning(
                "deep_research: search_knowledge_base returned no usable output: %s",
                kb_result[:120],
            )
            parts.append("## 知识库结果\n(知识库检索暂不可用)\n")
    except Exception as e:
        logger.warning(f"deep_research: search_kb failed: {e}")

    # Step 3: 简要总结（detailed 模式额外提示）
    parts.append(f"\n## 综合摘要\n（依据以上信息综合分析 {query} 的研究结果）")

    return "\n\n".join(parts)


@register_tool(
    name="document_comparison",
    description=(
        "对比多个文档：分别提取关键信息，然后找出异同和互补关系。"
        "适用于需要对比两份或多份文档的场景。"
        "参数: document_ids(文档ID列表，至少2个), focus(对比重点: comprehensive/financial/risk)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要对比的文档ID列表 (2-5个)",
                "minItems": 2,
                "maxItems": 5,
            },
            "focus": {
                "type": "string",
                "description": "对比重点",
                "enum": ["comprehensive", "financial", "risk"],
                "default": "comprehensive",
            },
        },
        "required": ["document_ids"],
    },
    tags=["skill", "analysis"],
)
async def document_comparison(
    document_ids: list[str],
    focus: str = "comprehensive",
    organization_id: int = 1,
    run_context=None,
    **_: Any,
) -> str:
    """文档对比：分别摘要 → cross_document_analysis"""
    if not document_ids or len(document_ids) < 2:
        return "请至少提供2个文档ID进行对比。"

    doc_ids = document_ids[:5]
    parts: list[str] = [
        f"# 文档对比分析 ({len(doc_ids)} 份文档)\n",
    ]

    # Step 1: 获取每份文档的基础信息
    for i, doc_id in enumerate(doc_ids, 1):
        try:
            ok, info = await _consume_tool_output(
                "get_document_info",
                {"document_id": doc_id},
                organization_id=organization_id,
                run_context=run_context,
            )
            if ok:
                parts.append(f"## 文档 {i}\n{info[:500]}")
            else:
                logger.warning(
                    "document_comparison: get_document_info returned no usable output "
                    "for %s: %s",
                    doc_id, info[:120],
                )
                parts.append(f"## 文档 {i}\n(无法获取文档信息: {doc_id})\n")
        except Exception as e:
            logger.warning(f"document_comparison: get_doc_info failed for {doc_id}: {e}")
            parts.append(f"## 文档 {i}\n(无法获取文档信息: {doc_id})\n")

    # Step 2: 提取分析框架
    framework_map = {
        "comprehensive": "comprehensive",
        "financial": "financial_health",
        "risk": "contract_risk",
    }
    analysis_framework = framework_map.get(focus, "comprehensive")

    for doc_id in doc_ids:
        try:
            ok, insight = await _consume_tool_output(
                "extract_insights",
                {
                    "document_id": doc_id,
                    "aspects": "all",
                    "framework": analysis_framework,
                },
                organization_id=organization_id,
                run_context=run_context,
            )
        except Exception as e:
            logger.warning(f"document_comparison: extract_insights failed for {doc_id}: {e}")
            parts.append(f"### 文档分析\n(洞察提取失败: {doc_id})\n")
            continue

        # issue #301 #12：先校验再消费 —— 错误载荷不得当"分析结果"写进报告。
        parsed = _validated_insight_payload(insight) if ok else None
        if parsed is None:
            logger.warning(
                "document_comparison: extract_insights returned no usable analysis for %s: %s",
                doc_id, str(insight)[:120],
            )
            parts.append(f"### 文档分析\n(洞察提取失败: {doc_id})\n")
            continue

        filename = parsed.get("filename", doc_id)
        insights = parsed.get("insights", {})
        summary = f"- 文档: {filename}\n"
        if isinstance(insights, dict):
            if "claims" in insights:
                claims = insights["claims"]
                summary += f"- 关键发现: {len(claims)} 条\n"
            if "structure" in insights:
                summary += f"- 章节: {len(insights['structure'])} 个\n"
        parts.append(f"### {filename} 分析摘要\n{summary}")

    # Step 3: 跨文档综合分析
    try:
        ok, cross = await _consume_tool_output(
            "cross_document_analysis",
            {"document_ids": doc_ids, "analysis_type": "comprehensive"},
            organization_id=organization_id,
            run_context=run_context,
        )
        if ok:
            parts.append("## 跨文档综合分析\n" + cross[:2000])
        else:
            # trustB 评审现场复现过的信任边界缺口：cross_document_analysis 的
            # {"error": ...} 载荷曾躲过 `"Error" not in cross`（小写键）漏进报告。
            # 结构化闸门下逐条拒收 + 如实标注。
            logger.warning(
                "document_comparison: cross_document_analysis returned no usable output: %s",
                cross[:120],
            )
            parts.append("## 跨文档综合分析\n(分析服务暂不可用)\n")
    except Exception as e:
        logger.warning(f"document_comparison: cross_doc failed: {e}")
        parts.append("## 跨文档综合分析\n(分析服务暂不可用)\n")

    return "\n\n".join(parts)


@register_tool(
    name="knowledge_analysis",
    description=(
        "对知识库中的文档进行结构化分析：先搜索相关文档，"
        "然后使用指定的分析框架（SWOT/杜邦/PEST/财务健康/合同风险）提取洞察，"
        "最后生成结构化的分析报告。"
        "参数: query(分析主题), framework(分析框架), generate_report(是否生成报告)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "分析主题或搜索关键词",
            },
            "framework": {
                "type": "string",
                "description": "分析框架",
                "enum": ["swot", "dupont", "pest", "financial_health", "contract_risk", "comprehensive"],
                "default": "comprehensive",
            },
            "generate_report": {
                "type": "boolean",
                "description": "是否生成最终报告 (默认 true)",
                "default": True,
            },
        },
        "required": ["query"],
    },
    tags=["skill", "analysis"],
)
async def knowledge_analysis(
    query: str,
    framework: str = "comprehensive",
    generate_report: bool = True,
    organization_id: int = 1,
    run_context=None,
    **_: Any,
) -> str:
    """知识分析：搜索 → 框架分析 → 报告生成"""
    parts: list[str] = [f"# 知识分析: {query}\n"]

    # Step 1: 搜索相关文档
    doc_ids: list[str] = []
    try:
        ok, docs_result = await _consume_tool_output(
            "search_knowledge_base",
            {"query": query, "top_k": 3},
            organization_id=organization_id,
            run_context=run_context,
        )
        # "No relevant ..." 是 search_knowledge_base 成功态的固有空结果哨兵
        # （非失败信号），空结果不设"检索到的文档"节。
        if ok and "No relevant" not in docs_result:
            parts.append("## 检索到的文档\n" + docs_result[:1000])
            # 从结果中尝试提取文档ID
            for line in docs_result.split("\n"):
                if "ID:" in line:
                    # Format: "- ID: xxx | 文件名: ..."
                    try:
                        doc_id = line.split("ID:")[1].split("|")[0].strip() if "|" in line else ""
                        if doc_id:
                            doc_ids.append(doc_id)
                    except (IndexError, ValueError):
                        pass
        elif not ok:
            logger.warning(
                "knowledge_analysis: search_knowledge_base returned no usable output: %s",
                docs_result[:120],
            )
            parts.append("## 检索到的文档\n(检索服务暂不可用)\n")
    except Exception as e:
        logger.warning(f"knowledge_analysis: search_kb failed: {e}")
        parts.append("## 检索到的文档\n(检索服务暂不可用)\n")

    # Step 2: 框架分析
    analysis_results = []
    failed_count = 0
    for doc_id in doc_ids[:3]:
        try:
            ok, insight = await _consume_tool_output(
                "extract_insights",
                {
                    "document_id": doc_id,
                    "aspects": "all",
                    "framework": framework if framework != "comprehensive" else "",
                },
                organization_id=organization_id,
                run_context=run_context,
            )
        except Exception as e:
            logger.warning(f"knowledge_analysis: extract_insights failed: {e}")
            failed_count += 1
            continue

        # issue #301 #12：先校验再消费 —— 旧检查 `"Error" not in insight` 对
        # 小写 "error" 键漏检，错误载荷被当"分析结果"写进报告。
        parsed = _validated_insight_payload(insight) if ok else None
        if parsed is None:
            failed_count += 1
            logger.warning(
                "knowledge_analysis: extract_insights returned no usable analysis for %s: %s",
                doc_id, str(insight)[:120],
            )
            continue
        analysis_results.append(insight)

    framework_label = framework.upper() if framework != "comprehensive" else "综合"
    if analysis_results:
        combined = "\n\n".join(
            r[:800] for r in analysis_results
        )
        analysis_section = f"## {framework_label}分析\n{combined}"
        if failed_count:
            # 部分失败如实标注：成功结果照常呈现，但不得让读者以为全部提取成功。
            analysis_section += f"\n(另有 {failed_count} 份文档洞察提取失败，未纳入分析)"
        parts.append(analysis_section)
    elif failed_count:
        # 提取全部失败 ≠ "未找到可分析的文档"（那是另一种事实）。
        parts.append(f"## {framework_label}分析\n(洞察提取失败：{failed_count} 份文档处理异常)\n")
    else:
        parts.append(f"## {framework_label}分析\n(未找到可分析的文档)\n")

    # Step 3: 生成报告（可选）
    if generate_report and analysis_results:
        try:
            report_title = f"{query} - {framework.upper() if framework != 'comprehensive' else '综合'}分析报告"
            sections = json.dumps([
                {"heading": "分析概述", "content": f"基于对知识库中 {len(doc_ids)} 份文档的 {framework} 分析", "type": "text"},
                {"heading": "分析结果", "content": json.dumps(analysis_results, ensure_ascii=False)[:3000], "type": "text"},
                {"heading": "结论与建议", "content": "请根据以上分析结果得出结论。", "type": "text"},
            ], ensure_ascii=False)
            ok, report = await _consume_tool_output(
                "generate_report",
                {"title": report_title, "sections": sections},
                organization_id=organization_id,
                run_context=run_context,
            )
            if ok:
                parts.append("## 生成报告\n" + report[:2000])
            else:
                logger.warning(
                    "knowledge_analysis: generate_report returned no usable output: %s",
                    report[:120],
                )
                parts.append("## 生成报告\n(报告生成失败)\n")
        except Exception as e:
            logger.warning(f"knowledge_analysis: generate_report failed: {e}")

    return "\n\n".join(parts)
