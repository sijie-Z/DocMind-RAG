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
    rag_source = "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(rag_dir.glob("*.py"))
    )
    assert not re.search(r"\bgraph_rag\b|\bsubgraph\b", rag_source, re.IGNORECASE), (
        "backend/app/rag 出现了图谱消费代码；若图谱已接入检索链路，"
        "请同步更新 README 的边界表述与本测试。"
    )
    compressor = _read("backend/app/rag/context_compressor.py")
    imports = re.findall(r"^(?:from|import)\s+([^\n]+)", compressor, re.MULTILINE)
    assert not any(re.search(r"llm|openai|anthropic", line, re.IGNORECASE) for line in imports), (
        "context_compressor.py 引入了 LLM 客户端；若改为 LLM 压缩，"
        "请同步更新 README 的表述与本测试。"
    )
    assert "await " not in compressor, (
        "context_compressor.py 出现异步调用（LLM 压缩的典型形态）；"
        "若改为 LLM 压缩，请同步更新 README 的表述与本测试。"
    )
    assert "extract_query_terms" in compressor, (
        "context_compressor.py 应仍是基于查询关键词的规则式句子抽取。"
    )


def test_help_page_steps_match_real_ui():
    """帮助页三条：PPT 支持 / 重试向量化 / 演示口令，必须与真实 UI 一致。"""
    _assert_absent(_HELP_VIEW, ["PowerPoint", "重试向量化", "guest / 123456", "guest/123456"])
    # 真实可达的操作入口（知识库页：行内"重新解析" + 工具栏"最近失败文档重建"）。
    _assert_present(_HELP_VIEW, ["重新解析", "最近失败文档重建"])
    # 账号获取的真实路径：登录页"去注册"（/register）与管理员"用户管理"。
    _assert_present(_HELP_VIEW, ["去注册", "用户管理"])

    parser = _read("backend/app/services/document_parser.py")
    if ".pptx" not in parser and "'.ppt'" not in parser:
        assert "PowerPoint" not in _read(_HELP_VIEW), (
            "解析器不支持 ppt/pptx，帮助页不得声称支持 PowerPoint。"
        )
    # 帮助页若声称固定演示口令，必须与随机密码实现冲突——由下面 locale 用例兜底。
    assert "123456" not in _read(_HELP_VIEW)


def test_locales_do_not_publish_fixed_demo_credentials():
    """四语 locale 不得再发布 guest / 123456 固定口令（密码为随机生成）。"""
    for rel in _LOCALES:
        text = _read(rel)
        assert "123456" not in text, f"{rel} 仍含固定弱口令 123456"
        assert "guest /" not in text, f"{rel} 仍发布固定演示账号口令"


def test_product_definition_image_capability_matches_code():
    """产品定义须承认图片 OCR 能力，同时写清不做图像理解/生成。"""
    _assert_absent("docs/product-definition.md", ["不处理图片"])
    _assert_present("docs/product-definition.md", ["OCR"])
    parser = _read("backend/app/services/document_parser.py")
    assert "'.png': self._parse_image" in parser, "图片 OCR 解析入口消失，请复核产品定义"
    _assert_present("docs/product-definition.md", ["不做图像理解"])


def test_architecture_numbers_match_code():
    """架构页数字与代码一致：33 个内置工具、22 个路由模块。"""
    _assert_absent(
        "docs/architecture.html",
        [
            "19 个工具实现",
            "19 个路由模块",
            "19 API 模块",
            "FastAPI · 19 模块",
        ],
    )
    _assert_present("docs/architecture.html", ["33 个工具实现", "22 个路由模块", "22 API 模块"])
    # 静态注册数 = @register_tool 装饰器数，防止口径再次漂移。
    tools_source = "\n".join(
        p.read_text(encoding="utf-8")
        for p in sorted((_REPO_ROOT / "backend" / "app" / "agent" / "tools").rglob("*.py"))
    ) + _read("backend/app/agent/core_tools.py")
    assert tools_source.count("@register_tool(") == 33, (
        "内置工具注册数不再是 33；请同步更新 README/docs/index.html/架构页。"
    )


def test_index_page_stats_are_current():
    """docs/index.html 不再使用 v1.2 / 1022 / 17 等过期数字。"""
    _assert_absent("docs/index.html", ["1022", "v1.2 ·", 'stat-num">17<'])
    _assert_present("docs/index.html", ["v1.22", "3084", 'stat-num">22<'])


def test_readme_pending_hardening_is_not_stale():
    """README 不得把已修项又列为"后续加固"。"""
    _assert_absent("README.md", ["后续加固：Agent 记忆接口按用户隔离"])
    # 已完成的加固收口应与 README_EN 口径一致。
    _assert_present("README.md", ["加固收口（已完成）"])
