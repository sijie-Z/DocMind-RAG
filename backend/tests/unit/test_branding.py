"""品牌防回归测试（ISS-2 品牌清扫）。

锁死后端 app/ 下的旧品牌残留：
- 所有 docstring 不得出现"派聪明 / paicongming"（不区分大小写）；
- 源码任意位置不得出现中文旧品牌"派聪明"（含日志、注释、字符串）。

注意：``ELASTICSEARCH_INDEX_NAME`` 默认值 "paicongming_knowledge" 是线上
数据物名（ES 索引），属"分级不动"项——所以第二条只禁中文旧品牌，
不扫 plain paicongming。
"""
import ast
import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"

_BRAND_RESIDUE = re.compile(r"派聪明|paicongming", re.IGNORECASE)
_CJK_BRAND = re.compile("派聪明")


def _iter_py_files():
    return sorted(APP_DIR.rglob("*.py"))


def _collect_docstrings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                yield getattr(node, "lineno", 1), doc


def test_no_brand_in_docstrings():
    offenders = []
    for path in _iter_py_files():
        for lineno, doc in _collect_docstrings(path):
            if _BRAND_RESIDUE.search(doc):
                offenders.append(f"{path.relative_to(APP_DIR)}:{lineno}")
    assert not offenders, f"docstring 品牌残留：{offenders}"


def test_no_cjk_brand_anywhere_in_app_source():
    offenders = []
    for path in _iter_py_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _CJK_BRAND.search(line):
                offenders.append(f"{path.relative_to(APP_DIR)}:{lineno}")
    assert not offenders, f"app/ 源码中文旧品牌残留：{offenders}"
