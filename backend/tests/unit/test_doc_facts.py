"""事实不变量测试（W0-8）：文档关键数字必须等于源码派生值。

三层校验，任何一层漂移都会红：

1. ``backend/scripts/check_doc_facts.py`` 从源码派生事实（SSE 事件闭集、
   端点模块数、前端路由数、工具注册数、HTTP/WebSocket 路由装饰器数）；
2. 派生值必须与入库的单一事实源 ``docs/facts.json`` 完全一致——代码演进
   而 facts 未同步即红；
3. 文档中出现的同类数字必须与 facts JSON 一致（README / README_EN /
   architecture.html / index.html / AGENT_ARCHITECTURE.md 等锚点）。

另有一条运行时对照：静态派生的工具名集合必须等于真实工具注册表
（``tool_registry``）。该用例在未安装后端依赖的轻量 CI job（仅 pytest +
``--noconftest``）中自动跳过，静态检查仍会执行。

变异验证（PR 证据）：在 ``events.py`` 的 ``EventType`` 中临时加入一个事件，
``test_derived_facts_match_facts_json`` 必须红；撤销后恢复绿。改一处文档数字
同理触发 ``test_doc_numbers_match_facts_json`` 红。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "check_doc_facts.py"
FACTS_PATH = REPO_ROOT / "docs" / "facts.json"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_doc_facts", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _facts() -> dict:
    return json.loads(FACTS_PATH.read_text(encoding="utf-8"))


def test_facts_json_is_the_closed_set_of_derived_facts():
    payload = _facts()
    assert set(payload["facts"]) == set(checker.FACT_KEYS)
    assert all(isinstance(v, int) and v >= 0 for v in payload["facts"].values())


def test_derived_facts_match_facts_json():
    derived = checker.derive_all(REPO_ROOT)
    facts = _facts()["facts"]
    assert derived == facts, (
        "源码派生值与 docs/facts.json 不一致；若变化是有意的，请同步更新 "
        "facts JSON 与引用它的文档：\n"
        f"  derived={derived}\n  facts  ={facts}"
    )


def test_doc_numbers_match_facts_json():
    facts = _facts()["facts"]
    problems = checker.check_doc_anchors(REPO_ROOT, facts)
    assert not problems, "文档数字与 facts JSON 漂移:\n" + "\n".join(problems)


def test_required_fact_coverage_in_docs():
    covered = checker.covered_facts(REPO_ROOT)
    missing = checker.REQUIRED_DOC_FACTS - covered
    assert not missing, f"以下事实在文档中没有任何数字锚点: {sorted(missing)}"


def test_facts_json_is_referenced_from_readme():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/facts.json" in readme


def test_runtime_registry_matches_static_tool_derivation():
    try:
        import app.agent.core_tools  # noqa: F401
        import app.agent.tools  # noqa: F401
        from app.agent.registry import tool_registry
    except ImportError as exc:  # pragma: no cover - 轻量 hygiene job 路径
        pytest.skip(f"backend dependencies unavailable: {exc}")

    derived_names = set(checker.derive_registered_tool_names(BACKEND_ROOT / "app" / "agent"))
    # MCP 动态工具（mcp__<server>__<tool>）是运行时注册的外部工具，不在
    # 「内置工具实现数」口径内；过滤后再与静态派生集合比对。
    runtime_names = {name for name in tool_registry._tools if not name.startswith("mcp__")}
    assert derived_names == runtime_names, (
        "静态派生的工具名与运行时注册表不一致（静态派生漏算/多算模块）:"
        f"\n  static-only={sorted(derived_names - runtime_names)}"
        f"\n  runtime-only={sorted(runtime_names - derived_names)}"
    )


# ── 判别力测试：每个派生器都必须能真实数对、且不被注释/未导入模块骗过 ──


def test_sse_derivation_ignores_comments_and_rejects_dynamic_members(tmp_path):
    good = tmp_path / "events.py"
    good.write_text(
        "from typing import Literal\n"
        "EventType = Literal[\n"
        '    "thinking",  # 注释不是成员\n'
        '    "done",\n'
        '    "error",\n'
        "]\n"
        'ThinkingType = Literal["reasoning", "planning"]\n',
        encoding="utf-8",
    )
    assert checker.derive_sse_event_types(good) == ["thinking", "done", "error"]

    bad = tmp_path / "bad_events.py"
    bad.write_text(
        'from typing import Literal\nX = "x"\nEventType = Literal["a", X]\n',
        encoding="utf-8",
    )
    with pytest.raises(checker.DerivationError):
        checker.derive_sse_event_types(bad)


def test_endpoint_module_derivation_only_counts_api_router(tmp_path):
    router = tmp_path / "router.py"
    router.write_text(
        "from fastapi import APIRouter\n"
        "from app.api.v1.endpoints import auth, chat\n"
        "api_router = APIRouter()\n"
        "api_router.include_router(auth.router, prefix='/auth')\n"
        "api_router.include_router(chat.router, prefix='/chat')\n"
        "other = APIRouter()\n"
        "other.include_router(auth.router)\n",
        encoding="utf-8",
    )
    assert checker.derive_endpoint_modules(router) == ["auth", "chat"]


def test_frontend_route_scan_ignores_comments(tmp_path):
    ts = tmp_path / "index.ts"
    ts.write_text(
        "/*\n"
        "path: '/block-commented'\n"
        "*/\n"
        "export const routes = [\n"
        "  { path: '/a', name: 'A' },\n"
        "  {\n"
        "    path: 'nested',\n"
        "    children: [{ path: '/b' }], // inline path: '/fake'\n"
        "  },\n"
        "  { path: '/:pathMatch(.*)*' },\n"
        "]\n",
        encoding="utf-8",
    )
    assert checker.derive_frontend_routes(ts) == ["/a", "nested", "/b", "/:pathMatch(.*)*"]


def test_tool_derivation_follows_import_graph_only(tmp_path):
    agent = tmp_path / "agent"
    tools = agent / "tools"
    (tools / "pkg").mkdir(parents=True)
    (agent / "core_tools.py").write_text(
        "from app.agent.registry import register_tool\n"
        "@register_tool(name='core_one')\n"
        "async def core_one(**ctx): ...\n",
        encoding="utf-8",
    )
    (tools / "__init__.py").write_text("from app.agent.tools import one, pkg\n", encoding="utf-8")
    (tools / "one.py").write_text(
        "from app.agent.registry import register_tool\n"
        "@register_tool(name='tool_one')\n"
        "async def tool_one(**ctx): ...\n",
        encoding="utf-8",
    )
    (tools / "pkg" / "__init__.py").write_text(
        "from app.agent.tools.pkg import inner\n", encoding="utf-8"
    )
    (tools / "pkg" / "inner.py").write_text(
        "from app.agent.registry import register_tool\n"
        "@register_tool(name='tool_inner')\n"
        "async def tool_inner(**ctx): ...\n",
        encoding="utf-8",
    )
    (tools / "orphan.py").write_text(
        "from app.agent.registry import register_tool\n"
        "@register_tool(name='orphan')\n"
        "async def orphan(**ctx): ...\n",
        encoding="utf-8",
    )
    assert sorted(checker.derive_registered_tool_names(agent)) == [
        "core_one",
        "tool_inner",
        "tool_one",
    ], "未被 tools/__init__.py 导入的模块不得计入注册表"


def test_route_decorator_derivation_separates_websocket(tmp_path):
    endpoints = tmp_path / "endpoints"
    endpoints.mkdir()
    (endpoints / "x.py").write_text(
        "router = APIRouter()\n"
        "@router.get('/a')\n"
        "async def a(): ...\n"
        "@router.post('/b')\n"
        "async def b(): ...\n"
        "@router.websocket('/ws')\n"
        "async def ws(): ...\n"
        "@other.get('/not-router')\n"
        "async def c(): ...\n",
        encoding="utf-8",
    )
    http, websocket = checker.derive_route_decorators(endpoints)
    assert [item.rsplit(":", 1)[-1] for item in http] == ["a", "b"]
    assert [item.rsplit(":", 1)[-1] for item in websocket] == ["ws"]


def test_doc_anchor_mismatch_is_reported(tmp_path):
    (tmp_path / "README.md").write_text("可调用 12 个工具\n", encoding="utf-8")
    anchors = [
        checker.DocAnchor(
            fact="registered_tools",
            path="README.md",
            pattern=r"可调用 (\d+) 个工具",
            label="工具数",
        )
    ]
    problems = checker.check_doc_anchors(tmp_path, {"registered_tools": 33}, anchors=anchors)
    assert len(problems) == 1
    assert "README.md" in problems[0] and "12" in problems[0] and "33" in problems[0]


def test_required_doc_anchor_missing_is_reported(tmp_path):
    """必存文档上的锚点被删除/改写时必须红，防「删掉数字」绕过守护。"""
    (tmp_path / "README.md").write_text("可调用很多工具\n", encoding="utf-8")
    problems = checker.check_doc_anchors(
        tmp_path,
        {
            "sse_event_types": 20,
            "endpoint_modules": 22,
            "frontend_routes": 34,
            "registered_tools": 33,
        },
    )
    assert any("锚点缺失" in problem for problem in problems)


def test_optional_doc_anchor_missing_is_allowed(tmp_path):
    """非必存文档没有该数字时不算漂移（合成仓库只创建 README）。"""
    (tmp_path / "README.md").write_text(
        "文档数字见 docs/facts.json。\n"
        "可调用 33 个工具。Tool Registry (33)。\n"
        "**33 个内置工具**。合计 33 个内置工具。\n"
        "22 个端点模块。注册 22 个模块。端点实现（22 个文件）。\n"
        "34 条路由。20 种 SSE 事件。SSE 事件类型（20 种）。\n",
        encoding="utf-8",
    )
    problems = checker.check_doc_anchors(
        tmp_path,
        {
            "sse_event_types": 20,
            "endpoint_modules": 22,
            "frontend_routes": 34,
            "registered_tools": 33,
        },
    )
    assert problems == []


def _make_mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    backend = root / "backend"
    agent = backend / "app" / "agent"
    tools = agent / "tools"
    endpoints = backend / "app" / "api" / "v1" / "endpoints"
    frontend = root / "frontend" / "src" / "router"
    for directory in (tools, endpoints, frontend, root / "docs"):
        directory.mkdir(parents=True, exist_ok=True)
    (agent / "events.py").write_text(
        'from typing import Literal\nEventType = Literal["done", "error"]\n',
        encoding="utf-8",
    )
    (backend / "app" / "api" / "v1" / "router.py").write_text(
        "api_router.include_router(auth.router)\n", encoding="utf-8"
    )
    (endpoints / "x.py").write_text(
        "router = APIRouter()\n"
        "@router.get('/a')\n"
        "async def a(): ...\n"
        "@router.websocket('/ws')\n"
        "async def ws(): ...\n",
        encoding="utf-8",
    )
    (agent / "core_tools.py").write_text(
        "from app.agent.registry import register_tool\n"
        "@register_tool(name='core_one')\n"
        "async def core_one(**ctx): ...\n",
        encoding="utf-8",
    )
    (tools / "__init__.py").write_text("from app.agent.tools import one\n", encoding="utf-8")
    (tools / "one.py").write_text(
        "from app.agent.registry import register_tool\n"
        "@register_tool(name='tool_one')\n"
        "async def tool_one(**ctx): ...\n",
        encoding="utf-8",
    )
    (frontend / "index.ts").write_text("export const routes = [{ path: '/a' }]\n", encoding="utf-8")
    (root / "README.md").write_text(
        "文档数字见 docs/facts.json。\n"
        "可调用 2 个工具。\n"
        "Tool Registry (2)\n"
        "**2 个内置工具**\n"
        "tools/               #   扩展工具（2 个；合计 2 个内置工具）\n"
        "共 1 个端点模块。\n"
        "主路由（注册 1 个模块）\n"
        "端点实现（1 个文件）\n"
        "共 1 条路由。\n"
        "共 2 种 SSE 事件。\n"
        "SSE 事件类型（2 种）\n",
        encoding="utf-8",
    )
    facts = {
        "schema_version": 1,
        "facts": {
            "sse_event_types": 2,
            "endpoint_modules": 1,
            "frontend_routes": 1,
            "registered_tools": 2,
            "http_route_decorators": 1,
            "websocket_route_decorators": 1,
        },
    }
    (root / "docs" / "facts.json").write_text(
        json.dumps(facts, ensure_ascii=False), encoding="utf-8"
    )
    return root


def test_main_green_then_red_on_fact_drift(tmp_path):
    root = _make_mini_repo(tmp_path)
    assert checker.main(["--root", str(root)]) == 0

    facts_path = root / "docs" / "facts.json"
    payload = json.loads(facts_path.read_text(encoding="utf-8"))
    payload["facts"]["sse_event_types"] = 99
    facts_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert checker.main(["--root", str(root)]) == 1


def test_main_red_on_doc_number_drift(tmp_path):
    root = _make_mini_repo(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace("可调用 2 个工具", "可调用 3 个工具"),
        encoding="utf-8",
    )
    assert checker.main(["--root", str(root)]) == 1


def test_endpoint_module_derivation_prefers_router_specs(tmp_path):
    """issue #479：路由表改为 ROUTER_SPECS 后，端点模块数按字典键派生。

    即使文件里仍有 ``api_router.include_router(...)`` 调用（兼容导出/其它
    APIRouter），ROUTER_SPECS 键优先且保持声明顺序。
    """
    router = tmp_path / "router.py"
    router.write_text(
        "from fastapi import APIRouter\n"
        "ROUTER_SPECS: dict[str, tuple] = {\n"
        "    'auth': (auth.router, '/auth', ['认证'], 'core'),\n"
        "    'chat': (chat.router, '/chat', ['聊天'], 'rag'),\n"
        "    'monitoring': (monitoring.router, '/monitoring', ['系统监控'], 'observability'),\n"
        "}\n"
        "api_router = build_router(['core', 'rag', 'observability'])\n"
        "api_router.include_router(legacy.router)\n",
        encoding="utf-8",
    )
    assert checker.derive_endpoint_modules(router) == ["auth", "chat", "monitoring"]
