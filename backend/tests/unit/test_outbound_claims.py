"""对外口径断言（W0-7 / issue #416）：对外文档与帮助页声称的能力必须与代码一致。

背景：多处对外表述与实现脱节——
- README 把图服务写成"知识图谱增强检索"，但 backend/app/rag 不消费图谱
  （graph_rag_service.get_subgraph_context 无外部调用）；
- README 把 ContextCompressor 写成 LLM 压缩，实为关键词句子抽取（无 LLM 调用）；
- 帮助页声称支持 PowerPoint、"重试向量化"按钮、guest / 123456 演示口令，
  实际解析器没有 ppt/pptx、该按钮从未可达、演示账号默认关闭且密码随机；
- 产品定义称"不处理图片"，实际 OCR 支持 png/jpg/jpeg/webp/bmp/gif；
- 架构页仍写 19 个工具/路由模块，README 把已修项又列为"后续加固"；
- docs/index.html 仍是 v1.2 / 1022 测试 / 17 API 模块。

本用例是静态口径门禁（不依赖网络/数据库，CI 可 --noconftest 运行）：
错误表述回归即红。变异点：任一被禁表述写回对应文件 → 对应用例精确红。

可复跑命令（backend 目录下）：
    python -m pytest tests/unit/test_outbound_claims.py -q --noconftest
"""

import ast
import re
from pathlib import Path

_UNIT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _UNIT_DIR.parents[2]

_HELP_VIEW = "frontend/src/views/systemHelp/index.vue"
_LOCALES = [
    "frontend/src/locales/zh.ts",
    "frontend/src/locales/en.ts",
    "frontend/src/locales/ja.ts",
    "frontend/src/locales/fr.ts",
]


def _read(rel: str) -> str:
    return (_REPO_ROOT / rel).read_text(encoding="utf-8")


def _assert_absent(rel: str, needles: list[str]) -> None:
    text = _read(rel)
    hits = [n for n in needles if n in text]
    assert not hits, f"{rel} 仍含过期/错误表述: {hits}"


def _assert_present(rel: str, needles: list[str]) -> None:
    text = _read(rel)
    missing = [n for n in needles if n not in text]
    assert not missing, f"{rel} 缺少与代码一致的必要表述: {missing}"


def test_readme_graph_and_compressor_claims_match_code():
    """README（中英）不得再声称图谱增强检索 / LLM 上下文压缩。"""
    _assert_absent(
        "README.md",
        ["知识图谱增强检索", "LLM 上下文压缩", "基于 LLM 的检索结果压缩"],
    )
    _assert_absent(
        "README_EN.md",
        [
            "Knowledge graph enhanced retrieval",
            "LLM context compression",
            "LLM-based context pruning",
        ],
    )
    # 边界必须写清：图谱不参与 RAG 检索；压缩为规则式、无 LLM 调用。
    _assert_present("README.md", ["不参与 RAG 检索链路", "规则式，无 LLM 调用"])
    _assert_present(
        "README_EN.md",
        ["not part of the RAG retrieval path", "rule-based, no LLM call"],
    )


def test_graph_and_compressor_code_evidence():
    """口径依据本身也守住：RAG 模块不消费图谱；压缩器不调用 LLM。"""
    rag_dir = _REPO_ROOT / "backend" / "app" / "rag"
    rag_source = "\n".join(p.read_text(encoding="utf-8") for p in sorted(rag_dir.glob("*.py")))
    assert not re.search(r"graph_rag|subgraph", rag_source, re.IGNORECASE), (
        "backend/app/rag 出现了图谱消费代码；若图谱已接入检索链路，"
        "请同步更新 README 的边界表述与本测试。"
    )
    compressor = _read("backend/app/rag/context_compressor.py")
    tree = ast.parse(compressor)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(
        re.search(r"llm|openai|anthropic|langchain", module, re.IGNORECASE) for module in imported
    ), (
        "context_compressor.py 引入了 LLM 客户端；若改为 LLM 压缩，"
        "请同步更新 README 的表述与本测试。"
    )
    assert not any(
        isinstance(node, (ast.Await, ast.AsyncFunctionDef)) for node in ast.walk(tree)
    ), (
        "context_compressor.py 出现异步函数/调用（LLM 压缩的典型形态）；"
        "若改为 LLM 压缩，请同步更新 README 的表述与本测试。"
    )
    assert "extract_query_terms" in compressor, (
        "context_compressor.py 应仍是基于查询关键词的规则式句子抽取。"
    )
    pipeline = _read("backend/app/rag/pipeline.py")
    assert "compress_context_list(" in pipeline, (
        "pipeline.py 不再调用 compress_context_list；README 的 RAG 管线描述需同步复核。"
    )


def test_help_page_steps_match_real_ui():
    """帮助内容（guide markdown + 帮助页视图）：PPT 支持 / 重试向量化 / 演示口令，必须与真实 UI 一致。"""
    guide_dir = _REPO_ROOT / "frontend" / "src" / "content" / "guide"
    guide_files = sorted(guide_dir.rglob("*.md"))
    assert guide_files, "帮助中心正文（frontend/src/content/guide/**/*.md）缺失"
    help_text = "\n".join(p.read_text(encoding="utf-8") for p in guide_files)
    help_text += "\n" + _read(_HELP_VIEW)

    for needle in ["重试向量化", "guest / 123456", "guest/123456"]:
        assert needle not in help_text, f"帮助内容仍含过期/错误表述: {needle}"
    # 正向断言真实支持格式（与上传面板 accept / 解析器一致）。
    for needle in ["PDF", "Word", ".docx", ".xlsx", "Markdown", "CSV", "OCR"]:
        assert needle in help_text, f"帮助内容缺少与代码一致的必要表述: {needle}"
    # 真实可达的操作入口（知识库页：行内"重新解析" + 工具栏"最近失败文档重建"）。
    for needle in ["重新解析", "最近失败文档重建", "红色错误提示条"]:
        assert needle in help_text, f"帮助内容缺少与代码一致的必要表述: {needle}"
    # 账号获取的真实路径：登录页"去注册"（/register）与管理员"用户管理"。
    for needle in ["去注册", "用户管理"]:
        assert needle in help_text, f"帮助内容缺少与代码一致的必要表述: {needle}"

    parser = _read("backend/app/services/document_parser.py")
    if ".pptx" not in parser and "'.ppt'" not in parser:
        assert "PowerPoint" not in help_text, (
            "解析器不支持 ppt/pptx，帮助内容不得声称支持 PowerPoint。"
        )
    assert "123456" not in help_text


def test_locales_do_not_publish_fixed_demo_credentials():
    """四语 locale 不得再发布 guest / 123456 固定口令（密码为随机生成）。"""
    for rel in _LOCALES:
        text = _read(rel)
        assert "123456" not in text, f"{rel} 仍含固定弱口令 123456"
        assert "guest /" not in text, f"{rel} 仍发布固定演示账号口令"


def test_product_definition_image_capability_matches_code():
    """产品定义须承认图片 OCR 能力，同时写清不做图像理解/生成。"""
    _assert_absent(
        "docs/product-definition.md",
        ["不处理图片", "911 个后端测试", "111 个前端测试", "89 个测试文件", "guest 账号自动初始化"],
    )
    _assert_present(
        "docs/product-definition.md",
        ["OCR", "冻结快照", "2223", "861", "ENABLE_DEMO_ACCOUNT", "ENABLE_ENSURE_DEMO_ENDPOINT"],
    )
    parser = _read("backend/app/services/document_parser.py")
    # 引号风格随 ruff format 归一（#216），断言按 AST 语义容忍单双引号。
    assert re.search(r"['\"]\.png['\"]\s*:\s*self\._parse_image", parser), (
        "图片 OCR 解析入口消失，请复核产品定义"
    )
    _assert_present("docs/product-definition.md", ["不做图像理解"])


def test_architecture_numbers_match_code():
    """架构页数字与代码一致：33 个内置工具、22 个路由模块，且不得虚报 Celery。"""
    _assert_absent(
        "docs/architecture.html",
        [
            "19 个工具",
            "19 个路由模块",
            "19 API 模块",
            "FastAPI · 19 模块",
            "Celery",
        ],
    )
    _assert_present(
        "docs/architecture.html",
        ["33 个内置工具", "基础工具（14 个）", "22 API 模块", "22 个路由模块"],
    )
    # 静态注册数 = @register_tool 装饰器数，防止口径再次漂移。
    tools_source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((_REPO_ROOT / "backend" / "app" / "agent" / "tools").rglob("*.py"))
    ) + _read("backend/app/agent/core_tools.py")
    assert tools_source.count("@register_tool(") == 33, (
        "内置工具注册数不再是 33；请同步更新 README/docs/index.html/架构页。"
    )


def test_index_page_stats_are_current():
    """docs/index.html 不再使用 v1.2 / 1022 / 17 等过期数字，且测试数带快照口径。"""
    _assert_absent("docs/index.html", ["1022", "v1.2 ·", 'stat-num">17<'])
    _assert_present("docs/index.html", ["v1.22", "3084", "冻结快照", 'stat-num">22<'])


def test_readme_module_counts_match_code():
    """README/README_EN 的模块、服务、实体、视图与路由数必须与代码一致。"""
    endpoints_dir = _REPO_ROOT / "backend" / "app" / "api" / "v1" / "endpoints"
    endpoints = list(endpoints_dir.glob("*.py"))
    router_source = _read("backend/app/api/v1/router.py")
    services = [
        p
        for p in (_REPO_ROOT / "backend" / "app" / "services").glob("*.py")
        if p.name != "__init__.py"
    ]
    models = [
        p
        for p in (_REPO_ROOT / "backend" / "app" / "models").glob("*.py")
        if p.name != "__init__.py"
    ]
    views = [
        p
        for p in (_REPO_ROOT / "frontend" / "src" / "views").iterdir()
        if (p.is_dir() and p.name != "__tests__") or (p.is_file() and p.suffix == ".vue")
    ]
    route_source = _read("frontend/src/router/index.ts")
    route_records = re.findall(r"^\s*path: '", route_source, re.MULTILINE)
    route_names = re.findall(r"^\s*name: '", route_source, re.MULTILINE)
    counts = {
        "endpoints": len(endpoints),
        "routers": router_source.count("include_router("),
        "services": len(services),
        "models": len(models),
        "views": len(views),
        "route_records": len(route_records),
        "route_names": len(route_names),
    }
    assert counts == {
        "endpoints": 22,
        "routers": 22,
        "services": 21,
        "models": 18,
        "views": 23,
        "route_records": 34,
        "route_names": 29,
    }, f"代码侧计数已漂移: {counts}；请同步 README 与本测试"

    def _api_table_data_rows(rel: str, start: str) -> int:
        block = _read(rel).split(start, 1)[1].split("Swagger", 1)[0]
        rows = [line for line in block.splitlines() if line.startswith("|")]
        return len(rows) - 2  # 去掉表头与分隔行

    assert _api_table_data_rows("README.md", "### 🌐 API 层") == 22, "README API 表行数应为 22"
    assert _api_table_data_rows("README_EN.md", "### 🌐 API Layer") == 22, (
        "README_EN API 表行数应为 22"
    )

    _assert_absent(
        "README.md",
        [
            "19 个端点模块",
            "注册 19 个模块",
            "19 个文件",
            "14 个服务",
            "20 个服务",
            "14 个实体",
            "18 个实体",
            "19 个模块，21 条路由",
        ],
    )
    _assert_present(
        "README.md",
        [
            "22 个端点模块",
            "注册 22 个模块",
            "22 个文件",
            "21 个服务",
            "18 个模型文件",
            "23 个页面模块，34 条路由记录 / 29 条具名路由",
            "34 条路由记录（29 条具名路由",
        ],
    )
    _assert_absent(
        "README_EN.md",
        [
            "19 endpoint modules",
            "19 modules registered",
            "19 files",
            "14 services",
            "20 services",
            "14 entities",
            "18 entities",
            "19 modules, 21 routes",
        ],
    )
    _assert_present(
        "README_EN.md",
        [
            "22 endpoint modules",
            "22 modules registered",
            "22 files",
            "21 services",
            "18 model files",
            "23 page modules, 34 route records / 29 named routes",
            "34 route records (29 named routes",
        ],
    )


def test_sse_event_count_matches_code():
    """README/EN 的 SSE 事件数必须与 EventType 闭集一致（当前 20）。"""
    events_source = _read("backend/app/agent/events.py")
    match = re.search(r"EventType\s*=\s*Literal\[(.*?)\]", events_source, re.DOTALL)
    assert match, "events.py 中未找到 EventType Literal 定义"
    event_types = re.findall(r'"([^"]+)"', match.group(1))
    assert len(event_types) == 20, (
        f"SSE 事件闭集已变化（{len(event_types)} 个）；请同步 README/README_EN 与本测试"
    )
    _assert_absent("README.md", ["12 种 SSE 事件", "SSE 事件类型（12 种）"])
    _assert_present("README.md", ["20 种 SSE 事件", "SSE 事件类型（20 种）"])
    _assert_absent("README_EN.md", ["12 SSE event types", "SSE event types (12 types)"])
    _assert_present("README_EN.md", ["20 SSE event types", "SSE event types (20 types)"])


def test_readme_pending_hardening_is_not_stale():
    """README 不得把已修项又列为"后续加固"。"""
    _assert_absent("README.md", ["后续加固：Agent 记忆接口按用户隔离"])
    # 已完成的加固收口应与 README_EN 口径一致。
    _assert_present("README.md", ["加固收口（已完成）"])
