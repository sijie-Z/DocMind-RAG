"""索引重建安全性回归（issue #184）。

## 背景

`app/core/elasticsearch.py` 的 `_create_index_if_not_exists` 原先在**向量维度不匹配时
自动重建索引**，而它挂在**每次文档处理都会走到**的路径上（`app/worker/doc_processor.py:226`）。

后果：**改一次 `VECTOR_DIMENSION` 就静默清空整个知识库** —— 没有二次确认、没有备份、
没有任何"即将删除 N 篇文档"的提示。而「换 embedding 模型」正是维度不匹配最常见的来源，
也就是说：**换一次模型 = 清空知识库**。

## 本文件钉住的不变式（需要真实 Elasticsearch，`RUN_INTEGRATION=1` 门控）

1. **维度不匹配时，索引与其中的数据都不得被删** —— 函数必须抛 `IndexConfigError`，
   且调用之后索引仍然存在、文档数一篇不少。这是本 issue 的核心。
2. `recreate_knowledge_index()` 不显式传 `confirm=True` 时**拒绝执行**。
3. `initialize()` 遇到索引配置错误时**保留 ES 客户端** ——
   否则"维度配错了"会表现为"ES 连不上"，把排查方向带偏。

## 变异验证（证明用例 1 有鉴别力）

把 `_create_index_if_not_exists` 里的 `raise IndexConfigError(...)` 改回
`await recreate_knowledge_index(confirm=True)`，用例 1 必须变红：
索引会被删除重建，文档数归零、`dims` 变成配置值，两条断言同时失败。

用**独立索引名**（`docmind_test_idx_<uuid>`）跑，绝不碰真实索引。
"""
import os
import uuid

import pytest
import pytest_asyncio

from app.core.config import settings
from app.core.elasticsearch import (
    IndexConfigError,
    _holder,
    create_index_if_not_exists,
    get_elasticsearch,
    recreate_knowledge_index,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 Elasticsearch（设置 RUN_INTEGRATION=1，并确保 ES 可达）",
)


def _es_reachable() -> bool:
    """CI 的 integration job **只提供 MySQL + Redis**（job 名字里就写着），没有 ES。

    没有这个守卫，本文件会在 CI 上全部 **ERROR** 而不是 SKIP —— 而 ERROR 是红的。
    `RUN_INTEGRATION` 门只说明"允许跑集成测试"，不等于"ES 一定在"。
    """
    import socket
    from urllib.parse import urlparse

    for host in settings.ELASTICSEARCH_HOSTS:
        u = urlparse(host)
        try:
            with socket.create_connection((u.hostname, u.port or 9200), timeout=2):
                return True
        except OSError:
            continue
    return False


if not _es_reachable():
    pytestmark = [
        pytestmark,
        pytest.mark.skip(reason="Elasticsearch 不可达（CI 的 integration job 不提供 ES）"),
    ]

# 与配置**故意不同**的维度，用来触发不匹配
_WRONG_DIMS = 17
_SEED_DOCS = 3


@pytest_asyncio.fixture
async def isolated_index(monkeypatch):
    """建一个维度故意不匹配、且带种子的独立索引；用完删掉。

    **每个用例都重建 ES 客户端**：pytest-asyncio 每用例一个新事件循环，而
    `AsyncElasticsearch` 把连接绑在创建它的那个 loop 上，跨 loop 复用会抛
    `RuntimeError: Event loop is closed`。DB 侧靠 `NullPool` 规避同一问题
    （见 `test_delete_document_children.py:53`），ES 没有池配置，只能在当前 loop 里重连。

    这里直接丢弃旧引用而不调 `close()` —— 旧的 loop 已经关闭，`close()` 自己就会抛。
    """
    monkeypatch.setattr(_holder, "_client", None)

    index_name = f"docmind_test_idx_{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(settings, "ELASTICSEARCH_INDEX_NAME", index_name)

    # 分两步制造「维度不匹配」：
    #   ① 先让配置等于我们要建的那个维度 —— `get_elasticsearch()` 会触发
    #      `_holder.initialize()`，而 initialize 内部本身就会调
    #      `create_index_if_not_exists` 把索引建出来（所以不能再显式 create，会 400）。
    #   ② 索引建好后再把配置改掉，此时「索引里的维度 ≠ 配置维度」。
    monkeypatch.setattr(settings, "VECTOR_DIMENSION", _WRONG_DIMS)
    client = await get_elasticsearch()
    assert await client.indices.exists(index=index_name), "initialize 应当已经建好索引"

    monkeypatch.setattr(settings, "VECTOR_DIMENSION", _WRONG_DIMS + 1)

    for i in range(_SEED_DOCS):
        await client.index(
            index=index_name,
            id=f"seed-{i}",
            document={"embedding": [0.1] * _WRONG_DIMS, "document_id": "doc-1"},
            refresh=True,
        )

    try:
        yield index_name, client
    finally:
        if await client.indices.exists(index=index_name):
            await client.indices.delete(index=index_name)


# ══════════════════════════════════════════════════════════════════════
# 不变式 1（核心）：维度不匹配时，索引与数据都不得被删
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_dimension_mismatch_raises_and_preserves_index_and_data(isolated_index):
    index_name, client = isolated_index
    before = (await client.count(index=index_name))["count"]
    assert before == _SEED_DOCS, f"夹具自身有问题：种子里应只有 {_SEED_DOCS} 篇，实际 {before}"

    with pytest.raises(IndexConfigError) as ei:
        await create_index_if_not_exists()

    # 报错信息必须能定位问题，且说清「不会自动重建」
    msg = str(ei.value)
    assert "不自动重建" in msg or "不会自动重建" in msg, f"错误信息没说清不会重建：{msg}"
    assert str(_WRONG_DIMS) in msg, "错误信息应带上索引里实际的维度"
    assert str(_SEED_DOCS) in msg, "错误信息应带上将要失去的文档数"

    # ★ 两条核心断言：索引还在，数据一篇不少
    assert await client.indices.exists(index=index_name), "索引被删了 —— 这正是 issue #184"
    after = (await client.count(index=index_name))["count"]
    assert after == _SEED_DOCS, f"数据丢了：调前 {_SEED_DOCS} 篇，调后 {after} 篇"

    # 维度也没被偷偷改掉
    mapping = await client.indices.get_mapping(index=index_name)
    assert mapping[index_name]["mappings"]["properties"]["embedding"]["dims"] == _WRONG_DIMS


@pytest.mark.asyncio
async def test_matching_dims_is_a_noop(isolated_index, monkeypatch):
    """维度一致时什么都不该发生（不抛错、不重建、数据不动）。"""
    index_name, client = isolated_index
    monkeypatch.setattr(settings, "VECTOR_DIMENSION", _WRONG_DIMS)

    await create_index_if_not_exists()  # 不抛

    assert await client.indices.exists(index=index_name)
    assert (await client.count(index=index_name))["count"] == _SEED_DOCS


# ══════════════════════════════════════════════════════════════════════
# 不变式 2：recreate_knowledge_index 必须显式确认
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_recreate_without_confirm_refuses_and_keeps_data(isolated_index):
    index_name, client = isolated_index

    result = await recreate_knowledge_index()  # 不传 confirm

    assert result is False, "未确认时必须拒绝执行"
    assert await client.indices.exists(index=index_name), "未确认却动了索引"
    assert (await client.count(index=index_name))["count"] == _SEED_DOCS, "未确认却丢了数据"


@pytest.mark.asyncio
async def test_recreate_with_confirm_actually_recreates(isolated_index):
    """显式确认后应当真的重建 —— 并且维度变成配置值、数据清空。"""
    index_name, client = isolated_index

    assert await recreate_knowledge_index(confirm=True) is True

    assert await client.indices.exists(index=index_name)
    mapping = await client.indices.get_mapping(index=index_name)
    assert (
        mapping[index_name]["mappings"]["properties"]["embedding"]["dims"]
        == _WRONG_DIMS + 1
    ), "重建后维度应等于配置值"
    assert (await client.count(index=index_name))["count"] == 0, "重建后不该有旧数据残留"


# ══════════════════════════════════════════════════════════════════════
# 不变式 3：配置错误不能把整个 ES 客户端搞没
# ══════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_initialize_keeps_client_when_index_config_is_wrong(isolated_index, monkeypatch):
    """索引配置不一致时，客户端必须保留、问题必须被记录。

    否则「维度配错了」会表现成「ES 连不上」，排查方向直接被带偏 ——
    这正是修复前 `except Exception` 兜底会造成的结果。
    """
    index_name, _ = isolated_index

    # 模拟进程重启后的首次 initialize：先清空 holder，再让它自己连一次
    monkeypatch.setattr(_holder, "_client", None)
    monkeypatch.setattr(_holder, "index_error", None)

    await _holder.initialize()

    assert _holder.client is not None, "配置问题不该让整个 ES 客户端消失"
    assert _holder.index_error, "索引配置错误必须被记录（供 /health 与文档处理路径读取）"
    assert str(_WRONG_DIMS) in _holder.index_error

    # 清掉，避免污染同进程内的其他用例
    _holder.index_error = None
