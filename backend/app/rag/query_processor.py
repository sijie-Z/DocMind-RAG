"""Query processing — intent classification, query rewrite, HyDE generation."""
import json
import logging
import re
from typing import List, Dict, Optional, Literal

from app.core.config import settings

logger = logging.getLogger(__name__)

QueryIntent = Literal["factual", "procedural", "list", "definition", "comparison", "causal", "summary", "other"]
QueryComplexity = Literal["simple", "medium", "complex"]
RetrievalStrategy = Literal["keyword_only", "hybrid", "hybrid_hyde"]


class QueryComplexityClassifier:
    """Classifies query complexity to adapt retrieval strategy (Adaptive RAG).

    - simple:  keyword-only search (fast, no embedding calls)
    - medium:  hybrid keyword + vector (default)
    - complex: hybrid + HyDE + multi-rewrite (full pipeline)
    """

    COMPLEXITY_SIGNALS = {
        "high": ["对比", "比较", "区别", "优缺点", "分析", "评估", "综合", "总结",
                  "为什么", "原因", "影响", "趋势", "策略", "方案", "建议",
                  "compare", "analyze", "evaluate", "synthesize", "trade-off"],
        "medium": ["如何", "怎么", "步骤", "流程", "有哪些", "列出", "解释",
                    "how", "explain", "list", "describe", "define"],
    }

    @classmethod
    def classify(cls, query: str) -> QueryComplexity:
        q = query.strip().lower()
        if not q:
            return "simple"

        # Length signal: very short queries are usually simple lookups
        if len(q) <= 8:
            return "simple"

        # Check complexity signals
        high_hits = sum(1 for w in cls.COMPLEXITY_SIGNALS["high"] if w in q)
        medium_hits = sum(1 for w in cls.COMPLEXITY_SIGNALS["medium"] if w in q)

        if high_hits >= 2 or (high_hits >= 1 and len(q) > 40):
            return "complex"
        if medium_hits >= 1 or high_hits >= 1 or len(q) > 30:
            return "medium"
        return "simple"

    @classmethod
    def get_strategy(cls, query: str) -> RetrievalStrategy:
        complexity = cls.classify(query)
        return {
            "simple": "keyword_only",
            "medium": "hybrid",
            "complex": "hybrid_hyde",
        }[complexity]


class QueryIntentClassifier:
    """Classifies query intent to optimize retrieval strategy."""

    INTENT_PATTERNS: Dict[QueryIntent, List[str]] = {
        "factual": ["是谁", "是什么", "在哪", "什么时候", "第几", "多少时间", "长度", "面积", "人口"],
        "procedural": ["如何", "怎么", "怎样", "步骤", "流程", "操作", "方法", "过程", "操作步骤"],
        "list": ["有哪些", "有什么", "列出", "哪些", "名单", "列表", "罗列"],
        "definition": ["什么是", "定义", "含义", "意思", "概念", "解释"],
        "comparison": ["区别", "不同", "比较", "差异", "对比", "哪一个好", "优缺点"],
        "causal": ["为什么", "原因", "结果", "导致", "因此", "所以", "由于"],
        "summary": ["总结", "概括", "摘要", "概述", "要点", "核心内容"],
    }

    @classmethod
    def classify(cls, query: str) -> QueryIntent:
        q = query.lower().strip()
        scores: Dict[str, float] = {intent: 0.0 for intent in cls.INTENT_PATTERNS}
        for intent, patterns in cls.INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in q:
                    scores[intent] += 1.0
        if max(scores.values()) == 0:
            return "other"
        return max(scores, key=scores.get)

    @classmethod
    def get_boost_fields(cls, intent: QueryIntent) -> List[str]:
        field_boosts = {
            "factual": ["chunk_text^2", "content^2", "title^1.5"],
            "procedural": ["chunk_text^2", "content^2", "section_title^1.8"],
            "list": ["chunk_text^1.5", "content^1.5", "list_content^2"],
            "definition": ["chunk_text^2", "content^2", "title^1.5", "definition^2.5"],
            "comparison": ["chunk_text^2", "content^2", "comparison^2", "pros_cons^2"],
            "causal": ["chunk_text^2", "content^2", "reason^2", "result^2"],
            "summary": ["chunk_text^1.5", "content^1.5", "summary^2", "conclusion^2"],
            "other": ["chunk_text^2", "content^2", "filename^1.2"],
        }
        return field_boosts.get(intent, ["chunk_text^2", "content^2", "filename^1.2"])


def extract_query_terms(query: str) -> List[str]:
    """Extract meaningful terms from query (bigrams for CJK, words for Latin)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    cjk_segments = re.findall(r"[一-鿿]+", q)
    raw_terms = []
    for seg in cjk_segments:
        for i in range(len(seg) - 1):
            gram = seg[i:i + 2]
            if gram not in raw_terms:
                raw_terms.append(gram)
    en_terms = re.findall(r"[a-z0-9]{2,}", q)
    for t in en_terms:
        if t not in raw_terms:
            raw_terms.append(t)
    stop_words = {
        "请", "帮我", "一下", "关于", "如何", "怎么", "什么", "是什么", "可以", "这个", "那个",
        "因为", "所以", "如果", "但是", "而且", "或者", "也是", "还是", "并且",
        "the", "and", "for", "with", "from", "that", "this", "is", "are", "was",
        "be", "have", "has", "had", "do", "does", "did", "will", "would", "could",
    }
    return [t for t in raw_terms if t.strip() and len(t) >= 2 and t not in stop_words][:12]


def rewrite_query_candidates(query: str) -> List[str]:
    """Lightweight local query rewrite (no LLM call)."""
    candidates: List[str] = []
    q = (query or "").strip()
    if not q:
        return candidates

    # Truncate long queries
    if len(q) > 200:
        q = q[:197] + "..."

    candidates.append(q)

    # No-punctuation version
    no_punct = re.sub(r"[，。！？；：、,.!?;:]", " ", q)
    no_punct = re.sub(r"\s+", " ", no_punct).strip()
    if no_punct and no_punct != q:
        candidates.append(no_punct)

    # Stop-word removed version
    stop_words = {"请", "帮我", "一下", "关于", "如何", "怎么", "是什么", "什么是", "给我", "是否"}
    tokens = [t for t in re.split(r"\s+", no_punct or q) if t]
    filtered = " ".join(t for t in tokens if t not in stop_words)
    if filtered and filtered not in candidates:
        candidates.append(filtered)

    dedup = []
    for c in candidates:
        c = c.strip()
        if c and c not in dedup:
            dedup.append(c)
    rewrite_count = max(1, int(getattr(settings, "RAG_QUERY_REWRITE_COUNT", 4) or 4))
    return dedup[:rewrite_count]


async def rewrite_query_llm(query: str, openai_client, model: str) -> List[str]:
    """Use LLM to generate query expansion variants."""
    if not openai_client or not getattr(settings, "RAG_ENABLE_QUERY_REWRITE", True):
        return [query]
    try:
        prompt = (
            "作为搜索专家，请为用户的原始问题生成 3 个相关的搜索查询变体（中文）。"
            "只返回 JSON 字符串数组，不要有任何解释。\n\n"
            f"原始问题: {query}\n\n输出示例: [\"查询1\", \"查询2\", \"查询3\"]"
        )
        resp = await openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=200,
        )
        content = resp.choices[0].message.content or ""
        match = re.search(r"\[.*\]", content, re.DOTALL)
        if match:
            rewritten = json.loads(match.group(0))
            if isinstance(rewritten, list):
                results = [query]
                for r in rewritten:
                    if r.strip() and r.strip() not in results:
                        results.append(r.strip())
                return results[:4]
        return [query]
    except Exception as e:
        logger.warning(f"LLM query rewrite failed: {e}")
        return [query]


async def generate_hyde_doc(query: str, intent: QueryIntent, openai_client, model: str) -> str:
    """Generate a hypothetical document for HyDE retrieval."""
    if not openai_client:
        return ""
    templates = {
        "factual": "请提供一个准确的事实性回答，包含具体的数值、日期或名称。50-100字。",
        "procedural": "请按步骤说明操作流程，使用数字序号。50-100字。",
        "list": "请列出相关要点，每个要点简洁明了。50-100字。",
        "definition": "请给出清晰的定义，包含关键特征和例子。50-100字。",
        "comparison": "请从多个维度对比分析，列出异同点。50-100字。",
        "causal": "请说明原因和可能的结果/影响。50-100字。",
        "summary": "请给出简明扼要的总结，包含核心要点。50-100字。",
        "other": "请提供一段事实性的简短回答。50-100字。",
    }
    template = templates.get(intent, templates["other"])
    try:
        prompt = (
            f"请为以下问题写一段理想的回答。\n要求：{template}\n"
            f"这段回答将用于向量检索，请尽可能使用专业术语和核心关键词。\n\n问题: {query}"
        )
        resp = await openai_client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300,
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"HyDE generation failed: {e}")
        return ""


async def generate_multi_hyde_docs(
    query: str, intent: QueryIntent, openai_client, model: str, num_docs: int = 2
) -> List[str]:
    """Generate multiple HyDE documents from different perspectives."""
    if not openai_client:
        return []
    perspectives_map = {
        "factual": ["官方定义视角", "数据统计视角", "实际案例视角"],
        "procedural": ["步骤概览视角", "细节说明视角", "注意事项视角"],
        "list": ["完整清单视角", "优先级视角"],
        "definition": ["概念定义视角", "特征描述视角"],
        "comparison": ["共同点视角", "差异点视角"],
        "causal": ["直接原因视角", "深层原因视角"],
        "summary": ["核心结论视角", "关键要点视角"],
        "other": ["概述视角", "细节视角"],
    }
    perspectives = perspectives_map.get(intent, perspectives_map["other"])[:num_docs]
    try:
        docs = []
        for perspective in perspectives:
            prompt = (
                f"从【{perspective}】的角度，为以下问题提供一个理想的回答。\n"
                f"要求：50-80字，事实性表述，用于向量检索。\n\n问题: {query}"
            )
            resp = await openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
                max_tokens=200,
            )
            content = resp.choices[0].message.content or ""
            if content:
                docs.append(content)
        return docs
    except Exception as e:
        logger.warning(f"Multi-HyDE generation failed: {e}")
        return []
