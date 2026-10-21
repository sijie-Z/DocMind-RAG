"""回放消费面（后端 A）HTTP 契约：读 API 三态 / 字段对齐 / 任务列表 has_replay。

覆盖（tests/api 惯例：假鉴权 + 替身隔离；列表用例走真实 sqlite 全链）：
- 401：两枚新端点未登录不得读取；
- 404 TASK_NOT_FOUND：任务不存在/非本人/组织不一致——回放读取必须先过与状态
  视图同一个归属闸门（不按裸 task_id 放行）；
- 404 REPLAY_NOT_FOUND：任务存在但无数据——**诚实空态，绝不编造空轨迹**；
- 200 Redis 命中 / 本地兜底：结构化 timeline 字段与写侧 to_dict() 逐字对齐；
- format=text：format_replay 文本出口保留；
- exact 绑定：前缀命中他任务文件不得成为"我的回放"（归属校验不可被前缀旁路）；
- GET /tasks/{id} 带 has_replay；
- GET /tasks 列表：has_replay 布尔 + 本人/严格同组织的归属过滤 + 排序分页。

变异判别：
- M1 无数据时返回编造的空 timeline（200）而非 404 → test_replay_404_* 红；
- M3 timeline 字段改名错位（tokens_used→tokens 等）→ test_replay_redis_* 红；
- 前缀旁路（exact=False）→ test_replay_exact_* 红。
"""

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from app.agent import checkpoint
from app.agent.exec_context import ExecutionContext
from app.models.agent_task import AgentTask

_TASK_ID = "task00000001"


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def auth():
    """登录态替身（依赖注入覆盖）；用完即撤，避免污染同进程其它 api 用例。"""
    from app.core.security import get_current_user
    from app.main import app

    class _User:
        id = 1
        organization_id = 1
        is_active = True

    user = _User()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def replay_dir(tmp_path, monkeypatch):
    import app.agent.replay.engine as engine_mod

    d = tmp_path / "replay"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(engine_mod, "REPLAY_DIR", d)
    return d


@pytest.fixture
def no_redis(monkeypatch):
    monkeypatch.setattr("app.core.redis.redis_client", None)


def _task(status=None):
    from app.models.agent_task import TaskStatus

    return AgentTask(
        id=_TASK_ID,
        user_id=1,
        organization_id=1,
        query="原问题",
        status=status or TaskStatus.RUNNING,
        completed_steps=2,
        total_steps=2,
    )


def _ctx_dict(task_id: str = _TASK_ID) -> dict:
    """一份真实形状的运行轨迹（由写侧 to_dict() 生成，绝不手拼键名）。"""
    ctx = ExecutionContext(query="分析文档", task_id=task_id)
    ctx.goal = "产出结论"
    ctx.plan_summary = "检索 → 汇总"
    ctx.complete_step(
        "s1",
        "检索资料",
        "search_knowledge_base",
        "completed",
        result="找到 3 条",
        duration=125.5,
        tokens=42,
    )
    ctx.complete_step("s2", "汇总输出", "generate_report", "failed", error="boom")
    ctx.total_tokens = 42
    ctx.mark_done()
    ctx.duration_ms = 1234.5  # 钉成确定值（mark_done 的墙钟值随机器抖动）
    return ctx.to_dict()


class _FakeRedis:
    def __init__(self, store=None):
        self.store = store or {}

    def __bool__(self):
        return True

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0


def _mock_task_loaded(monkeypatch):
    async def _load(**kwargs):
        return _task()

    monkeypatch.setattr(checkpoint, "load_task", _load)


# ── 鉴权 / 归属 ──────────────────────────────────────────────────────────────


def test_replay_requires_auth(client):
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay")
    assert r.status_code == 401, "未登录不得读取回放"


def test_tasks_list_requires_auth(client):
    r = client.get("/api/v1/agent/tasks")
    assert r.status_code == 401, "未登录不得读取任务列表"


def test_replay_404_when_task_missing_or_foreign(client, auth, monkeypatch, replay_dir, no_redis):
    """归属闸门先行：任务不存在/非本人/组织不一致 → 404 TASK_NOT_FOUND。"""

    async def _raise(**kwargs):
        raise checkpoint.TaskNotFoundError("任务不存在或无权访问")

    monkeypatch.setattr(checkpoint, "load_task", _raise)
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay")
    assert r.status_code == 404
    assert r.json()["error_code"] == "TASK_NOT_FOUND"


def test_replay_404_for_unsafe_task_id(client, auth, monkeypatch, replay_dir, no_redis):
    """路径穿越 task_id：归属闸门（替身放行）之后读侧白名单仍必须拒绝。

    用 Windows 分隔符穿越（单路径段可达路由；'/' 形式不会匹配到本路由，
    测不到读侧防线）。
    """
    _mock_task_loaded(monkeypatch)
    r = client.get("/api/v1/agent/tasks/..%5C..%5Csecret/replay")
    assert r.status_code == 404
    assert r.json()["error_code"] == "REPLAY_NOT_FOUND"


# ── 读 API 三态 + 字段对齐 ───────────────────────────────────────────────────


def test_replay_redis_hit_returns_structured_timeline(client, auth, monkeypatch, replay_dir):
    """Redis 命中：200 + 结构化 timeline；字段与写侧 to_dict() 逐字对齐。

    变异点 M3：任一字段改名/错位 → 键集断言红。
    """
    _mock_task_loaded(monkeypatch)
    d = _ctx_dict()
    monkeypatch.setattr(
        "app.core.redis.redis_client",
        _FakeRedis({f"agent:replay:{_TASK_ID}": json.dumps(d)}),
    )

    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay")
    assert r.status_code == 200
    data = r.json()["data"]

    assert set(data) == set(d), "顶字段必须与写侧键集全等"
    assert data["task_id"] == _TASK_ID
    assert data["query"] == "分析文档"
    assert data["total_tokens"] == 42
    assert data["duration_ms"] == 1234.5
    assert [list(s) for s in data["steps"]] == [list(s) for s in d["steps"]], (
        "步骤子键顺序与键集必须与写侧一致"
    )
    assert data["steps"][0]["tokens_used"] == 42
    assert data["steps"][0]["duration_ms"] == 125.5
    assert data["steps"][1]["status"] == "failed" and data["steps"][1]["error"] == "boom"
    assert data["failures"], "失败步骤必须在 failures 留痕"


def test_replay_local_fallback_when_redis_down(client, auth, monkeypatch, replay_dir, no_redis):
    """Redis 不可用 → 本地 benchmark/replay/ 兜底（engine 既有链）。"""
    _mock_task_loaded(monkeypatch)
    (replay_dir / f"{_TASK_ID}.json").write_text(json.dumps(_ctx_dict()), encoding="utf-8")

    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay")
    assert r.status_code == 200
    assert r.json()["data"]["task_id"] == _TASK_ID
    assert r.json()["data"]["steps"][0]["description"] == "检索资料"


def test_replay_404_replay_not_found_when_no_data(client, auth, monkeypatch, replay_dir, no_redis):
    """两后端皆无数据 → 404 REPLAY_NOT_FOUND——绝不编造空轨迹冒充"有回放"。

    变异点 M1：无数据时返回 200 空 timeline → 本用例红。
    """
    _mock_task_loaded(monkeypatch)
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay")
    assert r.status_code == 404
    body = r.json()
    assert body["error_code"] == "REPLAY_NOT_FOUND"
    assert not body.get("data"), "404 响应不得夹带编造的轨迹数据"


def test_replay_exact_binding_never_leaks_other_task(
    client, auth, monkeypatch, replay_dir, no_redis
):
    """前缀命中他任务文件 → 仍必须 404：按 task_id 的归属校验不可被前缀旁路。

    变异点：端点 load 传 exact=False → 本用例红（200 拿到他任务回放）。
    """
    _mock_task_loaded(monkeypatch)
    other = _ctx_dict(task_id=_TASK_ID + "abcd")
    (replay_dir / f"{_TASK_ID}abcd.json").write_text(json.dumps(other), encoding="utf-8")

    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay")
    assert r.status_code == 404
    assert r.json()["error_code"] == "REPLAY_NOT_FOUND"


def test_replay_text_format_returns_cli_style(client, auth, monkeypatch, replay_dir):
    """format=text：format_replay 的人类可读文本出口保留。"""
    _mock_task_loaded(monkeypatch)
    monkeypatch.setattr(
        "app.core.redis.redis_client",
        _FakeRedis({f"agent:replay:{_TASK_ID}": json.dumps(_ctx_dict())}),
    )
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay", params={"format": "text"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["format"] == "text"
    assert "Execution Replay" in data["text"] and "检索资料" in data["text"]
    assert "Tool: search_knowledge_base" in data["text"]


def test_replay_malformed_redis_payload_is_honest_404_not_500(
    client, auth, monkeypatch, replay_dir, no_redis
):
    """F1 判别：Redis 值合法 JSON 但形状畸形（list/字符串/缺键 dict）→ 诚实 404，
    绝不 500 裸抛（修复前 format_timeline 在 list 上 `.get` 直接炸 500）。

    变异点 M6：去掉引擎 _is_valid_replay_ctx 校验 → 本用例红（500）。
    """
    _mock_task_loaded(monkeypatch)
    malformed = [[1, 2, 3], "not-a-dict", {"query": "缺 task_id 与 steps"}]
    for payload in malformed:
        monkeypatch.setattr(
            "app.core.redis.redis_client",
            _FakeRedis({f"agent:replay:{_TASK_ID}": json.dumps(payload)}),
        )
        r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}/replay")
        assert r.status_code == 404, f"{payload!r} 必须诚实 404，实际 {r.status_code}"
        assert r.json()["error_code"] == "REPLAY_NOT_FOUND"


def test_get_task_has_replay_false_for_malformed_payload(client, auth, monkeypatch, replay_dir):
    """F3 判别：损坏内容不得标 has_replay=True（损坏 = 如实视为无回放）。"""
    _mock_task_loaded(monkeypatch)
    monkeypatch.setattr(
        "app.core.redis.redis_client",
        _FakeRedis({f"agent:replay:{_TASK_ID}": json.dumps([1, 2, 3])}),
    )
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}")
    assert r.status_code == 200
    assert r.json()["data"]["has_replay"] is False


# ── 状态视图 / 列表 has_replay ───────────────────────────────────────────────


def test_get_task_view_includes_has_replay(client, auth, monkeypatch, replay_dir, no_redis):
    """GET /tasks/{id} 补 has_replay：有文件 True / 无 False（不虚标）。"""
    _mock_task_loaded(monkeypatch)
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}")
    assert r.json()["data"]["has_replay"] is False

    (replay_dir / f"{_TASK_ID}.json").write_text(json.dumps(_ctx_dict()), encoding="utf-8")
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}")
    assert r.json()["data"]["has_replay"] is True


@pytest.mark.asyncio
async def test_tasks_list_has_replay_flags_and_scope(tmp_path, monkeypatch, replay_dir):
    """列表全链（真实 sqlite + 真实 checkpoint）：has_replay 布尔 + 归属过滤 + 排序。

    变异点：has_replay 恒 True/恒 False、去掉 user/org 过滤 → 对应断言红。
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 — 注册全部表，供 create_all
    from app.core.database import Base
    from app.core.security import get_current_user
    from app.main import app

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tlist.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    monkeypatch.setattr("app.core.redis.redis_client", None)

    t_a = await checkpoint.create_task(user_id=1, organization_id=1, query="任务A")
    t_b = await checkpoint.create_task(user_id=1, organization_id=1, query="任务B")
    t_other_user = await checkpoint.create_task(user_id=2, organization_id=1, query="他人任务")
    t_other_org = await checkpoint.create_task(user_id=1, organization_id=2, query="他组织任务")
    async with factory() as db:
        for tid, ts in ((t_a, datetime(2026, 1, 1, 0, 0, 0)), (t_b, datetime(2026, 1, 2, 0, 0, 0))):
            row = await db.get(AgentTask, tid)
            row.created_at = ts
        await db.commit()

    class _User:
        id = 1
        organization_id = 1
        is_active = True

    app.dependency_overrides[get_current_user] = lambda: _User()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://tlist") as c:
            # 无回放数据时：全部 False
            r = await c.get("/api/v1/agent/tasks")
            assert r.status_code == 200
            body = r.json()["data"]
            assert body["total"] == 2 and body["page"] == 1 and body["page_size"] == 20
            ids = [t["task_id"] for t in body["tasks"]]
            assert ids == [t_b, t_a], "本人 + 严格同组织，按创建时间倒序"
            assert all(t["has_replay"] is False for t in body["tasks"]), "无回放不虚标"
            assert t_other_user not in ids and t_other_org not in ids

            # 给 B 落一份本地回放 → 只有 B 标 True
            (replay_dir / f"{t_b}.json").write_text(json.dumps(_ctx_dict(t_b)), encoding="utf-8")
            body = (await c.get("/api/v1/agent/tasks")).json()["data"]
            flags = {t["task_id"]: t["has_replay"] for t in body["tasks"]}
            assert flags == {t_b: True, t_a: False}
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    await engine.dispose()
