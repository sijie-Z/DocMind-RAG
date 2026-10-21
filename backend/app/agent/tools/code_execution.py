"""Code execution tools — sandboxed Python and read-only SQL.

SAFETY: These tools are tagged "code" and DISABLED BY DEFAULT.
Users must explicitly enable them in AgentConfig.
"""

import asyncio
import contextlib
import logging
import re
import sys
from typing import Any

from app.agent.registry import ToolMeta, ToolResult, register_tool

logger = logging.getLogger(__name__)

# Safe Python builtins whitelist
SAFE_BUILTINS = {
    "abs",
    "all",
    "any",
    "bool",
    "chr",
    "complex",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hash",
    "hex",
    "int",
    "isinstance",
    "issubclass",
    "len",
    "list",
    "map",
    "max",
    "min",
    "oct",
    "ord",
    "pow",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "zip",
    "True",
    "False",
    "None",
    "print",
}

SAFE_MODULES = [
    "math",
    "statistics",
    "collections",
    "itertools",
    "functools",
    "datetime",
    "json",
    "re",
    "string",
    "textwrap",
    "decimal",
    "fractions",
    "random",
]

# ── SQL safety: whitelist-based validation ──────────────────────

# Tables that the agent SQL tool is allowed to query (read-only business tables)
ALLOWED_SQL_TABLES: set[str] = {
    "documents",
    "document_chunks",
    "document_tags",
    "tags",
    "chat_sessions",
    "chat_messages",
    "knowledge_processing_jobs",
    "notifications",
    "organizations",
    "prompt_templates",
    "permissions",
    "roles",
    "users",
    "user_settings",
    "workflows",
    "workflow_executions",
    "node_definitions",
    "system_manuals",
    "user_login_sessions",
    "user_activity_logs",
}

# Column whitelist per table — only these columns can be SELECTed
ALLOWED_SQL_COLUMNS: dict[str, set[str]] = {
    "documents": {
        "id",
        "filename",
        "title",
        "status",
        "content_length",
        "chunk_count",
        "organization_id",
        "uploader_id",
        "created_at",
        "updated_at",
        "file_path",
        "file_type",
        "parsed_at",
        "parse_error",
        "is_public",
    },
    "document_chunks": {
        "id",
        "document_id",
        "chunk_index",
        "chunk_text",
        "chunk_length",
        "start_pos",
        "end_pos",
        "page_number",
        "section_title",
        "meta_data",
    },
    "document_tags": {"document_id", "tag_id"},
    "tags": {"id", "name", "color"},
    "chat_sessions": {
        "id",
        "user_id",
        "title",
        "status",
        "organization_id",
        "created_at",
        "updated_at",
        "settings",
    },
    "chat_messages": {
        "id",
        "session_id",
        "user_id",
        "role",
        "content",
        "message_type",
        "sources",
        "feedback_score",
        "is_cached",
        "created_at",
        "tokens_input",
        "tokens_output",
        "evaluation",
    },
    "knowledge_processing_jobs": {
        "id",
        "document_id",
        "status",
        "job_type",
        "progress",
        "error_message",
        "started_at",
        "completed_at",
    },
    "notifications": {"id", "user_id", "type", "title", "content", "is_read", "created_at"},
    "organizations": {
        "id",
        "name",
        "code",
        "is_active",
        "max_users",
        "max_storage_mb",
        "created_at",
    },
    "prompt_templates": {
        "id",
        "name",
        "content",
        "description",
        "is_active",
        "version",
        "updated_at",
        # A2：组织归属列（SQL 工具按 org 过滤的列白名单）
        "organization_id",
    },
    # 安全加固：移除 email 等敏感列，防止跨租户读取用户联系信息
    "users": {
        "id",
        "username",
        "display_name",
        "is_active",
        "role",
        "organization_id",
        "last_login",
        "created_at",
    },
    "user_settings": {"user_id", "theme", "language", "notification_prefs"},
    "system_manuals": {"id", "title", "content", "version", "is_active", "updated_at"},
    "workflows": {
        "id",
        "name",
        "description",
        "definition",
        "status",
        "created_by",
        "organization_id",
        "created_at",
        "updated_at",
    },
    "workflow_executions": {
        "id",
        "workflow_id",
        "status",
        "started_at",
        "completed_at",
        "result",
        "error_message",
    },
    "node_definitions": {"id", "workflow_id", "node_type", "config", "position_x", "position_y"},
}

# Columns allowed in any table (common identifiers)
_ALLOWED_COMMON = {"id", "created_at", "updated_at", "status", "name"}

# S4(a) 安全加固：SELECT 表达式中允许出现的函数白名单。
# 只有显式列出的函数可用，其他任何函数（如 CONCAT）一律拒绝。
_ALLOWED_SQL_FUNCTIONS = {
    "count",
    "sum",
    "avg",
    "min",
    "max",
    "coalesce",
    "nullif",
    "cast",
    "round",
    "abs",
    "year",
    "month",
    "day",
    "date",
    "strftime",
    "json_extract",
    "upper",
    "lower",
    "trim",
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
        t.lower() for t in tables if "organization_id" in ALLOWED_SQL_COLUMNS.get(t.lower(), set())
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
            tok
            for tok in tokens
            if tok.lower() not in _ALLOWED_SQL_FUNCTIONS and tok.lower() not in _SQL_KEYWORD_TOKENS
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
    _FORBIDDEN_CALLS = {
        "eval",
        "exec",
        "compile",
        "__import__",
        "open",
        "globals",
        "locals",
        "vars",
        "getattr",
        "setattr",
        "delattr",
        "breakpoint",
        "input",
        "format",
        "format_map",
    }
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


# ── AST sandbox: child-process executor (issue #206) ─────────────────
#
# 旧实现 ``await asyncio.wait_for(loop.run_in_executor(None, _run), timeout)``
# 只是放弃等待：超时后线程继续自旋（Python 无杀线程 API）、进程级
# ``redirect_stdout`` 的 StringIO 永久挂在 sys.stdout 上、``asyncio.run()`` 还会在
# ``shutdown_default_executor()`` 永久阻塞。改为子进程后超时可被真正杀死，
# stdout/stderr 是管道，不再需要（也不可能）做进程级重定向。
#
# 安全边界不变：父进程的 ``_ast_safety_check`` 仍是唯一的准入闸门；子进程与父
# 进程同解释器、同用户，只负责"可杀 + 输出隔离"，不是权限边界。

# AST 子进程执行的墙钟超时（秒）。模块级常量便于测试钳小；生产 10s 与旧契约一致。
_AST_EXEC_TIMEOUT_SECONDS = 10.0

# 子进程启动引导：经 ``-I -c`` 运行，用户代码经 stdin 传入（避免 argv 长度/转义
# 问题）。-I 会忽略 PYTHONIOENCODING 等环境变量，因此三路标准流显式 reconfigure
# 为 UTF-8（Windows 管道默认本地编码，中文输出会乱码或解码失败）。
_CHILD_BOOTSTRAP = """\
import builtins as _b
import sys as _sys

_sys.stdin.reconfigure(encoding="utf-8")
_sys.stdout.reconfigure(encoding="utf-8")
_sys.stderr.reconfigure(encoding="utf-8")

_code = _sys.stdin.read()

_names = {safe_builtins!r}
_mods = {safe_modules!r}

_g = {{"__builtins__": {{_n: getattr(_b, _n, None) for _n in _names}}}}
for _m in _mods:
    try:
        _g[_m] = __import__(_m)
    except ImportError:
        pass

try:
    exec(compile(_code, "<sandbox>", "exec"), _g, {{}})
except BaseException as _e:
    print(f"{{type(_e).__name__}}: {{_e}}", file=_sys.stderr, flush=True)
    _sys.exit(1)
"""


def _build_child_wrapper() -> str:
    """Render the child bootstrap with the safe-builtins/module whitelists."""
    return _CHILD_BOOTSTRAP.format(
        safe_builtins=sorted(SAFE_BUILTINS),
        safe_modules=list(SAFE_MODULES),
    )


async def _run_ast_subprocess(
    code: str,
    *,
    timeout: float = _AST_EXEC_TIMEOUT_SECONDS,
) -> str:
    """Execute AST-validated code in a child interpreter; return captured output.

    ``sys.executable -I -c <bootstrap>`` with the user code on stdin. On
    timeout the child is killed and reaped (``proc.kill(); await proc.wait()``),
    so no execution keeps burning CPU after the call returns.

    Raises:
        TimeoutError: child exceeded ``timeout`` seconds and was killed.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            _build_child_wrapper(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except NotImplementedError as e:
        # 评审 M3：Windows + SelectorEventLoop（uvicorn >= 0.36 在 --reload /
        # workers>1 时即选它）不支持 create_subprocess_exec。给出可操作提示，
        # 而不是让 NotImplementedError 以 "unknown" 失败或裸冒泡。
        from app.agent.tools.docker_sandbox import SandboxUnavailableError

        raise SandboxUnavailableError(
            "当前事件循环不支持子进程执行（Windows 下 uvicorn --reload / workers>1 "
            "会使用 SelectorEventLoop）。请以单 worker 方式运行后端（ProactorEventLoop），"
            "或在 Linux/WSL 上运行。"
        ) from e
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(code.encode("utf-8")),
            timeout=timeout,
        )
    except BaseException:
        # 超时、任务取消（客户端断连 / 服务端关停）或 spawn 后的任意异常：
        # 子进程必须一并 kill + reap。否则死循环子进程会在后台永久燃烧 CPU，
        # 与超时未杀死线程是同一症状（评审 M1）。kill 是同步调用；取消路径上
        # 外层 task 处于 cancelling 状态，await 会立刻再次抛出，故用 shield 把
        # reap 放后台尽力完成，再把原异常原样抛出。
        if proc.returncode is None:
            # kill 与"进程恰好退出"存在竞态：Handle 已关闭时 Windows 可能抛
            # ProcessLookupError。此时进程已死，抑制即可，不能替换原始异常
            # （评审复核 minor ①）。
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            with contextlib.suppress(BaseException):
                await asyncio.shield(proc.wait())
        raise

    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0:
        return f"Error: {err or f'exit code {proc.returncode}'}"
    # 与旧契约一致：无 stdout 但有 stderr 时按错误返回（真实异常已由引导脚本
    # 以非零退出码覆盖，这里多覆盖解释器级 warning 等场景）。
    if err and not out:
        return f"Error:\n{err}"
    return out


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
async def execute_python(code: str, **_: Any) -> str | ToolResult:
    from app.agent.tools.docker_sandbox import (
        DockerSandboxError,
        DockerSandboxTimeout,
        DockerSandboxUnavailable,
        SandboxUnavailableError,
        resolve_sandbox_mode,
        run_in_docker,
    )
    from app.core.config import settings

    # ── Resolve sandbox mode (issue #206: fail-closed) ────────────
    # 只有显式 SANDBOX_MODE=ast 允许进程内（子进程）执行；auto/docker 在 Docker
    # 不可用时返回结构化失败，绝不静默降级到更弱的沙箱。
    try:
        mode = resolve_sandbox_mode()
    except SandboxUnavailableError as e:
        logger.error("execute_python refused (sandbox unavailable): %s", e)
        return ToolResult.fail(code="sandbox_unavailable", message=str(e))

    if mode == "docker":
        try:
            output = await run_in_docker(
                code,
                image=getattr(settings, "SANDBOX_DOCKER_IMAGE", "python:3.11-slim"),
                memory_mb=getattr(settings, "SANDBOX_DOCKER_MEMORY_MB", 256),
                network=getattr(settings, "SANDBOX_DOCKER_NETWORK", "none"),
                timeout=getattr(settings, "SANDBOX_DOCKER_TIMEOUT_SECONDS", 30),
                # 结构化失败（评审 M2）：错误串会被 registry 包成 success=True，
                # 因此 Docker 运行时失败也以 ToolResult.fail 呈现（不落进程内、
                # 也不伪装成功）。默认字符串契约保留给 workflow 节点。
                raise_on_error=True,
            )
        except DockerSandboxTimeout as e:
            return ToolResult.fail(
                code="timeout",
                message=str(e),
                meta=ToolMeta(sandbox="docker"),
            )
        except DockerSandboxUnavailable as e:
            return ToolResult.fail(
                code="sandbox_unavailable",
                message=str(e),
                meta=ToolMeta(sandbox="docker"),
            )
        except DockerSandboxError as e:
            return ToolResult.fail(
                code=e.code,
                message=str(e),
                meta=ToolMeta(sandbox="docker"),
            )
        return ToolResult.ok(data=output, meta=ToolMeta(sandbox="docker"))

    # ── Explicit "ast": parent-side AST gate, child-process execution ─
    ok, reason = _ast_safety_check(code)
    if not ok:
        # 保持旧契约：AST 拒绝与子进程内的运行时错误一样，都是内容型错误串
        # （#293 明确 execute_python 的 stdout 是任意文本、分类权留在工具内部；
        # 改结构化失败会改变既有反思/重试语义，不在 #206 范围内）。
        return f"Error: {reason}"

    try:
        output = await _run_ast_subprocess(code, timeout=_AST_EXEC_TIMEOUT_SECONDS)
    except TimeoutError:
        return ToolResult.fail(
            code="timeout",
            message=f"Code execution timed out ({_AST_EXEC_TIMEOUT_SECONDS:g} second limit).",
            meta=ToolMeta(sandbox="ast"),
        )
    except SandboxUnavailableError as e:
        return ToolResult.fail(
            code="sandbox_unavailable",
            message=str(e),
            meta=ToolMeta(sandbox="ast"),
        )
    except Exception as e:
        return ToolResult.fail(
            code="unknown",
            message=f"{type(e).__name__}: {e}",
            meta=ToolMeta(sandbox="ast"),
        )

    # 安全加固：限制输出长度，防止内存/响应膨胀
    if len(output) > 10000:
        output = output[:10000] + "\n...[output truncated]"
    if not output:
        return ToolResult.ok(
            data="(Code executed successfully with no output)",
            meta=ToolMeta(sandbox="ast"),
        )
    return ToolResult.ok(data=output, meta=ToolMeta(sandbox="ast"))


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
            r"\b(group\s+by|order\s+by|limit|having)\b",
            query[where_gate.end() :],
            flags=re.IGNORECASE,
        )
        where_body = (
            query[where_gate.end() : where_gate.end() + tail_gate.start()]
            if tail_gate
            else query[where_gate.end() :]
        )
        pinned = re.search(
            rf"\borganization_id\s*=\s*{org_id}\b", where_body, flags=re.IGNORECASE
        ) or re.search(rf"\b{org_id}\s*=\s*organization_id\b", where_body, flags=re.IGNORECASE)
        # 即使等式存在，只要同一 WHERE 里出现 OR，条件就可能被放宽
        # （`organization_id = 3 OR 1=1`），此时不能跳过注入。
        if pinned and not re.search(r"\bor\b", where_body, flags=re.IGNORECASE):
            return query

    where_m = re.search(r"\bwhere\b", query, flags=re.IGNORECASE)
    tail_m = re.search(
        r"\b(group\s+by|order\s+by|limit|having)\b",
        query,
        flags=re.IGNORECASE,
    )
    if where_m:
        # 包裹原 WHERE 条件（防止 OR 破坏组织过滤语义）
        if tail_m and tail_m.start() > where_m.end():
            tail_pos = tail_m.start()
            return (
                query[: where_m.start()]
                + f" WHERE (organization_id = {org_id} AND "
                + query[where_m.end() : tail_pos]
                + ") "
                + query[tail_pos:]
            )
        return (
            query[: where_m.start()]
            + f" WHERE (organization_id = {org_id} AND "
            + query[where_m.end() :]
            + ")"
        )
    # 无 WHERE：在 GROUP BY/ORDER BY/LIMIT/HAVING 之前插入
    if tail_m:
        return (
            query[: tail_m.start()]
            + f" WHERE organization_id = {org_id} "
            + query[tail_m.start() :]
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
                data.append(
                    dict(
                        zip(columns, [str(v) if v is not None else None for v in row], strict=False)
                    )
                )

            return json.dumps(
                {
                    "columns": columns,
                    "rows": len(data),
                    "data": data,
                },
                ensure_ascii=False,
                indent=2,
            )
    except TimeoutError:
        return "Error: SQL query timed out (5 second limit)."
    except Exception as e:
        return f"SQL Error: {type(e).__name__}: {e}"
