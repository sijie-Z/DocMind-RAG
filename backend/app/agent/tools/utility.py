"""Utility tools — math, format conversion, system status, weather."""

import json
import logging
from typing import Any

from app.agent.registry import ToolResult, extended_surface_enabled, register_tool

logger = logging.getLogger(__name__)


@register_tool(
    name="calculate",
    description=(
        "Safely evaluate a mathematical expression. Supports: +, -, *, /, "
        "** (power), % (modulo), sqrt, sin, cos, tan, log, log2, log10, "
        "abs, ceil, floor, pi, e, and parentheses. "
        "Use this for calculations that need precise results."
    ),
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Mathematical expression to evaluate, e.g. 'sqrt(144) + 2**10'",
            },
        },
        "required": ["expression"],
    },
    tags=["utility", "compute"],
    # issue #509：能力已合并进 utility(operation=calculate)；保留注册与 handler
    # （不删工具、旧名可用），但不再进 schema；旧名经 TOOL_ALIASES 路由。
    hidden=True,
    superseded_by="utility",
)
async def calculate(expression: str, **_: Any) -> str:
    import ast
    import math
    import operator

    # Safe math functions and constants
    safe_dict = {
        "sqrt": math.sqrt,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "asin": math.asin,
        "acos": math.acos,
        "atan": math.atan,
        "atan2": math.atan2,
        "log": math.log,
        "log2": math.log2,
        "log10": math.log10,
        "exp": math.exp,
        "abs": abs,
        "ceil": math.ceil,
        "floor": math.floor,
        "factorial": math.factorial,
        "gcd": math.gcd,
        "pi": math.pi,
        "e": math.e,
        "tau": math.tau,
        "degrees": math.degrees,
        "radians": math.radians,
        "sinh": math.sinh,
        "cosh": math.cosh,
        "tanh": math.tanh,
    }

    bin_ops = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
        ast.USub: operator.neg,
    }

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.BinOp):
            left = _eval(node.left)
            right = _eval(node.right)
            op_type = type(node.op)
            if op_type not in bin_ops:
                raise ValueError(f"Unsupported operator: {op_type.__name__}")
            return bin_ops[op_type](left, right)
        if isinstance(node, ast.UnaryOp):
            operand = _eval(node.operand)
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("Unsupported unary operator")
        if isinstance(node, ast.Call):
            func_name = node.func.id if isinstance(node.func, ast.Name) else None
            if not func_name or func_name not in safe_dict:
                raise ValueError(f"Unsupported function: {func_name}")
            args = [_eval(a) for a in node.args]
            return safe_dict[func_name](*args)
        if isinstance(node, ast.Name):
            if node.id in safe_dict:
                return safe_dict[node.id]
            raise ValueError(f"Unknown variable: {node.id}")
        raise ValueError(f"Unsupported expression type: {type(node).__name__}")

    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval(tree)
        if isinstance(result, float):
            if abs(result) < 1e-10:
                result = 0.0
            result = round(result, 10)
        return str(result)
    except SyntaxError as e:
        return f"Syntax error: {e}"
    except ZeroDivisionError:
        return "Error: Division by zero"
    except Exception as e:
        return f"Calculation error: {type(e).__name__}: {e}"


@register_tool(
    name="format_converter",
    description=(
        "Convert between common data formats: JSON, YAML, CSV, and Markdown tables. "
        "Input format is auto-detected. Output format must be specified. "
        "Useful for data transformation and formatting tasks."
    ),
    parameters={
        "type": "object",
        "properties": {
            "data": {
                "type": "string",
                "description": "The data string to convert",
            },
            "output_format": {
                "type": "string",
                "description": "Target format: json, yaml, csv, markdown_table",
                "default": "json",
            },
            "input_format": {
                "type": "string",
                "description": "Input format or 'auto' for auto-detection",
                "default": "auto",
            },
        },
        "required": ["data", "output_format"],
    },
    tags=["utility", "format"],
    # issue #509：能力已合并进 utility(operation=format_converter)；保留注册与
    # handler（不删工具、旧名可用），但不再进 schema。
    hidden=True,
    superseded_by="utility",
)
async def format_converter(
    data: str,
    output_format: str = "json",
    input_format: str = "auto",
    **_: Any,
) -> str:
    output_fmt = output_format.lower().strip()

    # Try to parse input
    parsed = None
    input_fmt = input_format.lower().strip()

    if input_fmt == "auto":
        parsed, input_fmt = _auto_parse(data)

    if not parsed:
        return f"Error: Could not parse input data as {input_fmt}."

    try:
        if output_fmt == "json":
            return json.dumps(parsed, ensure_ascii=False, indent=2)
        if output_fmt == "yaml":
            try:
                import yaml

                return yaml.dump(parsed, allow_unicode=True, default_flow_style=False)
            except ImportError:
                return "YAML output unavailable: pyyaml not installed."
        elif output_fmt == "csv":
            return _to_csv(parsed)
        elif output_fmt == "markdown_table" or output_fmt == "markdown":
            return _to_markdown_table(parsed)
        else:
            return f"Unsupported output format: {output_fmt}. Supported: json, yaml, csv, markdown_table"
    except Exception as e:
        return f"Conversion error: {type(e).__name__}: {e}"


def _auto_parse(data: str):
    """Auto-detect and parse data format."""
    data = data.strip()
    # JSON
    try:
        return json.loads(data), "json"
    except (json.JSONDecodeError, ValueError):
        pass
    # YAML
    try:
        import yaml

        result = yaml.safe_load(data)
        if isinstance(result, (dict, list)):
            return result, "yaml"
    except Exception:
        pass
    # CSV
    try:
        import csv
        import io

        reader = csv.reader(io.StringIO(data))
        rows = list(reader)
        if len(rows) > 1:
            headers = rows[0]
            result = [dict(zip(headers, row, strict=False)) for row in rows[1:]]
            return result, "csv"
    except Exception:
        pass
    return None, "unknown"


def _to_csv(data) -> str:
    """Convert structured data to CSV string."""
    import csv
    import io

    output = io.StringIO()
    if isinstance(data, list) and data and isinstance(data[0], dict):
        headers = list(data[0].keys())
        writer = csv.DictWriter(output, fieldnames=headers)
        writer.writeheader()
        writer.writerows(data)
    elif isinstance(data, list) and data and isinstance(data[0], list):
        writer = csv.writer(output)
        writer.writerows(data)
    elif isinstance(data, dict):
        writer = csv.writer(output)
        for k, v in data.items():
            writer.writerow([k, v])
    else:
        return "Error: Cannot convert this data structure to CSV."

    return output.getvalue().strip()


def _to_markdown_table(data) -> str:
    """Convert structured data to Markdown table."""
    if not isinstance(data, list) or not data:
        return "Error: Markdown table conversion requires a list of objects."

    if isinstance(data[0], dict):
        headers = list(data[0].keys())
        lines = []
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
        for row in data:
            lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")
        return "\n".join(lines)

    if isinstance(data[0], list):
        lines = []
        lines.append("| " + " | ".join(f"Col {i + 1}" for i in range(len(data[0]))) + " |")
        lines.append("| " + " | ".join(["---"] * len(data[0])) + " |")
        for row in data:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        return "\n".join(lines)

    return "Error: Cannot convert to Markdown table."


@register_tool(
    name="get_system_status",
    description=(
        "Query the DocMind system status **scoped to the caller's own organization**: "
        "the number of indexed chunks and document records belonging to this "
        "organization, plus Redis memory usage. Use this to check system health or "
        "answer questions about the size of the user's own knowledge base. "
        "Organization-scoped: it never returns system-wide totals across tenants."
    ),
    parameters={"type": "object", "properties": {}},
    tags=["utility", "system"],
    requires_auth=True,
    enabled_predicate=extended_surface_enabled,
)
async def get_system_status(organization_id: int | None = None, **_: Any) -> str:
    # 安全加固（issue #257）：此前签名为 `**_: Any`，注册表按签名过滤掉 org 上下文，
    # 于是本工具返回全集群/全库的全局计数（ES cat.indices 求和 + 无 where 的
    # documents/users count），任何租户（哪怕空库）都能看到别人的数据量。现在：
    #   · `requires_auth=True`——注册表 auth 钩子对缺 org 的调用 fail-closed；
    #   · 缺 org 直接拒答（防御绕过注册表的直接调用路径，与 compare_documents
    #     的 fail-closed 风格一致），不触达 ES/DB；
    #   · 三条查询全部按调用者所在组织过滤（ES 走 organization_id term 硬过滤）。
    if organization_id is None:
        return "Error: 无法确定组织范围，拒绝返回系统统计。"

    status: dict[str, Any] = {"organization_id": organization_id}

    # ES status —— 你所在组织的已索引 chunk 数（共享索引，按 organization_id 过滤）
    try:
        from app.core.config import settings
        from app.core.elasticsearch import es_client

        if es_client:
            res = await es_client.count(
                index=settings.ELASTICSEARCH_INDEX_NAME,
                query={"bool": {"filter": [{"term": {"organization_id": str(organization_id)}}]}},
            )
            status["elasticsearch"] = {
                "chunks": int(res.get("count", 0)),
            }
        else:
            status["elasticsearch"] = "Not connected"
    except Exception as e:
        status["elasticsearch"] = f"Error: {e}"

    # Redis status —— 进程级聚合（内存/连接数/uptime），不含租户数据归属，保持原样
    try:
        from app.core.redis import redis_client

        if redis_client:
            info = await redis_client.info("memory")
            status["redis"] = {
                "used_memory_mb": round(info.get("used_memory", 0) / (1024 * 1024), 1),
                "connected_clients": info.get("connected_clients", 0),
                "uptime_days": round(info.get("uptime_in_seconds", 0) / 86400, 1),
            }
        else:
            status["redis"] = "Not connected"
    except Exception as e:
        status["redis"] = f"Error: {e}"

    # DB status —— 全部按调用者所在组织过滤（此前无 where，返回全库行数）
    try:
        from sqlalchemy import func, select

        from app.core.database import AsyncSessionLocal
        from app.models.document import Document
        from app.models.user import User

        async with AsyncSessionLocal() as session:
            doc_count = (
                await session.execute(
                    select(func.count(Document.id)).where(
                        Document.organization_id == organization_id
                    )
                )
            ).scalar() or 0
            user_count = (
                await session.execute(
                    select(func.count(User.id)).where(User.organization_id == organization_id)
                )
            ).scalar() or 0
            status["database"] = {
                "documents": doc_count,
                "users": user_count,
            }
    except Exception as e:
        status["database"] = f"Error: {e}"

    return json.dumps(status, ensure_ascii=False, indent=2)


@register_tool(
    name="utility",
    description=("低风险本地工具（不执行代码）：计算/格式转换/时间/语言/数值统计。"),
    parameters={
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "calculate",
                    "format_converter",
                    "get_current_time",
                    "detect_language",
                    "analyze_data",
                ],
                "description": "操作",
            },
            "expression": {
                "type": "string",
                "description": "calculate: 表达式",
            },
            "data": {
                "type": "string",
                "description": "analyze_data/format_converter: 数据",
            },
            "output_format": {
                "type": "string",
                "description": "format_converter: json/yaml/csv/markdown_table",
                "default": "json",
            },
            "input_format": {
                "type": "string",
                "description": "format_converter: auto/json/yaml/csv",
                "default": "auto",
            },
            "text": {
                "type": "string",
                "description": "detect_language: 文本",
            },
        },
        "required": ["operation"],
    },
    # 扩展 tag：受限子代理（search/analysis）仍能看到 utility（含 analyze_data 等）。
    tags=["utility", "compute", "analysis", "data", "language", "format"],
    category="utility",
)
async def utility(
    operation: str,
    expression: str = "",
    data: str = "",
    output_format: str = "json",
    input_format: str = "auto",
    text: str = "",
    **_: Any,
) -> str | ToolResult:
    """低风险 utility 伞形（issue #509）。

    各 operation 直接调用原有独立 handler，**不经过 execute_python**（沙箱语义
    不变；这五个工具本来就是 low/免审批/纯进程内）。旧名经 TOOL_ALIASES 注入
    ``operation`` 默认参数。
    """
    op = str(operation or "").strip().lower()
    if op == "calculate":
        if not expression:
            return ToolResult.fail(
                code="validation_error", message="operation=calculate 需要 expression 参数"
            )
        return await calculate(expression)
    if op == "format_converter":
        if not data:
            return ToolResult.fail(
                code="validation_error", message="operation=format_converter 需要 data 参数"
            )
        return await format_converter(data, output_format, input_format)
    if op == "get_current_time":
        # 延迟导入：core_tools 末尾会 import tools 包（避免模块级循环）。
        from app.agent.core_tools import get_current_time as _get_current_time

        return await _get_current_time()
    if op == "detect_language":
        if not text:
            return ToolResult.fail(
                code="validation_error", message="operation=detect_language 需要 text 参数"
            )
        from app.agent.tools.translation import detect_language as _detect_language

        return await _detect_language(text)
    if op == "analyze_data":
        if not data:
            return ToolResult.fail(
                code="validation_error", message="operation=analyze_data 需要 data 参数"
            )
        from app.agent.tools.data_analysis import analyze_data as _analyze_data

        return await _analyze_data(data)
    return ToolResult.fail(
        code="validation_error",
        message=(
            f"未知 operation: {operation}（支持 calculate/format_converter/"
            "get_current_time/detect_language/analyze_data）"
        ),
    )
