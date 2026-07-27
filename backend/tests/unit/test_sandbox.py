"""Tests for AST-level Python sandbox and Docker sandbox dispatch."""

import asyncio

import pytest

from app.agent.tools.code_execution import execute_python


def run(code: str) -> str:
    return asyncio.run(execute_python(code=code))


class TestASTSandbox:
    """In-process AST sandbox — blocks dangerous operations."""

    # ── Safe code should execute ──────────────────────────────

    def test_safe_math(self):
        assert run("print(sum(range(10)))") == "45"

    def test_safe_string_ops(self):
        assert run("print('hello'.upper())") == "HELLO"

    def test_safe_no_output(self):
        assert "(Code executed successfully" in run("x = 42")

    # ── Blocked: imports ──────────────────────────────────────

    def test_block_import_os(self):
        r = run("import os\nprint('bad')")
        assert r.startswith("Error:")

    def test_block_import_from(self):
        r = run("from os import getcwd")
        assert r.startswith("Error:")

    def test_block_import_nested(self):
        r = run("def f():\n import sys\nf()")
        assert r.startswith("Error:")

    # ── Blocked: dangerous builtins ───────────────────────────

    @pytest.mark.parametrize("code", [
        'eval("1+1")',
        'exec("x=1")',
        'compile("x", "", "exec")',
        '__import__("os")',
        'open("/etc/passwd")',
        'globals()',
        'locals()',
        'getattr({}, "keys")',
        'setattr(type("X",(),{}), "x", 1)',
        'breakpoint()',
        'input()',
    ])
    def test_block_dangerous_builtins(self, code):
        r = run(code)
        assert r.startswith("Error:"), f"should block: {code!r} → {r!r}"

    # ── Blocked: attribute access (sandbox escape) ────────────

    def test_block_attribute_access(self):
        r = run('x = "".__class__')
        assert r.startswith("Error:")

    def test_block_class_attribute_chain(self):
        r = run("x = [].__class__.__bases__")
        assert r.startswith("Error:")

    # ── Syntax error ──────────────────────────────────────────

    def test_syntax_error(self):
        r = run("print(1 + ")
        assert "syntax error" in r.lower()

    # ── String concatenation bypass attempt ───────────────────

    def test_block_concat_bypass(self):
        r = run('exec("ev"+"al(\\"1+1\\")")')
        assert r.startswith("Error:")

    def test_block_getattr_escape(self):
        r = run('getattr(__builtins__, "open")')
        assert r.startswith("Error:")


class TestSandboxModeDispatch:
    """Sanity check: the function is importable and accepts code."""

    def test_function_imports(self):
        from app.agent.tools.code_execution import execute_python
        import inspect
        assert inspect.iscoroutinefunction(execute_python)

    def test_accepts_code_kwarg(self):
        r = run("print(42)")
        assert r == "42"
