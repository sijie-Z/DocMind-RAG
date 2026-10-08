"""CI CodeQL 收口：ExecutionContext 本地 replay 落盘的路径安全化（CWE-22）。

task_id 是不可信输入（URL/快照）。此前 `Path("benchmark/replay") / f"{task_id}.json"`
裸拼——穿越字符（../、绝对路径、反斜杠）可把落点带出基准目录（CodeQL
py/path-injection ×2 HIGH）。现在：非法 task_id **一律拒绝、不落盘**；合法 id 的
落点被约束在 benchmark/replay/ 内；save_local() 对非法 id 显式抛 ValueError。

变异点：把 `_replay_file_for` 换回裸拼 → 非法用例的 `ok is False`/零落盘断言红。
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
    "a" * 100,   # 超长
    "a:b",       # Windows 盘符/ADS
    "a b",       # 空格
]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_id", _UNSAFE_IDS)
async def test_save_rejects_unsafe_task_id_without_writing_anything(tmp_path, monkeypatch, bad_id):
    monkeypatch.chdir(tmp_path)
    ctx = ExecutionContext(query="q")
    ctx.task_id = bad_id

    ok = await ctx.save()

    assert ok is False, f"非法 task_id 必须拒绝本地落盘：{bad_id!r}"
    written = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert written == [], f"拒绝路径不得产生任何文件：{written}"


@pytest.mark.asyncio
async def test_save_writes_inside_replay_dir_for_safe_task_id(tmp_path, monkeypatch):
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
    monkeypatch.chdir(tmp_path)
    ctx = ExecutionContext(query="q")
    ctx.task_id = "../evil"

    with pytest.raises(ValueError):
        await ctx.save_local()
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]
