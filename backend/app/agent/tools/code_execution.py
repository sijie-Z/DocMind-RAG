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
    # 安全加固：移除 email 等敏感列，防止跨租户读取用户联系信息
    "users": {"id", "username", "display_name", "is_active",
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

# S4(a) 安全加固：SELECT 表达式中允许出现的函数白名单。
# 只有显式列出的函数可用，其他任何函数（如 CONCAT）一律拒绝。
_ALLOWED_SQL_FUNCTIONS = {
    "count", "sum", "avg", "min", "max", "coalesce", "nullif", "cast",
    "round", "abs", "year", "month", "day", "date", "strftime",
    "json_extract", "upper", "lower", "trim",
}

# 解析表达式时需要跳过的 SQL 关键字（别名/去重）
_SQL_KEYWORD_TOKENS = {"as", "distinct"}


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
    # 安全加固：union/with/into/information_schema 等可绕过列白名单或写出文件，一律禁止
    illegal = re.compile(
        r"\b(insert|update|delete|drop|alter|create|truncate|replace|grant|"
        r"revoke|attach|detach|pragma|exec|execute|call|load|import|export|"
        r"union|with|into|outfile|dumpfile|load_file|procedure|information_schema"
        r"|lock|sleep|benchmark)\b",
        flags=re.IGNORECASE,
    )
    if illegal.search(stripped):
        return False, "Dangerous SQL keyword detected"

    # 3b. 安全加固：禁止子查询（(SELECT ...) 可绕过列白名单读取任意列）
    if re.search(r"\(\s*select\b", stripped, flags=re.IGNORECASE):
        return False, "Subqueries are not allowed"

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

    # S4(c) 安全加固（fail-closed）：若查询只涉及没有 organization_id 列的表
    # （如 document_chunks、chat_messages），无法自动附加租户过滤条件。
    # 这里直接拒绝，而不是尝试按外键自动 JOIN 回所属表 —— 自动 JOIN 在复杂
    # 查询下容易改写错误、且可能引入跨组织数据；含 organization_id 的表与
    # 无该列的表 JOIN（如 documents JOIN document_chunks）仍然允许。
    filterable_tables = [
        t.lower() for t in tables
        if "organization_id" in ALLOWED_SQL_COLUMNS.get(t.lower(), set())
    ]
    if not filterable_tables:
        return False, "Query only touches tables without organization_id and cannot be org-scoped"

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

        # 去掉字符串字面量和别名（AS xxx，同时覆盖 CAST(x AS TYPE) 的类型名）
        expr = re.sub(r"'[^']*'", " ", col_clean)
        expr = re.sub(r'"[^"]*"', " ", expr)
        expr = re.sub(r"\bas\s+[a-zA-Z_][a-zA-Z0-9_]*", " ", expr, flags=re.IGNORECASE)

        # S4(a) 安全加固：函数名必须来自白名单，杜绝 CONCAT(hashed_password, id)
        # 这类"借道未授权函数读取非白名单列"的绕过
        for func_name in re.findall(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", expr):
            if func_name.lower() not in _ALLOWED_SQL_FUNCTIONS:
                return False, f"Function '{func_name}' is not in the allowed whitelist"

        # S4(a) 安全加固：表达式中"每一个"标识符都必须命中列白名单。
        # 旧实现只要求命中一个（any），因此 CONCAT(hashed_password, id)
        # 之类的表达式可以夹带非白名单列。
        tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", expr)
        tokens = [
            tok for tok in tokens
            if tok.lower() not in _ALLOWED_SQL_FUNCTIONS
            and tok.lower() not in _SQL_KEYWORD_TOKENS
        ]
        for tok in tokens:
            if tok.lower() not in known_columns:
                return False, f"Column '{col_clean}' is not in the allowed whitelist"

    return True, ""


def _ast_safety_check(code: str) -> tuple[bool, str]:
    """AST-level safety validation for the in-process Python sandbox.

    Returns (ok, reason). Extracted from execute_python so it can be unit-tested.
    """
    import ast as _ast

    # 安全加固：限制代码长度，防止超大输入
    if len(code) > 5000:
        return False, "Code too long (max 5000 chars)."

    _FORBIDDEN = (_ast.Import, _ast.ImportFrom)
    _FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open",
                        "globals", "locals", "vars", "getattr", "setattr",
                        "delattr", "breakpoint", "input", "format", "format_map"}
    # S5 安全加固：string.Formatter().get_field() / str.format / str.format_map /
    # Formatter.vformat 会在字符串内部执行 getattr/getitem，绕过基于 AST 的
    # 下划线属性检查，例如：
    #   string.Formatter().get_field("0.__globals__[__builtins__][__import__]", ...)
    # 因此这些属性整体禁用（含只取属性、不立即调用的形式）。
    _FORBIDDEN_ATTRS = {"format", "format_map", "get_field", "vformat", "Formatter"}
    # S5：字符串字面量中的 dunder 标记（如 __globals__ / __builtins__）一并拒绝
    _DUNDER_IN_STRING = re.compile(r"__[a-zA-Z0-9_]+")
    try:
        tree = _ast.parse(code)
    except SyntaxError as e:
        return False, f"Syntax error: {e}"
    for node in _ast.walk(tree):
        if isinstance(node, _FORBIDDEN):
            return False, "import statements are not allowed in sandbox."
        if isinstance(node, _ast.Call) and isinstance(node.func, _ast.Name):
            if node.func.id in _FORBIDDEN_CALLS:
                return False, f"calling '{node.func.id}()' is not allowed in sandbox."
        # 安全加固：禁止一切以下划线开头的属性访问。
        # 仅拦截双下划线（__）无法防住沙箱逃逸（如 random._os.system 可执行任意命令），
        # 因此单下划线私有属性一并禁止。
        if isinstance(node, _ast.Attribute) and node.attr.startswith("_"):
            return False, "private/dunder attribute access is not allowed in sandbox."
        # S5：format 系列可在字符串内部解析属性/下标，必须整体禁用
        if isinstance(node, _ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            return False, f"attribute '{node.attr}' is not allowed in sandbox."
        # S5：字符串字面量中出现 dunder 标记同样拒绝
        if isinstance(node, _ast.Constant) and isinstance(node.value, str):
            if _DUNDER_IN_STRING.search(node.value):
                return False, "dunder tokens in string literals are not allowed in sandbox."
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
    # HITL：宿主进程内执行任意代码 —— 声明高危，审批门（approval.py）据此判定。
    risk_level="high",
)
async def execute_python(code: str, **_: Any) -> str:
    # ── Resolve sandbox mode ──────────────────────────────────────
    from app.core.config import settings

    mode = getattr(settings, "SANDBOX_MODE", "ast")
    if mode == "auto":
        from app.agent.tools.docker_sandbox import _docker_available
        mode = "docker" if _docker_available() else "ast"

    if mode == "docker":
        try:
            from app.agent.tools.docker_sandbox import run_in_docker

            return await run_in_docker(
                code,
                image=getattr(settings, "SANDBOX_DOCKER_IMAGE", "python:3.11-slim"),
                memory_mb=getattr(settings, "SANDBOX_DOCKER_MEMORY_MB", 256),
                network=getattr(settings, "SANDBOX_DOCKER_NETWORK", "none"),
                timeout=getattr(settings, "SANDBOX_DOCKER_TIMEOUT_SECONDS", 30),
            )
        except Exception as e:
            logger.warning(f"Docker sandbox failed, falling back to AST: {e}")

    # ── AST-level safety check ────────────────────────────────────
    ok, reason = _ast_safety_check(code)
    if not ok:
        return f"Error: {reason}"

    # Build safe globals — no __builtins__ escape hatch
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
                exec(code, safe_globals, {})  # nosec B102 - 本工具的用途即沙箱执行，已做 AST 拦截

        # Run in a thread with timeout
        loop = asyncio.get_event_loop()
        await asyncio.wait_for(
            loop.run_in_executor(None, _run),
            timeout=10.0,
        )

        output = f_out.getvalue()
        errors = f_err.getvalue()

        # 安全加固：限制输出长度，防止内存/响应膨胀
        if len(output) > 10000:
            output = output[:10000] + "\n...[output truncated]"
        if len(errors) > 4000:
            errors = errors[:4000] + "\n...[error truncated]"

        if errors and not output:
            return f"Error:\n{errors.strip()}"
        if output:
            return output.strip()
        return "(Code executed successfully with no output)"

    except TimeoutError:
        return "Error: Code execution timed out (10 second limit)."
    except Exception as e:
        return f"Error: {type(e).__name__}: {e}"


def _inject_org_filter(query: str, org_id: int) -> str:
    """Append an organization_id filter to a whitelisted SELECT query.

    Extracted from execute_sql so it can be unit-tested. 只有当查询已经把
    organization_id 等值限定为调用者所在组织时才跳过注入。
    """
    needs_org_filter = False
    for table in ALLOWED_SQL_COLUMNS:
        if "organization_id" in ALLOWED_SQL_COLUMNS[table]:
            if re.search(rf"\b(?:from|join)\s+{re.escape(table)}\b", query, flags=re.IGNORECASE):
                needs_org_filter = True
                break
    if not needs_org_filter:
        return query
    # S4(b) 安全加固：旧实现只要查询里出现 organization_id 就跳过注入，
    # 于是自带 `organization_id = 3` 的查询可以完全躲开租户过滤；
    # 现在仅当查询已把 organization_id 等值限定为"调用者的组织"时才跳过，
    # 否则（限定的是其他组织、或仅提及该列）仍注入调用者组织条件，
    # 由 AND 收紧结果集。
    where_gate = re.search(r"\bwhere\b", query, flags=re.IGNORECASE)
    if where_gate:
        # 只在 WHERE 之后找等值条件：SELECT 列表里的 `organization_id = 3`
        # 是投影不是过滤，不能被当作已限定。
        tail_gate = re.search(
            r"\b(group\s+by|order\s+by|limit|having)\b", query[where_gate.end():], flags=re.IGNORECASE,
        )
        where_body = (
            query[where_gate.end(): where_gate.end() + tail_gate.start()]
            if tail_gate else query[where_gate.end():]
        )
        pinned = re.search(rf"\borganization_id\s*=\s*{org_id}\b", where_body, flags=re.IGNORECASE) or re.search(
            rf"\b{org_id}\s*=\s*organization_id\b", where_body, flags=re.IGNORECASE
        )
        # 即使等式存在，只要同一 WHERE 里出现 OR，条件就可能被放宽
        # （`organization_id = 3 OR 1=1`），此时不能跳过注入。
        if pinned and not re.search(r"\bor\b", where_body, flags=re.IGNORECASE):
            return query

    where_m = re.search(r"\bwhere\b", query, flags=re.IGNORECASE)
    tail_m = re.search(
        r"\b(group\s+by|order\s+by|limit|having)\b",
        query, flags=re.IGNORECASE,
    )
    if where_m:
        # 包裹原 WHERE 条件（防止 OR 破坏组织过滤语义）
        if tail_m and tail_m.start() > where_m.end():
            tail_pos = tail_m.start()
            return (
                query[:where_m.start()]
                + f" WHERE (organization_id = {org_id} AND "
                + query[where_m.end():tail_pos]
                + ") "
                + query[tail_pos:]
            )
        return (
            query[:where_m.start()]
            + f" WHERE (organization_id = {org_id} AND "
            + query[where_m.end():]
            + ")"
        )
    # 无 WHERE：在 GROUP BY/ORDER BY/LIMIT/HAVING 之前插入
    if tail_m:
        return (
            query[:tail_m.start()]
            + f" WHERE organization_id = {org_id} "
            + query[tail_m.start():]
        )
    return query.rstrip().rstrip(";") + f" WHERE organization_id = {org_id}"


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
    # HITL：对组织库执行任意（只读）SQL —— 数据面高危，审批门据此判定。
    risk_level="high",
)
async def execute_sql(query: str, organization_id: int | None = None, **_: Any) -> str:
    import json

    # Four-layer safety validation
    safe, reason = _is_safe_select_sql(query)
    if not safe:
        return f"Error: {reason}."

    # 安全加固：强制租户隔离。无法确定组织范围时拒绝执行（fail-closed），
    # 防止无组织用户/越权上下文查询到其他组织数据。
    if organization_id is None:
        return "Error: 无法确定组织范围，禁止执行跨组织查询。"
    org_id = int(organization_id)

    # 对含 organization_id 列的白名单表自动附加组织过滤条件
    query = _inject_org_filter(query, org_id)

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
