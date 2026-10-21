"""CI 收口（复核轮）：ExecutionContext 本地 replay 落盘的路径安全化（CWE-22）。

语义（双后端，显式钉住）：
- **路径防线只覆盖本地回退分支**：Redis 键没有路径语义，`save()` 在 Redis 可用时
  落 Redis、不触磁盘——非法 task_id 也照常保存（返回 True，可接受；专门用例钉住）；
- 本地回退分支：非法 task_id 一律拒绝（`save`→False / `save_local`→ValueError），
  合法 id 的落点被约束在 benchmark/replay/ 内。

密封性（复核教训）：用例**显式 stub `app.core.redis.redis_client`**，让"走不走
Redis"由用例决定而不是运行环境——CI 集成 job 里真有 Redis，此前写法被环境翻转
（setex 成功提前 return True，本地分支从不执行）。本文件在"无 Redis"与"假可用
Redis"两种环境下都应全绿（证据：_scratch_r201p2/_redis_avail_plugin.py）。

变异点：
- 去掉 `force_local_fallback` 种子（在假可用 Redis 环境）→ 本地分支用例红；
- 把 `_replay_file_for` 校验挪走（还原裸拼）→ 本地分支用例红。
"""

import pytest

from app.agent.exec_context import ExecutionContext

_UNSAFE_IDS = [
    "../evil",
    "../../evil",
    "sub/dir",
    "/abs/evil",
    "a\\b",
    "..",
    ".",
    "",
    "a" * 100,  # 超长
    "a:b",  # Windows 盘符/ADS
    "a b",  # 空格
]


class _UnavailableRedis:
    """`if redis_client:` 为假的替身——强制 save() 走本地回退分支。"""

    def __bool__(self):
        return False


class _AvailableRedis:
    """可用的最小替身（记录 setex 的键）；供"Redis 可用"语义用例使用。"""

    def __bool__(self):
        return True

    def __init__(self):
        self.keys: dict[str, str] = {}

    async def setex(self, key, ttl, value):
        self.keys[key] = value
        return True


@pytest.fixture
def force_local_fallback(monkeypatch):
    """照抄仓内既有做法（tests/unit/test_metrics_redis.py）：字符串目标 patch
    `app.core.redis.redis_client`——exec_context 在函数内 `from app.core.redis
    import redis_client`，每次调用都取模块属性，因此必然看到替身、与真实环境无关。"""
    monkeypatch.setattr("app.core.redis.redis_client", _UnavailableRedis())


@pytest.fixture
def force_redis_available(monkeypatch):
    fake = _AvailableRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)
    return fake


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", _UNSAFE_IDS)
async def test_local_fallback_rejects_unsafe_task_id_without_writing_anything(
    tmp_path,
    monkeypatch,
    force_local_fallback,
    bad_id,
):
    monkeypatch.chdir(tmp_path)
    ctx = ExecutionContext(query="q")
    ctx.task_id = bad_id

    ok = await ctx.save()

    assert ok is False, f"本地回退分支必须拒绝非法 task_id：{bad_id!r}"
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [], f"拒绝路径不得产生任何文件：{written}"


@pytest.mark.asyncio
async def test_local_fallback_writes_inside_replay_dir_for_safe_task_id(
    tmp_path,
    monkeypatch,
    force_local_fallback,
):
    monkeypatch.chdir(tmp_path)
    ctx = ExecutionContext(query="q")
    ctx.task_id = "abc123def456"

    ok = await ctx.save()

    assert ok is True
    target = tmp_path / "benchmark" / "replay" / "abc123def456.json"
    assert target.is_file(), "合法 task_id 的落点必须被约束在基准目录内"
    assert target.parent.parent == tmp_path / "benchmark"


@pytest.mark.asyncio
async def test_save_local_default_path_rejects_unsafe_task_id(tmp_path, monkeypatch):
    """save_local() 与 Redis 无关：默认路径一律经 _replay_file_for 校验。"""
    monkeypatch.chdir(tmp_path)
    ctx = ExecutionContext(query="q")
    ctx.task_id = "../evil"

    with pytest.raises(ValueError):
        await ctx.save_local()
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


@pytest.mark.asyncio
async def test_save_with_redis_available_never_touches_disk(
    tmp_path,
    monkeypatch,
    force_redis_available,
):
    """语义钉住（双后端自洽）：Redis 可用时 save() 走 Redis——非法 task_id 也返回
    True（键无路径语义）且不触磁盘；合法 id 同样不落盘。stub 让该用例在真实有
    Redis 的 CI 环境里同样成立。"""
    monkeypatch.chdir(tmp_path)

    ctx_bad = ExecutionContext(query="q")
    ctx_bad.task_id = "../evil"
    assert await ctx_bad.save() is True
    assert any(k.startswith("agent:replay:") for k in force_redis_available.keys)

    ctx_ok = ExecutionContext(query="q")
    ctx_ok.task_id = "abc123"
    assert await ctx_ok.save() is True

    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [], "Redis 分支不得触磁盘（路径防线只属于本地回退分支）"
