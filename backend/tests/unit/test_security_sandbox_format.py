"""S5 regression test — string.Formatter().get_field() 沙箱逃逸。

get_field 会在字符串内部对对象执行 getattr/getitem，因此
`string.Formatter().get_field("0.__globals__[__builtins__][__import__]", ...)`
可以在没有任何 AST 属性节点（下划线访问）的情况下读取 dunder，
从而绕过 _ast_safety_check。修复后 format/format_map/Formatter/get_field/vformat
一律禁用，字符串字面量中的 dunder 标记同样拒绝。
"""

import asyncio

from app.agent.tools.code_execution import _ast_safety_check, execute_python

# 已验证的逃逸片段：旧检查完全放行
BYPASS_SNIPPET = (
    'string.Formatter().get_field("0.__globals__[__builtins__][__import__]", (json.dumps,), {})'
)


def _run(code: str) -> str:
    # issue #206：execute_python 返回 ToolResult；str() 维持纯文本契约。
    return str(asyncio.run(execute_python(code=code)))


class TestFormatterEscape:
    """S5 — get_field / format 系列必须被 AST 检查拒绝。"""

    def test_exact_bypass_snippet_rejected(self):
        ok, reason = _ast_safety_check(BYPASS_SNIPPET)
        assert not ok, "该片段是已验证的沙箱逃逸路径，必须拒绝"
        assert "not allowed" in reason

    def test_execute_python_rejects_bypass(self):
        result = _run(BYPASS_SNIPPET)
        assert result.startswith("Error:")

    def test_format_family_calls_rejected(self):
        for snippet in (
            '"{}".format(1)',
            '"{}".format_map({})',
            "format(1, 'd')",
            "'{0}'.format(2)",
        ):
            ok, _ = _ast_safety_check(snippet)
            assert not ok, f"should block: {snippet!r}"

    def test_formatter_attribute_reference_rejected(self):
        # 即使只是取出属性（不立即调用），也必须拒绝
        ok, _ = _ast_safety_check("f = string.Formatter")
        assert not ok

    def test_dunder_tokens_in_string_literals_rejected(self):
        ok, _ = _ast_safety_check('x = "0.__globals__"')
        assert not ok

    def test_normal_string_ops_still_work(self):
        # 常规字符串方法（非 format 系列）不受影响
        assert _run("print('hello'.upper())") == "HELLO"
