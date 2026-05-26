"""Utility tools — math, format conversion, system status, weather."""

import json
import logging
from typing import Any

from app.agent.registry import register_tool

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
)
async def calculate(expression: str, **_: Any) -> str:
    import ast
    import math
    import operator

    # Safe math functions and constants
    safe_dict = {
        "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
        "tan": math.tan, "asin": math.asin, "acos": math.acos,
        "atan": math.atan, "atan2": math.atan2, "log": math.log,
        "log2": math.log2, "log10": math.log10, "exp": math.exp,
        "abs": abs, "ceil": math.ceil, "floor": math.floor,
        "factorial": math.factorial, "gcd": math.gcd,
        "pi": math.pi, "e": math.e, "tau": math.tau,
        "degrees": math.degrees, "radians": math.radians,
        "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    }

    bin_ops = {
        ast.Add: operator.add, ast.Sub: operator.sub,
        ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg,
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
        "Query the DocMind system status including Elasticsearch document count, "
        "Redis memory usage, and database statistics. Use this to check system health "
        "or answer questions about the knowledge base size."
    ),
    parameters={"type": "object", "properties": {}},
    tags=["utility", "system"],
)
async def get_system_status(**_: Any) -> str:
    status = {}

    # ES status
    try:
        from app.core.elasticsearch import es_client
        if es_client:
            indices = await es_client.cat.indices(format="json")
            doc_count = sum(int(idx.get("docs.count", 0)) for idx in indices)
            store_size = sum(int(idx.get("store.size", 0)) for idx in indices)
            status["elasticsearch"] = {
                "indices": len(indices),
                "total_documents": doc_count,
                "total_size_bytes": store_size,
                "total_size_mb": round(store_size / (1024 * 1024), 1),
            }
        else:
            status["elasticsearch"] = "Not connected"
    except Exception as e:
        status["elasticsearch"] = f"Error: {e}"

    # Redis status
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

    # DB status
    try:
        from sqlalchemy import func, select

        from app.core.database import AsyncSessionLocal
        from app.models.document import Document
        from app.models.user import User

        async with AsyncSessionLocal() as session:
            doc_count = (await session.execute(select(func.count(Document.id)))).scalar() or 0
            user_count = (await session.execute(select(func.count(User.id)))).scalar() or 0
            status["database"] = {
                "documents": doc_count,
                "users": user_count,
            }
    except Exception as e:
        status["database"] = f"Error: {e}"

    return json.dumps(status, ensure_ascii=False, indent=2)
