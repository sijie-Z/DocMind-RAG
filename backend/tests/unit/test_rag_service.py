import time

from app.rag.context_compressor import compress, compress_context_list
from app.rag.query_processor import (
    QueryComplexityClassifier,
    QueryIntentClassifier,
    rewrite_query_candidates,
)
from app.rag.retriever import HybridRetriever


def test_rrf_fuse_keeps_rewrite_and_modality_signals():
    keyword_hits = [
        {
            "_id": "doc-1",
            "_score": 9.2,
            "_rewrite_hits": 3,
            "_source": {"content": "报销流程审批", "filename": "policy.md", "document_id": "d1"},
        }
    ]
    vector_hits = [
        {
            "_id": "doc-1",
            "_score": 1.7,
            "_source": {"content": "报销流程审批", "filename": "policy.md", "document_id": "d1"},
        },
        {
            "_id": "doc-2",
            "_score": 1.6,
            "_source": {"content": "预算管理", "filename": "budget.md", "document_id": "d2"},
        },
    ]

    fused = HybridRetriever._rrf_fuse(keyword_hits, vector_hits)
    by_id = {item["_id"]: item for item in fused}

    assert by_id["doc-1"]["has_keyword"] is True
    assert by_id["doc-1"]["has_vector"] is True
    assert by_id["doc-1"]["rewrite_hits"] == 3


def test_post_process_prioritizes_fresh_multimodal_hits():
    now = time.time()
    fused_hits = [
        {
            "_id": "doc-1",
            "_score": 1.0,
            "has_keyword": True,
            "has_vector": True,
            "rewrite_hits": 3,
            "_source": {
                "content": "报销流程需要提交发票并经过审批",
                "filename": "finance_policy.md",
                "document_id": "d1",
                "upload_time": now,
            },
        },
        {
            "_id": "doc-2",
            "_score": 1.0,
            "has_keyword": False,
            "has_vector": True,
            "rewrite_hits": 1,
            "_source": {
                "content": "流程说明：该文档主要描述系统背景，不涉及报销细节",
                "filename": "misc.md",
                "document_id": "d2",
                "upload_time": now - 7 * 24 * 3600,
            },
        },
    ]

    retriever = HybridRetriever()
    docs = retriever._post_process("报销 流程 审批", fused_hits, top_k=2)
    assert len(docs) == 2
    assert docs[0]["document_id"] == "d1"
    assert docs[0]["score"] > docs[1]["score"]


def test_query_intent_classifier():
    test_cases = [
        ("如何报销差旅费？", "procedural"),
        ("报销流程是什么？", "procedural"),
        ("公司的报销政策有哪些？", "list"),
        ("发票丢失怎么处理？", "procedural"),
        ("什么是弹性工作制？", "definition"),
        ("报销和预支有什么区别？", "comparison"),
        ("为什么我的报销被拒绝了？", "causal"),
        ("请总结一下休假制度", "summary"),
        ("张三是谁", "factual"),
        ("会议室在哪里", "factual"),
    ]

    for query, expected_intent in test_cases:
        result = QueryIntentClassifier.classify(query)
        print(f"Query: {query} -> Intent: {result} (expected: {expected_intent})")


def test_rewrite_query_candidates():
    query = "如何报销差旅费？"
    candidates = rewrite_query_candidates(query)

    print(f"Query: {query}")
    print(f"Candidates: {candidates}")

    assert len(candidates) > 0, "Should have at least one candidate"
    assert query in candidates, "Original query should be in candidates"
    print(f"Generated {len(candidates)} query candidates")


def test_context_compressor():
    long_text = """
    报销流程说明：第一，员工需要在系统中提交报销申请，包括选择费用类型、填写金额、上传发票照片。
    第二，直属主管进行审批，审批通过后进入财务复核环节。第三，财务人员核对发票真实性后进行付款处理。
    第四，员工可以在个人中心查看报销进度。关于发票要求，必须是正规增值税发票，发票内容要与实际消费一致。
    特殊情况如发票丢失需要提供替代证明材料。请假制度说明：员工每年享有带薪年假十天。
    """.strip()

    query = "报销流程"
    compressed = compress(long_text, query, max_chars=200)

    print(f"Original length: {len(long_text)}")
    print(f"Compressed length: {len(compressed)}")
    print(f"Compressed text: {compressed}")

    assert len(compressed) <= 220
    assert "报销" in compressed or "流程" in compressed


def test_context_compressor_list():
    contexts = [
        {"text": "报销需要提供发票，发票内容要与实际消费一致，发票日期必须在30天内。", "filename": "policy1.md"},
        {"text": "请假流程：员工在系统中提交请假申请，主管审批后生效，年假最短单位为半天。", "filename": "policy2.md"},
        {"text": "差旅费标准：国内出差每天补助150元，住宿费上限300元，交通费实报实销。", "filename": "policy3.md"},
    ]

    query = "报销 发货"
    compressed = compress_context_list(contexts, query, max_context_chars=200)

    print(f"Input contexts: {len(contexts)}")
    print(f"Compressed contexts: {len(compressed)}")

    for ctx in compressed:
        print(f"  - {ctx['filename']}: {len(ctx['text'])} chars (compressed: {ctx.get('is_compressed', False)})")


def test_complexity_classifier_simple():
    """Short factual queries → simple → keyword_only."""
    assert QueryComplexityClassifier.classify("报销流程") == "simple"
    assert QueryComplexityClassifier.classify("张三是谁") == "simple"
    assert QueryComplexityClassifier.get_strategy("报销流程") == "keyword_only"


def test_complexity_classifier_medium():
    """How-to / list queries → medium → hybrid."""
    assert QueryComplexityClassifier.classify("如何提交报销申请？") == "medium"
    assert QueryComplexityClassifier.classify("公司有哪些福利政策？") == "medium"
    assert QueryComplexityClassifier.get_strategy("如何提交报销申请？") == "hybrid"


def test_complexity_classifier_complex():
    """Multi-signal analytical queries → complex → hybrid_hyde."""
    q1 = "请对比分析公司不同部门的报销流程差异和优缺点"
    assert QueryComplexityClassifier.classify(q1) == "complex"
    assert QueryComplexityClassifier.get_strategy(q1) == "hybrid_hyde"

    q2 = "为什么上季度的报销被拒绝率上升了？请分析原因并给出建议"
    assert QueryComplexityClassifier.classify(q2) == "complex"


def test_complexity_classifier_empty():
    assert QueryComplexityClassifier.classify("") == "simple"
    assert QueryComplexityClassifier.classify("   ") == "simple"
