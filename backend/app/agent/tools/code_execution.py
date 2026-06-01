"""Code execution tools — sandboxed Python and read-only SQL.

SAFETY: These tools are tagged "code" and DISABLED BY DEFAULT.
Users must explicitly enable them in AgentConfig.
"""

import contextlib
import logging
import re
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

# ── SQL safety: whitelist-based validation ──────────────────────

# Tables that the agent SQL tool is allowed to query (read-only business tables)
ALLOWED_SQL_TABLES: set[str] = {
    "documents", "document_chunks", "document_tags", "tags",
    "chat_sessions", "chat_messages",
    "knowledge_processing_jobs",
    "notifications",
    "organizations",
    "prompt_templates",
    "permissions", "roles",
    "users", "user_settings",
    "workflows", "workflow_executions", "node_definitions",
    "system_manuals",
    "user_login_sessions", "user_activity_logs",
}

# Column whitelist per table — only these columns can be SELECTed
ALLOWED_SQL_COLUMNS: dict[str, set[str]] = {
    "documents": {"id", "filename", "title", "status", "content_length",
                   "chunk_count", "organization_id", "uploader_id",
                   "created_at", "updated_at", "file_path", "file_type",
                   "parsed_at", "parse_error", "is_public"},
    "document_chunks": {"id", "document_id", "chunk_index", "chunk_text",
                         "chunk_length", "start_pos", "end_pos",
                         "page_number", "section_title", "meta_data"},
    "document_tags": {"document_id", "tag_id"},
    "tags": {"id", "name", "color"},
    "chat_sessions": {"id", "user_id", "title", "status", "organization_id",
                       "created_at", "updated_at", "settings"},
    "chat_messages": {"id", "session_id", "user_id", "role", "content",
                       "message_type", "sources", "feedback_score",
                       "is_cached", "created_at",
                       "tokens_input", "tokens_output",
                       "evaluation"},
    "knowledge_processing_jobs": {"id", "document_id", "status", "job_type",
                                   "progress", "error_message",
                                   "started_at", "completed_at"},
    "notifications": {"id", "user_id", "type", "title", "content",
                       "is_read", "created_at"},
    "organizations": {"id", "name", "code", "is_active", "max_users",
                       "max_storage_mb", "created_at"},
    "prompt_templates": {"id", "name", "content", "description",
                          "is_active", "version", "updated_at"},
    "users": {"id", "username", "email", "display_name", "is_active",
               "role", "organization_id", "last_login", "created_at"},
    "user_settings": {"user_id", "theme", "language", "notification_prefs"},
    "system_manuals": {"id", "title", "content", "version", "is_active",
                        "updated_at"},
    "workflows": {"id", "name", "description", "definition", "status",
                   "created_by", "organization_id", "created_at", "updated_at"},
    "workflow_executions": {"id", "workflow_id", "status", "started_at",
                             "completed_at", "result", "error_message"},
    "node_definitions": {"id", "workflow_id", "node_type", "config",
                          "position_x", "position_y"},
}

# Columns allowed in any table (common identifiers)
_ALLOWED_COMMON = {"id", "created_at", "updated_at", "status", "name"}


def _is_safe_select_sql(sql: str) -> tuple[bool, str]:
    """Validate SQL is a restricted SELECT over whitelist tables/columns.

    Returns (True, "") or (False, "reason").
    """
    if not sql or not isinstance(sql, str):
        return False, "Empty SQL query"

    stripped = sql.strip()

    # 1. Must be SELECT
    if not re.match(r"^select\b", stripped, flags=re.IGNORECASE):
        return False, "Only SELECT queries are allowed"

    # 2. No dangerous keywords (INSERT, UPDATE, DELETE, DROP, etc.)
    illegal = re.compile(
        r"\b(insert|update|delete|drop|alter|create|truncate|replace|grant|"
        r"revoke|attach|detach|pragma|exec|execute|call|load|import|export)\b",
        flags=re.IGNORECASE,
    )
    if illegal.search(stripped):
        return False, "Dangerous SQL keyword detected"

    # 3. No multi-statement or comments
    if ";" in stripped.rstrip(";"):
        return False, "Multiple statements are not allowed"
    if "--" in stripped or "/*" in stripped or "*/" in stripped:
        return False, "SQL comments are not allowed"

    # 4. Extract table names and validate against whitelist
    tables = re.findall(
        r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        stripped,
        flags=re.IGNORECASE,
    )
    if not tables:
        return False, "No table found in query"

    for t in tables:
        if t.lower() not in ALLOWED_SQL_TABLES:
            return False, f"Table '{t}' is not in the allowed whitelist"

    # 5. Extract selected columns and validate
    select_match = re.search(
        r"^select\s+(.*?)\s+from\b",
        stripped,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not select_match:
        return False, "Cannot parse SELECT columns"

    raw_columns = [seg.strip() for seg in select_match.group(1).split(",")]

    # Build known columns from matched tables
    matched_tables = [t.lower() for t in tables if t.lower() in ALLOWED_SQL_COLUMNS]
    known_columns: set[str] = set(_ALLOWED_COMMON)
    for t in matched_tables:
        known_columns.update(ALLOWED_SQL_COLUMNS.get(t, set()))

    for col in raw_columns:
        col_clean = col.strip()
        if col_clean == "*":
            return False, "SELECT * is not allowed — specify columns explicitly"

        # Handle aggregate functions: extract inner column reference
        agg_match = re.match(
            r"(count|sum|avg|min|max|coalesce|nullif|cast|round|abs|year|month|day|strftime|json_extract)\s*\((.*?)\)",
            col_clean,
            flags=re.IGNORECASE,
        )
        if agg_match:
            inner = agg_match.group(2).strip()
            if inner == "*" or inner == "1" or not inner:
                # COUNT(*), COUNT(1), or empty — safe, no column reference
                continue
            # Check the inner column reference
            inner_clean = re.sub(r"\s+as\s+.*", "", inner, flags=re.IGNORECASE).strip()
            for part in inner_clean.split(","):
                part = part.strip()
                if not part:
                    continue
                tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", part)
                tokens = [
                    tok for tok in tokens
                    if tok.lower()
                    not in {"as", "distinct", "upper", "lower", "trim", "round"}
                ]
                if tokens and not any(tok.lower() in known_columns for tok in tokens):
                    return False, f"Column '{col_clean}' is not in the allowed whitelist"
            continue

        # Extract identifier tokens (strip SQL functions/aliases)
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", col_clean)
        tokens = [
            tok for tok in tokens
            if tok.lower()
            not in {"as", "sum", "avg", "min", "max", "count", "distinct",
                    "coalesce", "nullif", "cast", "upper", "lower", "trim",
                    "round", "abs", "year", "month", "day", "date", "strftime",
                    "json_extract", "json"}
        ]
        if not tokens:
            continue
        # At least one token must be a known column
        if not any(tok.lower() in known_columns for tok in tokens):
            return False, f"Column '{col_clean}' is not in the allowed whitelist"

    return True, ""


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

    # Four-layer safety validation
    safe, reason = _is_safe_select_sql(query)
    if not safe:
        return f"Error: {reason}."

    # Force limit
    query_stripped = query.strip().lower()
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
