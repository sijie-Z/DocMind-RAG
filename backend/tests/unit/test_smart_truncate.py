"""`_smart_truncate` 的契约测试（issue #203 ④）。

该函数生于 2026-05-10 且一直零测试覆盖；PER 重写（3535958）把它搬进新 loop.py
却丢掉了唯一调用点，本次接线（executor._synthesize_tool_results）把第一批测试补齐。

阈值语义：`MAX_RESULT_TOKENS * 2 = 6000` **字符**（常量名说 tokens、实为 chars，
历史怪癖，接线不改）。
"""

from app.agent.loop import _smart_truncate


def test_under_threshold_identity():
    # 恰好 6000（<=6000）原样返回。
    assert _smart_truncate("x" * 6000, "list_documents") == "x" * 6000
    assert _smart_truncate("", "list_documents") == ""
    # 6001 字符、默认分支 → 截为前 6000 + 标记。
    assert _smart_truncate("x" * 6001, "code_execution") == "x" * 6000 + "\n...[result truncated]"


def test_list_single_oversized_line_returned_as_is():
    """锁住继承怪癖（接线不引入、暂不修）：list 分支对单条超长行（无 \\n 可分行）原样返回。"""
    blob = "x" * 7000
    assert _smart_truncate(blob, "list_documents") == blob


def test_list_keeps_first_15_lines_and_marker():
    text = "\n".join(f"line-{i:02d}-" + "q" * 200 for i in range(30))  # ~6.3k 字符
    out = _smart_truncate(text, "list_documents")
    assert "line-00-" in out and "line-14-" in out
    assert "line-15-" not in out
    assert "... and 15 more items" in out


def test_search_keeps_first_3_sections_and_marker():
    text = "\n\n".join(f"section-{i}-" + "y" * 2100 for i in range(5))
    out = _smart_truncate(text, "search_knowledge_base")
    assert "section-0-" in out and "section-2-" in out
    assert "section-3-" not in out
    assert "... and 2 more results omitted" in out


def test_head_tail_for_conversation():
    text = "HEAD" * 2000 + "MID" + "TAIL" * 500  # 10003 字符
    out = _smart_truncate(text, "get_conversation_history")
    assert out.startswith("HEAD")
    assert out.endswith("TAIL")
    assert "[middle omitted]" in out


def test_default_branch_unknown_tool():
    out = _smart_truncate("z" * 7000, "code_execution")
    assert out == "z" * 6000 + "\n...[result truncated]"
