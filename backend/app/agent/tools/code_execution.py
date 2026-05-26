"""Code execution tools — sandboxed Python and read-only SQL.

SAFETY: These tools are tagged "code" and DISABLED BY DEFAULT.
Users must explicitly enable them in AgentConfig.
"""

import contextlib
import logging
from typing import Any

from app.agent.registry import register_tool

logger = logging.getLogger(__name__)

# Safe Python builtins whitelist
SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "chr", "complex", "dict", "divmod",
    "enumerate", "filter", "float", "format", "frozenset", "hash",
    "hex", "int", "isinstance", "issubclass", "len", "list", "map",
    "max", "min", "oct", "ord", "pow", "range", "repr", "reversed",
    "round", "set", "slice", "sorted", "str", "sum", "tuple", "type",
    "zip", "True", "False", "None", "print",
}

SAFE_MODULES = [
    "math", "statistics", "collections", "itertools", "functools",
    "datetime", "json", "re", "string", "textwrap", "decimal",
    "fractions", "random",
]


@register_tool(
    name="execute_python",
    description=(
        "Execute Python code in a sandboxed environment. "
        "Supports math, statistics, collections, datetime, json, re, etc. "
        "10 second timeout. No filesystem or network access. "
        "Returns stdout output. Use for calculations, data processing, and algorithms."
    ),
    parameters={
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute. Use print() to output results.",
            },
        },
        "required": ["code"],
    },
    tags=["code", "compute"],
    requires_auth=True,
)
async def execute_python(code: str, **_: Any) -> str:
    # Check for dangerous patterns
    dangerous = [
        "import os", "import subprocess", "import sys", "import shutil",
        "import socket", "import requests", "import urllib",
        "open(", "exec(", "eval(", "compile(", "__import__",
        "globals()", "locals()", "getattr(", "setattr(",
        ".system(", ".popen(", ".spawn(", ".fork(",
        "rmdir", "unlink", "remove(", "rmtree",
    ]
    for pattern in dangerous:
        if pattern in code:
            return f"Error: Dangerous code pattern detected: '{pattern}'. This is not allowed."

    # Build safe globals
    safe_globals = {"__builtins__": {}}
    for name in SAFE_BUILTINS:
        safe_globals["__builtins__"][name] = __builtins__.get(name) if isinstance(__builtins__, dict) else getattr(__builtins__, name, None)
    # Always include print
    safe_globals["__builtins__"]["print"] = print

    for mod_name in SAFE_MODULES:
        with contextlib.suppress(ImportError):
            safe_globals[mod_name] = __import__(mod_name)

    try:
        import asyncio
        import io
        from contextlib import redirect_stderr, redirect_stdout

        f_out = io.StringIO()
        f_err = io.StringIO()

        def _run():
            with redirect_stdout(f_out), redirect_stderr(f_err):
                exec(code, safe_globals, {})

        # Run in a thread with timeout
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, _run),
            timeout=10.0,
        )

        output = f_out.getvalue()
        errors = f_err.getvalue()

        if errors and not output:
            return f"Error:\n{errors.strip()}"
        if output:
            return output.strip()
        return "(Code executed successfully with no output)"

    except TimeoutError:
        return "Error: Code execution timed out (10 second limit)."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


@register_tool(
    name="execute_sql",
    description=(
        "Execute a read-only SQL query against the organization's database. "
        "Returns results as JSON. Max 100 rows. 5 second timeout. "
        "Only SELECT statements are allowed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "SQL SELECT query to execute",
            },
        },
        "required": ["query"],
    },
    tags=["code", "data"],
    requires_auth=True,
)
async def execute_sql(query: str, **_: Any) -> str:
    import json

    query_stripped = query.strip().lower()
    if not query_stripped.startswith("select"):
        return "Error: Only SELECT queries are allowed."
    if ";" in query.rstrip(";"):
        return "Error: Multiple statements are not allowed."
    # Force limit
    if "limit" not in query_stripped:
        query = query.rstrip().rstrip(";") + " LIMIT 100"

    try:
        import asyncio

        from sqlalchemy import text

        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            result = await asyncio.wait_for(
                session.execute(text(query)),
                timeout=5.0,
            )
            rows = result.fetchall()
            columns = list(result.keys())

            data = []
            for row in rows[:100]:
                data.append(dict(zip(columns, [str(v) if v is not None else None for v in row], strict=False)))

            return json.dumps({
                "columns": columns,
                "rows": len(data),
                "data": data,
            }, ensure_ascii=False, indent=2)
    except TimeoutError:
        return "Error: SQL query timed out (5 second limit)."
    except Exception as e:
        return f"SQL Error: {type(e).__name__}: {e}"
