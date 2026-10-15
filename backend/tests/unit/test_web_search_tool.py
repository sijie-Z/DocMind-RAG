"""web_search 工具判别测试：后端正常/故障/缺包三态 × ddgs 新名与旧名兜底。

背景（harness 审计·维度1「web：有但半成品」）：web_search 是真调 DuckDuckGo 的
实现，但依赖包（新名 ddgs / 旧名 duckduckgo-search）既未入 requirements.txt 也未
装进环境 → 任何全新环境下该工具恒失败。补齐动作：requirements 声明 ddgs；代码改
「ddgs 优先、duckduckgo_search 兜底」两段 import。

本文件钉住四条契约（全部 mock 掉真实网络，不造假数据）：
  ① 正常路径：结果条数与字段映射逐条对位（title→标题行 / body→snippet（200 字
     截断）/ href→url 行），max_results 帽到 10；空结果如实报「无结果」；
  ② 故障路径：上游异常（进入即抛 / 迭代中途抛）→ 结构化 ToolResult.fail，
     已取到的部分结果不得伪装成成功数据回流；
  ③ 缺包路径：两段 import 都不可用 → invalid_configuration 结构化失败，
     安装提示指向实际声明的包名 ddgs；
  ④ 旧名兜底：ddgs 不可用但 duckduckgo_search 在 → 工具仍可用。

变异矩阵（每条应精确红）：
  M1  import 段删掉 ddgs 优先（只留旧名）→ 用例 success/empty/enter-exc/mid-iter 红
      （missing 不红——该用例两段 import 本就全断，与 import 顺序无关）；
  M2  故障路径还原为错误字符串（旧 #293 形态）→ 用例 2a/2b 红（success=True）；
  M3  字段映射错位（url 取 body 或 title 取 href）→ 用例 1 红；
  M4  删旧名兜底分支 → 用例 4 红；
  M5  缺包提示还原为旧文案（pip install duckduckgo-search）→ 用例 3 红。
"""

import sys
import types
from unittest.mock import AsyncMock

import pytest

import app.agent.tools.web_search as ws_mod
from app.agent.registry import tool_registry

# ── 假件 ────────────────────────────────────────────────────────────────────

_SAMPLE: list[dict[str, str]] = [
    {
        "title": "Python 3.12.0 is released",
        "href": "https://www.python.org/downloads/release/python-3120/",
        "body": "CPython 3.12.0 is now available.",
    },
    {
        "title": "What's New In Python 3.12",
        "href": "https://docs.python.org/3/whatsnew/3.12.html",
        "body": "This article explains the new features.",
    },
    {
        "title": "Long body entry",
        "href": "https://example.com/long-body",
        "body": "L" * 250,
    },
]


def _install_fake_backend(
    monkeypatch,
    *,
    results=None,
    enter_exc=None,
    mid_iter_exc=None,
    names=("ddgs",),
):
    """注入假搜索后端（同一假件可挂到给定的一个或多个模块名上）。

    返回 calls 列表：每次 text() 调用记录 {query, max_results}，供断言参数透传。
    """

    calls: list[dict] = []

    class _FakeDDGS:
        def __enter__(self):
            if enter_exc is not None:
                raise enter_exc
            return self

        def __exit__(self, *a):
            return False

        def text(self, query, max_results=None):
            calls.append({"query": query, "max_results": max_results})
            if mid_iter_exc is not None:

                def _gen():
                    yield (results or [{}])[0]
                    raise mid_iter_exc

                return _gen()
            return list(results or [])

    for name in names:
        mod = types.ModuleType(name)
        mod.DDGS = _FakeDDGS
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(ws_mod, "_check_rate_limit", AsyncMock(return_value=True))
    return calls


def _block(monkeypatch, *names: str) -> None:
    """把模块名置 None：`from <name> import ...` 立即 ImportError（模拟未安装）。"""
    for name in names:
        monkeypatch.setitem(sys.modules, name, None)


# ── ① 正常路径：字段映射 / 条数 / 帽子 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_web_search_success_maps_fields_and_clamps_max_results(monkeypatch):
    """成功路径：title/href/body 逐条对位映射，max_results 帽 10，旧名全断也走通。

    变异 M3（url 取 body、title 取 href 等错位）→ 本条红；
    变异 M1（只留旧名 import，而旧名被 _block 全断）→ 本条红。
    """
    calls = _install_fake_backend(monkeypatch, results=_SAMPLE)
    _block(monkeypatch, "duckduckgo_search")  # 新名单路径即可用

    result = await tool_registry.execute_detailed(
        "web_search", {"query": "Python 3.12 release", "max_results": 50}, user_id=1
    )

    assert result.success is True, result.to_dict()
    assert result.data is not None and isinstance(result.data, str)
    assert result.meta.tool_name == "web_search"
    assert calls == [{"query": "Python 3.12 release", "max_results": 10}], (
        "max_results 必须帽到 10 并原样透传给后端"
    )

    data = result.data
    assert data.startswith("Web search results for: Python 3.12 release")
    # 每条结果 = [i] 标题行 + snippet 行 + url 行；逐条对位，不造假字段
    for i, r in enumerate(_SAMPLE[:2], 1):
        assert f"[{i}] {r['title']}" in data, f"第 {i} 条标题未对位"
        assert r["body"] in data, f"第 {i} 条 snippet 未对位"
        assert r["href"] in data, f"第 {i} 条 url 未对位"
    # snippet 截断 200：长 body 只留前 200 字符
    assert "L" * 200 in data and "L" * 201 not in data, "snippet 必须截断到 200 字符"
    # 条数：恰 3 条、从 1 编号
    numbered = [ln for ln in data.splitlines() if ln.startswith("[")]
    assert len(numbered) == 3 and numbered[0] == f"[1] {_SAMPLE[0]['title']}"


@pytest.mark.asyncio
async def test_web_search_empty_results_reported_honestly(monkeypatch):
    """后端返回空列表：如实报「无结果」，不得编造内容、也不得报错。"""
    _install_fake_backend(monkeypatch, results=[])

    result = await tool_registry.execute_detailed(
        "web_search", {"query": "no-such-thing"}, user_id=1
    )

    assert result.success is True, result.to_dict()
    assert result.data == "No web results found for: no-such-thing"


# ── ② 故障路径：上游异常必须结构化 fail，部分结果不得伪装成功 ────────────────


@pytest.mark.asyncio
async def test_web_search_backend_enter_failure_is_structured_fail(monkeypatch):
    """后端进入（建连）即抛：结构化 ToolResult.fail（unknown，#293 契约），
    str() 保持 'Error: [code] ...' 前缀契约。

    变异 M2（还原为错误字符串）→ success 归一为 True → 本条红。
    """
    _install_fake_backend(monkeypatch, enter_exc=RuntimeError("boom-enter"))

    result = await tool_registry.execute_detailed("web_search", {"query": "q"}, user_id=1)

    assert result.success is False, f"上游故障不得伪装成功: {result.to_dict()}"
    assert result.error is not None and result.error.code == "unknown"
    assert "RuntimeError" in result.error.message and "boom-enter" in result.error.message
    assert str(result).startswith("Error: [unknown]")


@pytest.mark.asyncio
async def test_web_search_mid_iteration_failure_does_not_return_partial_results(monkeypatch):
    """迭代中途抛（已取到第 1 条）→ 结构化 fail，且部分结果不得作为成功数据回流。

    变异 M2（还原为错误字符串）→ 本条红；若有人把已取到的部分结果
    静默当成功返回（"best effort 伪装"）→ 本条红。
    """
    _install_fake_backend(monkeypatch, results=_SAMPLE, mid_iter_exc=TimeoutError("read timeout"))

    result = await tool_registry.execute_detailed("web_search", {"query": "q"}, user_id=1)

    assert result.success is False, f"中途失败不得伪装成功: {result.to_dict()}"
    assert result.data is None, "部分结果不得回流为成功数据"
    # error_mapper 把 timeout 类重分类为可重试码（registry.py:701-710）
    assert result.error is not None and result.error.code == "timeout"
    assert "read timeout" in result.error.message


# ── ③ 缺包路径：结构化 invalid_configuration，提示指向 ddgs ─────────────────


@pytest.mark.asyncio
async def test_web_search_missing_backend_is_invalid_configuration(monkeypatch):
    """新名旧名都不可用：结构化 fail（invalid_configuration），提示装 ddgs。

    变异 M1（只留旧名 import）改不回本条（本条两段都断）——本条钉的是
    ③ 的诚实失败形态 + ④ 安装提示与实际声明的包名一致。
    """
    _block(monkeypatch, "ddgs", "duckduckgo_search")
    monkeypatch.setattr(ws_mod, "_check_rate_limit", AsyncMock(return_value=True))

    result = await tool_registry.execute_detailed("web_search", {"query": "q"}, user_id=1)

    assert result.success is False, result.to_dict()
    assert result.error is not None and result.error.code == "invalid_configuration"
    assert "ddgs" in result.error.message, "安装提示必须指向 requirements 实际声明的 ddgs"


# ── ④ 旧名兜底：ddgs 不可用时旧名仍可用 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_web_search_legacy_name_import_fallback(monkeypatch):
    """ddgs 不可用、duckduckgo_search 可用 → 走兜底段，工具仍可用。

    变异 M4（删兜底 import）→ ImportError → invalid_configuration → 本条红。
    """
    _block(monkeypatch, "ddgs")
    calls = _install_fake_backend(monkeypatch, results=_SAMPLE[:1], names=("duckduckgo_search",))

    result = await tool_registry.execute_detailed("web_search", {"query": "q"}, user_id=1)

    assert result.success is True, result.to_dict()
    assert f"[1] {_SAMPLE[0]['title']}" in result.data
    assert calls and calls[0]["query"] == "q"
