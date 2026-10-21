"""CI 门禁：文档事实不变量 —— 文档关键数字必须等于源码派生值。

背景（W0-8）：README / 架构页里的「N 个工具 / N 个端点模块 / N 条路由 /
N 种 SSE 事件 / N 个路由装饰器」曾多次滞后于代码演进（例如 12 种事件、
19 个端点模块、21 条路由），而没有任何门禁能发现。本脚本把这些数字变成
可派生的事实：

    源码（single source of truth for behavior）
      → 派生值
      → docs/facts.json（入库的期望值，代码变化时显式更新）
      → 文档中出现的同类数字（README / README_EN / architecture.html ...）

三层必须一致；任一层漂移即退出码 1。脚本零依赖（仅标准库），可在
ci-hygiene 的轻量 job 中直接运行。

派生口径：
    sse_event_types          app/agent/events.py 的 EventType = Literal[...]
                             字符串成员数（闭集；含非字面量成员视为派生失败）
    endpoint_modules         app/api/v1/router.py 中 api_router.include_router(...)
                             调用数
    frontend_routes          frontend/src/router/index.ts 中路由记录 ``path:``
                             字符串字面量数（注释内的 path 不计）
    registered_tools         app/agent 下经 @register_tool(name=...) 注册的工具数；
                             只遍历 core_tools.py 与 tools/__init__.py 可达的
                             模块（与运行时 import 面一致，未接线的文件不计）
    http_route_decorators    app/api/v1/endpoints/** 中 @router.get/post/put/
                             patch/delete/head/options 装饰器总数
    websocket_route_decorators
                             app/api/v1/endpoints/** 中 @router.websocket 数（另计）

用法::

    python scripts/check_doc_facts.py               # 校验（CI 门禁）
    python scripts/check_doc_facts.py --print-derived  # 打印派生值（同步 facts 用）
    python scripts/check_doc_facts.py --root DIR    # 指定仓库根（测试用）

退出码：0 = 通过；1 = 派生值 / facts JSON / 文档数字不一致；2 = 源码或
facts JSON 无法派生（文件缺失、结构非法等）。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_DIR.parent

FACT_KEYS = (
    "sse_event_types",
    "endpoint_modules",
    "frontend_routes",
    "registered_tools",
    "http_route_decorators",
    "websocket_route_decorators",
)

# 需要在文档中至少出现一次的事实（HTTP 装饰器数不在对外文档里陈述，不强制）。
REQUIRED_DOC_FACTS = frozenset(
    {"sse_event_types", "endpoint_modules", "frontend_routes", "registered_tools"}
)

# 这些文档必然存在于仓库中：其中登记的锚点必须至少命中一次，防止「改写措辞
# 把数字删掉」让守护静默失效（其余文档的锚点缺失视为该文档没有陈述该数字）。
REQUIRED_DOC_FILES = frozenset(
    {
        "README.md",
        "README_EN.md",
        "docs/architecture.html",
        "docs/index.html",
        "AGENT_ARCHITECTURE.md",
    }
)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_AGENT_MODULE_PREFIX = "app.agent."
_TOOLS_MODULE_PREFIX = "app.agent.tools"


class DerivationError(RuntimeError):
    """源码结构不符合派生预期（宁可红，不可静默少算）。"""


# ──────────────────────────────────────────────────────────────────────────────
# 通用读取
# ──────────────────────────────────────────────────────────────────────────────


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise DerivationError(f"无法读取 {path}: {exc}") from exc


def _parse_python(path: Path) -> ast.Module:
    try:
        return ast.parse(_read_text(path), filename=str(path))
    except SyntaxError as exc:
        raise DerivationError(f"无法解析 {path}: {exc}") from exc


# ──────────────────────────────────────────────────────────────────────────────
# 1. SSE 事件闭集（events.py Literal）
# ──────────────────────────────────────────────────────────────────────────────


def _literal_string_members(value: ast.expr, where: str) -> list[str]:
    if not isinstance(value, ast.Subscript):
        raise DerivationError(
            f"{where}: EventType 必须是 Literal[...]，实际是 {type(value).__name__}"
        )
    base = value.value
    base_name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", None)
    if base_name != "Literal":
        raise DerivationError(f"{where}: EventType 必须是 Literal[...]，实际基底是 {base_name!r}")
    slice_node = value.slice
    members = slice_node.elts if isinstance(slice_node, ast.Tuple) else [slice_node]
    values: list[str] = []
    for member in members:
        if not (isinstance(member, ast.Constant) and isinstance(member.value, str)):
            raise DerivationError(
                f"{where}: EventType 含非字符串字面量成员（{ast.dump(member)[:80]}），"
                "事件闭集无法静态派生"
            )
        values.append(member.value)
    if len(values) != len(set(values)):
        raise DerivationError(f"{where}: EventType 存在重复成员: {values}")
    return values


def derive_sse_event_types(events_path: Path) -> list[str]:
    """返回 EventType Literal 的字符串成员（保持声明顺序）。"""
    tree = _parse_python(events_path)
    for node in tree.body:
        targets: list[ast.expr] = []
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        if any(isinstance(target, ast.Name) and target.id == "EventType" for target in targets):
            return _literal_string_members(value, str(events_path))
    raise DerivationError(f"{events_path}: 未找到顶层 EventType = Literal[...] 定义")


# ──────────────────────────────────────────────────────────────────────────────
# 2. 端点模块（router.py include_router）
# ──────────────────────────────────────────────────────────────────────────────


def derive_endpoint_modules(router_path: Path) -> list[str]:
    """返回 api_router.include_router(<module>.router, ...) 的模块名列表。"""
    tree = _parse_python(router_path)
    modules: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "include_router"
        ):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id == "api_router"):
            continue
        if not node.args:
            raise DerivationError(f"{router_path}:{node.lineno}: include_router 缺少模块参数")
        argument = node.args[0]
        if isinstance(argument, ast.Attribute) and isinstance(argument.value, ast.Name):
            modules.append(argument.value.id)
            continue
        if isinstance(argument, ast.Name):
            modules.append(argument.id)
            continue
        raise DerivationError(
            f"{router_path}:{node.lineno}: include_router 首参不是 <module>.router 形式，"
            f"无法派生模块名: {ast.dump(argument)[:80]}"
        )
    if not modules:
        raise DerivationError(f"{router_path}: 未找到任何 api_router.include_router(...) 调用")
    return modules


# ──────────────────────────────────────────────────────────────────────────────
# 3. 前端路由（router/index.ts 的 path:）
# ──────────────────────────────────────────────────────────────────────────────


def _tokenize_ts(source: str) -> list[tuple[str, str]]:
    """极简 TS tokenizer：注释丢弃，字符串/标识符/单字符标点各自成 token。

    只服务于「找出 routes 数组里路由对象的 path 属性」，不追求完整语法。
    假设路由文件不含正则字面量（含 ``/`` 的除号/正则不在本文件出现）。
    """
    tokens: list[tuple[str, str]] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        nxt = source[index + 1] if index + 1 < length else ""
        if char in " \t\r\n":
            index += 1
            continue
        if char == "/" and nxt == "/":
            while index < length and source[index] != "\n":
                index += 1
            continue
        if char == "/" and nxt == "*":
            index += 2
            while index < length and not (
                source[index] == "*" and index + 1 < length and source[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue
        if char in ("'", '"', "`"):
            quote = char
            index += 1
            value: list[str] = []
            while index < length and source[index] != quote:
                if source[index] == "\\" and index + 1 < length:
                    value.append(source[index + 1])
                    index += 2
                    continue
                value.append(source[index])
                index += 1
            index += 1
            tokens.append(("string", "".join(value)))
            continue
        if char.isalpha() or char in ("_", "$"):
            start = index
            while index < length and (source[index].isalnum() or source[index] in ("_", "$")):
                index += 1
            tokens.append(("ident", source[start:index]))
            continue
        if char.isdigit():
            start = index
            while index < length and (source[index].isdigit() or source[index] == "."):
                index += 1
            tokens.append(("number", source[start:index]))
            continue
        tokens.append(("punct", char))
        index += 1
    return tokens


def _match_bracket(tokens: list[tuple[str, str]], start: int) -> int:
    """返回与 tokens[start]（左括号）配对的右括号下标。"""
    pairs = {"[": "]", "{": "}", "(": ")"}
    opener = tokens[start][1]
    if tokens[start][0] != "punct" or opener not in pairs:
        raise DerivationError(f"token {start} 不是左括号: {tokens[start]!r}")
    close = pairs[opener]
    depth = 0
    for index in range(start, len(tokens)):
        kind, value = tokens[index]
        if kind != "punct":
            continue
        if value == opener:
            depth += 1
        elif value == close:
            depth -= 1
            if depth == 0:
                return index
    raise DerivationError(f"括号不配对: {opener} @ token {start}")


def _paths_in_route_object(tokens: list[tuple[str, str]], start: int, end: int) -> list[str]:
    """提取一个路由对象顶层的 path 字符串；递归进入 children 数组。"""
    paths: list[str] = []
    depth = 0
    index = start + 1
    while index < end:
        kind, value = tokens[index]
        if kind == "punct" and value in ("{", "[", "("):
            depth += 1
            index += 1
            continue
        if kind == "punct" and value in ("}", "]", ")"):
            depth -= 1
            index += 1
            continue
        if depth == 0 and kind == "ident" and value == "path":
            if (
                index + 2 < end
                and tokens[index + 1] == ("punct", ":")
                and tokens[index + 2][0] == "string"
            ):
                paths.append(tokens[index + 2][1])
                index += 3
                continue
        if depth == 0 and kind == "ident" and value == "children":
            if (
                index + 2 < end
                and tokens[index + 1] == ("punct", ":")
                and tokens[index + 2] == ("punct", "[")
            ):
                array_end = _match_bracket(tokens, index + 2)
                paths.extend(_paths_in_route_array(tokens, index + 2, array_end))
                index = array_end + 1
                continue
        index += 1
    return paths


def _paths_in_route_array(tokens: list[tuple[str, str]], start: int, end: int) -> list[str]:
    """提取一个路由数组（routes / children）中每个元素对象的 path。"""
    paths: list[str] = []
    index = start + 1
    while index < end:
        kind, value = tokens[index]
        if kind == "punct" and value == "{":
            object_end = _match_bracket(tokens, index)
            paths.extend(_paths_in_route_object(tokens, index, object_end))
            index = object_end + 1
            continue
        if kind == "punct" and value == "[":
            array_end = _match_bracket(tokens, index)
            paths.extend(_paths_in_route_array(tokens, index, array_end))
            index = array_end + 1
            continue
        index += 1
    return paths


def _find_routes_array(tokens: list[tuple[str, str]]) -> tuple[int, int]:
    """定位 ``routes = [...]`` 初始化数组的 token 区间。"""
    for index, (kind, value) in enumerate(tokens):
        if kind != "ident" or value != "routes":
            continue
        cursor = index + 1
        while cursor < len(tokens) and tokens[cursor] not in (("punct", "="), ("punct", ";")):
            cursor += 1
        if cursor >= len(tokens) or tokens[cursor] != ("punct", "="):
            continue
        cursor += 1
        if cursor < len(tokens) and tokens[cursor] == ("punct", "["):
            return cursor, _match_bracket(tokens, cursor)
    raise DerivationError("未找到 routes = [...] 路由数组")


def derive_frontend_routes(router_ts_path: Path) -> list[str]:
    """返回前端 ``routes`` 数组中的路由记录 path 列表（含 children；注释/回调内对象不计）。"""
    tokens = _tokenize_ts(_read_text(router_ts_path))
    start, end = _find_routes_array(tokens)
    routes = _paths_in_route_array(tokens, start, end)
    if not routes:
        raise DerivationError(f"{router_ts_path}: routes 数组中没有路由记录 path: 字面量")
    return routes


# ──────────────────────────────────────────────────────────────────────────────
# 4. 工具注册表（core_tools.py + tools/ 包，按 import 面遍历）
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_agent_module(module: str, agent_dir: Path) -> Path | None:
    if not module.startswith(_AGENT_MODULE_PREFIX):
        return None
    relative = module[len(_AGENT_MODULE_PREFIX) :]
    if not relative:
        return None
    file_path = agent_dir / (relative.replace(".", "/") + ".py")
    if file_path.is_file():
        return file_path
    package_path = agent_dir / relative.replace(".", "/") / "__init__.py"
    if package_path.is_file():
        return package_path
    return None


def _registered_tool_name(decorator: ast.expr, path: Path, lineno: int) -> str | None:
    func = decorator.func if isinstance(decorator, ast.Call) else decorator
    func_name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
    if func_name != "register_tool":
        return None
    if not isinstance(decorator, ast.Call):
        raise DerivationError(f"{path}:{lineno}: @register_tool 必须带 name= 关键字")
    for keyword in decorator.keywords:
        if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                return keyword.value.value
    raise DerivationError(f"{path}:{lineno}: @register_tool 缺少字符串 name=，无法派生工具名")


def derive_registered_tool_names(agent_dir: Path) -> list[str]:
    """按运行时 import 面遍历，返回 @register_tool(name=...) 注册的工具名。"""
    seeds = [agent_dir / "core_tools.py", agent_dir / "tools" / "__init__.py"]
    for seed in seeds:
        if not seed.is_file():
            raise DerivationError(f"{agent_dir}: 缺少工具入口文件 {seed.name}")
    queue: list[Path] = list(seeds)
    visited: set[Path] = set()
    names: list[str] = []
    seen: dict[str, Path] = {}
    while queue:
        path = queue.pop(0).resolve()
        if path in visited:
            continue
        visited.add(path)
        tree = _parse_python(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for decorator in node.decorator_list:
                    name = _registered_tool_name(decorator, path, node.lineno)
                    if name is None:
                        continue
                    if name in seen:
                        raise DerivationError(f"工具名重复注册: {name!r}（{path} 与 {seen[name]}）")
                    seen[name] = path
                    names.append(name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module == _TOOLS_MODULE_PREFIX or node.module.startswith(
                    _TOOLS_MODULE_PREFIX + "."
                ):
                    candidates = [node.module]
                    candidates.extend(f"{node.module}.{alias.name}" for alias in node.names)
                    for candidate in candidates:
                        resolved = _resolve_agent_module(candidate, agent_dir)
                        if resolved is not None:
                            queue.append(resolved)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == _TOOLS_MODULE_PREFIX or alias.name.startswith(
                        _TOOLS_MODULE_PREFIX + "."
                    ):
                        resolved = _resolve_agent_module(alias.name, agent_dir)
                        if resolved is not None:
                            queue.append(resolved)
    if not names:
        raise DerivationError(f"{agent_dir}: 未找到任何 @register_tool 注册")
    return names


# ──────────────────────────────────────────────────────────────────────────────
# 5. 路由装饰器（@router.*）
# ──────────────────────────────────────────────────────────────────────────────


def derive_route_decorators(endpoints_dir: Path) -> tuple[list[str], list[str]]:
    """返回 (HTTP 装饰器列表, WebSocket 装饰器列表)，元素形如 ``get:handler``。"""
    if not endpoints_dir.is_dir():
        raise DerivationError(f"端点评点目录不存在: {endpoints_dir}")
    http: list[str] = []
    websocket: list[str] = []
    for path in sorted(endpoints_dir.rglob("*.py")):
        tree = _parse_python(path)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if not isinstance(target, ast.Attribute):
                    continue
                owner = target.value
                if not (isinstance(owner, ast.Name) and owner.id == "router"):
                    continue
                if target.attr in _HTTP_METHODS:
                    http.append(f"{target.attr}:{node.name}")
                elif target.attr == "websocket":
                    websocket.append(f"websocket:{node.name}")
    if not http:
        raise DerivationError(f"{endpoints_dir}: 未找到任何 @router.<method> HTTP 装饰器")
    return http, websocket


# ──────────────────────────────────────────────────────────────────────────────
# 聚合派生
# ──────────────────────────────────────────────────────────────────────────────


def derive_all(root: Path = REPO_ROOT) -> dict[str, int]:
    """从仓库源码派生全部事实（int 值）。"""
    root = Path(root)
    backend = root / "backend"
    http, websocket = derive_route_decorators(backend / "app" / "api" / "v1" / "endpoints")
    return {
        "sse_event_types": len(derive_sse_event_types(backend / "app" / "agent" / "events.py")),
        "endpoint_modules": len(
            derive_endpoint_modules(backend / "app" / "api" / "v1" / "router.py")
        ),
        "frontend_routes": len(
            derive_frontend_routes(root / "frontend" / "src" / "router" / "index.ts")
        ),
        "registered_tools": len(derive_registered_tool_names(backend / "app" / "agent")),
        "http_route_decorators": len(http),
        "websocket_route_decorators": len(websocket),
    }


# ──────────────────────────────────────────────────────────────────────────────
# 文档数字锚点
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DocAnchor:
    """一处「文档中的关键数字」：命中 pattern 的第一捕获组必须等于 facts[fact]。"""

    fact: str
    path: str
    pattern: str
    label: str


DOC_ANCHORS: tuple[DocAnchor, ...] = (
    # ── README.md ──
    DocAnchor("registered_tools", "README.md", r"可调用 (\d+) 个工具", "工具数（简介）"),
    DocAnchor("registered_tools", "README.md", r"Tool Registry \((\d+)\)", "工具数（架构图）"),
    DocAnchor("registered_tools", "README.md", r"\*\*(\d+) 个内置工具\*\*", "工具数（工具清单）"),
    DocAnchor("registered_tools", "README.md", r"合计 (\d+) 个内置工具", "工具数（目录树合集）"),
    DocAnchor("endpoint_modules", "README.md", r"(\d+) 个端点模块", "端点模块数（API 层）"),
    DocAnchor(
        "endpoint_modules", "README.md", r"注册 (\d+) 个模块", "端点模块数（目录树 router.py）"
    ),
    DocAnchor(
        "endpoint_modules",
        "README.md",
        r"端点实现（(\d+) 个文件）",
        "端点文件数（目录树 endpoints/）",
    ),
    DocAnchor("frontend_routes", "README.md", r"(\d+) 条路由", "前端路由数"),
    DocAnchor("sse_event_types", "README.md", r"(\d+) 种 SSE 事件", "SSE 事件数（简介）"),
    DocAnchor("sse_event_types", "README.md", r"SSE 事件类型（(\d+) 种）", "SSE 事件数（目录树）"),
    # ── README_EN.md ──
    DocAnchor(
        "registered_tools",
        "README_EN.md",
        r"(\d+) tools for complex multi-step tasks",
        "tool count (intro)",
    ),
    DocAnchor(
        "registered_tools",
        "README_EN.md",
        r"Tool Registry \((\d+)\)",
        "tool count (architecture diagram)",
    ),
    DocAnchor(
        "registered_tools", "README_EN.md", r"\*\*(\d+) Built-In Tools\*\*", "tool count (catalog)"
    ),
    DocAnchor(
        "registered_tools",
        "README_EN.md",
        r"\((\d+) built-in tools total\)",
        "tool count (tree)",
    ),
    DocAnchor("endpoint_modules", "README_EN.md", r"(\d+) endpoint modules", "endpoint modules"),
    DocAnchor(
        "endpoint_modules",
        "README_EN.md",
        r"\((\d+) modules registered\)",
        "endpoint modules (tree)",
    ),
    DocAnchor(
        "endpoint_modules",
        "README_EN.md",
        r"Endpoint implementations \((\d+) files\)",
        "endpoint files (tree)",
    ),
    DocAnchor(
        "frontend_routes", "README_EN.md", r"(\d+) route records", "frontend routes (records)"
    ),
    DocAnchor("sse_event_types", "README_EN.md", r"(\d+) SSE event types", "SSE event types"),
    DocAnchor(
        "sse_event_types",
        "README_EN.md",
        r"SSE event types \((\d+) types\)",
        "SSE event types (tree)",
    ),
    # ── docs/architecture.html ──
    DocAnchor("registered_tools", "docs/architecture.html", r"(\d+) Agent 工具", "工具数（徽标）"),
    DocAnchor(
        "registered_tools",
        "docs/architecture.html",
        r'<div class="big">(\d+)</div><div class="sl">Agent 工具</div>',
        "工具数（指标卡）",
    ),
    DocAnchor("registered_tools", "docs/architecture.html", r"内置 (\d+) 个", "工具数（工具系统）"),
    DocAnchor(
        "registered_tools",
        "docs/architecture.html",
        r"合计 (\d+) 个内置工具",
        "工具数（目录树合集）",
    ),
    DocAnchor(
        "endpoint_modules",
        "docs/architecture.html",
        r"endpoints/.*#\s*(\d+) 个路由模块",
        "端点模块数（目录树）",
    ),
    DocAnchor(
        "endpoint_modules", "docs/architecture.html", r"(\d+) API 模块", "端点模块数（徽标）"
    ),
    DocAnchor(
        "endpoint_modules",
        "docs/architecture.html",
        r'<div class="big">(\d+)</div><div class="sl">后端 API 模块</div>',
        "端点模块数（指标卡）",
    ),
    DocAnchor(
        "endpoint_modules",
        "docs/architecture.html",
        r"FastAPI · (\d+) 路由模块",
        "端点模块数（后端架构标题）",
    ),
    # ── docs/index.html ──
    DocAnchor(
        "endpoint_modules",
        "docs/index.html",
        r'<div class="stat-num">(\d+)</div><div class="stat-lbl">API 模块</div>',
        "端点模块数（统计卡）",
    ),
    DocAnchor("registered_tools", "docs/index.html", r"(\d+) 个 Agent 工具", "工具数（meta）"),
    DocAnchor("registered_tools", "docs/index.html", r"(\d+) 个内置工具", "工具数（首屏）"),
    DocAnchor(
        "registered_tools",
        "docs/index.html",
        r'<div class="stat-num">(\d+)</div><div class="stat-lbl">Agent 工具</div>',
        "工具数（统计卡）",
    ),
    DocAnchor("registered_tools", "docs/index.html", r"内置 (\d+) 个专业工具", "工具数（工具区）"),
    # ── AGENT_ARCHITECTURE.md ──
    DocAnchor(
        "registered_tools",
        "AGENT_ARCHITECTURE.md",
        r"集成了 (\d+) 个生产级工具",
        "工具数（简介）",
    ),
    DocAnchor(
        "registered_tools", "AGENT_ARCHITECTURE.md", r"(\d+) 个工具描述", "工具数（System 提示）"
    ),
    DocAnchor(
        "registered_tools", "AGENT_ARCHITECTURE.md", r"工具生态（(\d+) 个内置", "工具数（工具生态）"
    ),
    DocAnchor(
        "registered_tools",
        "AGENT_ARCHITECTURE.md",
        r"(\d+) 个自注册工具",
        "工具数（对比表）",
    ),
    DocAnchor(
        "sse_event_types",
        "AGENT_ARCHITECTURE.md",
        r"(\d+) 种 SSE 事件类型",
        "SSE 事件数（特性表/协议/对比表）",
    ),
    DocAnchor(
        "sse_event_types",
        "AGENT_ARCHITECTURE.md",
        r"(\d+) 种 AgentEvent 类型",
        "SSE 事件数（文件索引）",
    ),
    DocAnchor(
        "sse_event_types",
        "AGENT_ARCHITECTURE.md",
        r"接收 (\d+) 种事件类型",
        "SSE 事件数（协议说明）",
    ),
    # ── 其它对外文档 ──
    DocAnchor(
        "registered_tools",
        "backend/seed_docs/architecture.md",
        r"(\d+) 个内置工具",
        "工具数（种子文档）",
    ),
    DocAnchor(
        "registered_tools", "docs/career/README.md", r"(\d+) 个内置工具", "工具数（履历文档）"
    ),
    DocAnchor("registered_tools", "docs/career/README.md", r"和 (\d+) 个工具", "工具数（面试稿）"),
)


def check_doc_anchors(
    root: Path, facts: dict[str, int], anchors: tuple[DocAnchor, ...] | None = None
) -> list[str]:
    """返回文档数字与 facts 不一致的清单。

    缺失语义：
    - 文档文件不存在 → 跳过（测试用的合成仓库）；
    - 文档存在但锚点一次都没命中 → 只有 ``REQUIRED_DOC_FILES`` 中的必存文档
      判失败（防止改写措辞把数字删掉后静默放行）；其余文档视为未陈述该数字。
    """
    root = Path(root)
    problems: list[str] = []
    for anchor in DOC_ANCHORS if anchors is None else anchors:
        expected = facts.get(anchor.fact)
        if expected is None:
            raise DerivationError(
                f"锚点 {anchor.label} 引用了 facts 中不存在的事实 {anchor.fact!r}"
            )
        path = root / anchor.path
        if not path.is_file():
            continue
        text = _read_text(path)
        matches = list(re.finditer(anchor.pattern, text, re.MULTILINE))
        if not matches:
            if anchor.path in REQUIRED_DOC_FILES:
                problems.append(
                    f"{anchor.path}: 锚点缺失（{anchor.label}）——必存文档必须保留该数字锚点"
                )
            continue
        for match in matches:
            raw = match.group(1)
            if not raw.isdigit():
                raise DerivationError(f"{anchor.path}: 锚点 {anchor.label} 捕获到非数字 {raw!r}")
            value = int(raw)
            if value != expected:
                lineno = text.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{anchor.path}:{lineno}: {anchor.label}: 文档={value}, facts={expected}"
                )
    return problems


def covered_facts(root: Path, anchors: tuple[DocAnchor, ...] | None = None) -> set[str]:
    """返回在文档锚点中至少出现一次的 fact 集合。"""
    root = Path(root)
    covered: set[str] = set()
    for anchor in DOC_ANCHORS if anchors is None else anchors:
        path = root / anchor.path
        if not path.is_file():
            continue
        if re.search(anchor.pattern, _read_text(path), re.MULTILINE):
            covered.add(anchor.fact)
    return covered


# ──────────────────────────────────────────────────────────────────────────────
# facts JSON
# ──────────────────────────────────────────────────────────────────────────────


def load_facts(facts_path: Path) -> dict[str, int]:
    try:
        payload = json.loads(_read_text(facts_path))
    except json.JSONDecodeError as exc:
        raise DerivationError(f"{facts_path}: JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("facts"), dict):
        raise DerivationError(f"{facts_path}: 顶层必须是对象且包含 facts 对象")
    values = payload["facts"]
    missing = set(FACT_KEYS) - set(values)
    unknown = set(values) - set(FACT_KEYS)
    if missing or unknown:
        raise DerivationError(
            f"{facts_path}: facts 键集必须恰为 {list(FACT_KEYS)}；"
            f"缺少={sorted(missing)}, 多出={sorted(unknown)}"
        )
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DerivationError(f"{facts_path}: facts[{key!r}] 必须是非负整数，实际 {value!r}")
    return values


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根目录（默认自动定位）")
    parser.add_argument(
        "--facts", type=Path, default=None, help="facts JSON 路径（默认 <root>/docs/facts.json）"
    )
    parser.add_argument(
        "--print-derived", action="store_true", help="只打印源码派生值（JSON），不校验"
    )
    args = parser.parse_args(argv)

    root: Path = args.root.resolve()
    if not root.is_dir():
        print(f"ERROR: 仓库根不存在: {root}", file=sys.stderr)
        return 2

    try:
        derived = derive_all(root)
    except DerivationError as exc:
        print(f"ERROR: 源码派生失败: {exc}", file=sys.stderr)
        return 2

    if args.print_derived:
        print(json.dumps(derived, ensure_ascii=False, indent=2))
        return 0

    facts_path = args.facts or (root / "docs" / "facts.json")
    try:
        facts = load_facts(facts_path)
        problems = [
            f"facts JSON 漂移: {key}: facts={facts[key]}, derived={derived[key]}"
            for key in FACT_KEYS
            if derived[key] != facts[key]
        ]
        problems.extend(check_doc_anchors(root, facts))
    except DerivationError as exc:
        print(f"ERROR: facts/文档校验失败: {exc}", file=sys.stderr)
        return 2

    readme = root / "README.md"
    if not readme.is_file():
        problems.append("README.md 缺失：无法确认 docs/facts.json 的引用入口")
    elif "docs/facts.json" not in _read_text(readme):
        problems.append("README.md 未引用 docs/facts.json（事实单一事实源入口）")

    if problems:
        print(
            "FAIL: 事实不变量被破坏（代码 / facts JSON / 文档数字必须三方一致）:", file=sys.stderr
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"OK: 事实不变量成立（{len(FACT_KEYS)} 项事实；派生值与 {facts_path} 及文档数字一致）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
