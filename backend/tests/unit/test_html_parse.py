"""HTML 文档入库：抽正文、丢脚本样式，标题进 metadata。

背景（2026-11-14 真实语料）：用户语料目录里有 477 个 .html 文件，上传一律 400
（扩展名不在 supported_extensions）。HTML 是常见导出格式（博客、Confluence、
网页存档），缺它等于这类资料完全进不了知识库。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.document_parser import DocumentParser

HTML = """<!doctype html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>采购制度摘录</title>
  <style>body { color: red; } .x { display: none; }</style>
  <script>var leak = "这段脚本文字绝不应该出现在正文里";</script>
</head>
<body>
  <nav>导航栏文字</nav>
  <article>
    <h1>采购制度摘录</h1>
    <p>第5条 付款条件：合同付款须经财务与法务复核。</p>
    <p>第9条 归档：合同与验收单保存不少于五年。</p>
    <ul><li>附件一：验收单模板</li></ul>
  </article>
  <footer>页脚版权文字</footer>
</body>
</html>
"""


def test_html_parses_body_text_and_drops_script(tmp_path: Path) -> None:
    target = tmp_path / "policy.html"
    target.write_text(HTML, encoding="utf-8")

    result = asyncio.run(DocumentParser().parse_local_file(str(target), "1"))
    content = result["content"]

    assert "付款条件" in content
    assert "验收单模板" in content
    # 脚本与样式不得混入正文（否则会污染检索与嵌入）
    assert "这段脚本文字绝不应该出现在正文里" not in content
    assert "display: none" not in content
