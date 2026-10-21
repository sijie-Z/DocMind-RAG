"""回放消费面（后端 A）：引擎级契约 —— 结构化出口 / 精确匹配 / 读侧路径安全 / has_replay。

背景：回放数据由 loop 收尾时 `ExecutionContext.save()` 真写（Redis
`agent:replay:{task_id}`，7 天；本地 `benchmark/replay/` 兜底），但此前只有 CLI
消费。本组用例钉住引擎新增的消费面契约：

- `format_timeline`：字段与写侧 `ExecutionContext.to_dict()` 逐字对齐（改名即红），
  坏行丢弃、不编造；
- `load(exact=True)`：HTTP 面只认精确 task_id——前缀命中到**另一个任务**的文件
  必须为 None（否则按 task_id 的归属校验会被前缀旁路）；
- 读侧白名单：路径穿越/非法 task_id 一律 None（与写侧 `_replay_file_for` 同防线）；
- `has_replay`：Redis → 本地文件 → False 三态；损坏文件按无数据降级。

变异点（见每条用例）：
- M1 无数据时编造空轨迹（如 load 返回 {}）→ test_no_data_* 红；
- M3 format_timeline 字段改名（如 tokens_used→tokens）→ test_field_alignment 红；
- 读侧去掉白名单（还原裸拼）→ test_load_rejects_unsafe_task_id 红。
"""

import json

import pytest

from app.agent.exec_context import ExecutionContext
from app.agent.replay.engine import ReplayEngine

_TASK_ID = "aaaabbbbcccc"


class _FakeRedis:
    """最小 Redis 替身：get/exists（回放读取链只消费这两个）。"""

    def __init__(self, store: dict[str, str] | None = None):
        self.store = store or {}

    def __bool__(self):
        return True

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0


@pytest.fixture
def replay_dir(tmp_path, monkeypatch):
    """把引擎的本地兜底目录指到 tmp（写入的 400 份历史文件绝不参与用例）。"""
    import app.agent.replay.engine as engine_mod

    d = tmp_path / "replay"
    monkeypatch.setattr(engine_mod, "REPLAY_DIR", d)
    return d


@pytest.fixture
def no_redis(monkeypatch):
    monkeypatch.setattr("app.core.redis.redis_client", None)


def _sample_ctx_dict(task_id: str = _TASK_ID) -> dict:
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
    ctx.add_finding("s1", "关键事实", tool="search_knowledge_base")
    ctx.record_decision("execute", "retry_step", "第一次超时")
    ctx.total_tokens = 42
    ctx.mark_done()
    return ctx.to_dict()


# ── 结构化出口：字段对齐 / 诚实投影 ──────────────────────────────────────────


def test_format_timeline_fields_align_with_write_side():
    """字段与写侧 to_dict() 逐字对齐（顶/步/发现/决策四层键集全等）。

    变异点 M3：把任一步骤键改名（如 tokens_used→tokens）或改口径 → 本用例红。
    """
    engine = ReplayEngine()
    d = _sample_ctx_dict()
    tl = engine.format_timeline(d)

    assert set(tl) == set(d), "顶字段必须与写侧键集全等（禁止改名/漏键/加键）"
    assert [list(s) for s in tl["steps"]] == [list(s) for s in d["steps"]], (
        "步骤子键顺序与键集必须与写侧一致"
    )
    assert [list(f) for f in tl["findings"]] == [list(f) for f in d["findings"]], (
        "发现子键顺序与键集必须与写侧一致"
    )
    assert [list(x) for x in tl["decisions"]] == [list(x) for x in d["decisions"]], (
        "决策子键顺序与键集必须与写侧一致（#204 起含 ts）"
    )
    assert tl["steps"][0]["tokens_used"] == 42, "tokens 逐字透传"
    assert tl["steps"][0]["duration_ms"] == 125.5
    assert tl["steps"][0]["started_at"] == d["steps"][0]["started_at"], (
        "步骤墙钟锚点逐字透传（#204）"
    )
    assert tl["steps"][0]["finished_at"] == d["steps"][0]["finished_at"]
    assert tl["steps"][1]["status"] == "failed" and tl["steps"][1]["error"] == "boom"
    assert tl["findings"][0]["source_step"] == "s1"
    assert tl["decisions"][0]["action"] == "retry_step"
    assert tl["decisions"][0]["ts"] == d["decisions"][0]["ts"] > 0, "决策墙钟时间戳逐字透传（#204）"
    assert tl["failures"], "失败的步骤必须在 failures 里留痕（run 级失败列表）"
    # 失败轨迹在顶层 failures 中有记录（写侧 complete_step 的既有语义）
    assert any("boom" in f for f in tl["failures"])


def test_format_timeline_drops_malformed_rows_without_fabricating():
    """坏行（非 dict）丢弃；缺键给 get 默认——宁可少，绝不编造轨迹数据。"""
    engine = ReplayEngine()
    tl = engine.format_timeline(
        {
            "task_id": "t1",
            "steps": [{"step_id": "s1", "description": "ok", "status": "completed"}, "junk", 42],
            "findings": "not-a-list",
            "decisions": None,
            "failures": [123],
        }
    )
    assert len(tl["steps"]) == 1, "非 dict 坏行必须丢弃"
    assert tl["steps"][0]["step_id"] == "s1"
    assert tl["steps"][0]["tokens_used"] is None, "缺键 → None（不虚构 0/占位）"
    assert tl["steps"][0]["started_at"] is None and tl["steps"][0]["finished_at"] is None, (
        "旧回放缺时间戳 → None（#204：不得补 0.0 冒充 1970 真实时间）"
    )
    assert tl["findings"] == [] and tl["decisions"] == []
    assert tl["steps_completed"] == 1, "steps_completed 缺失时按实际步数回退（派生而非虚构）"


def test_format_timeline_empty_ctx_is_honest_empty_projection():
    """空 dict → 空投影（不炸、不虚构）；是否有回放由 404 语义在 API 层判定。"""
    engine = ReplayEngine()
    tl = engine.format_timeline({})
    assert tl["task_id"] == "" and tl["steps"] == []
    assert tl["steps_completed"] == 0 and tl["total_tokens"] == 0


# ── 读取链：Redis → 本地精确/前缀 → None ─────────────────────────────────────


@pytest.mark.asyncio
async def test_load_redis_hit_wins(replay_dir, monkeypatch):
    """Redis 命中优先于本地文件。"""
    fake = _FakeRedis({f"agent:replay:{_TASK_ID}": json.dumps({"task_id": _TASK_ID, "steps": []})})
    monkeypatch.setattr("app.core.redis.redis_client", fake)
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}.json").write_text(json.dumps({"task_id": "local"}), encoding="utf-8")

    ctx = await ReplayEngine().load(_TASK_ID)
    assert ctx == {"task_id": _TASK_ID, "steps": []}, "Redis 命中必须优先（7 天热数据）"


@pytest.mark.asyncio
async def test_load_local_fallback_when_redis_empty(replay_dir, no_redis):
    """Redis 无数据 → 本地 benchmark/replay/ 兜底。"""
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}.json").write_text(
        json.dumps({"task_id": _TASK_ID, "query": "q", "steps": []}), encoding="utf-8"
    )
    ctx = await ReplayEngine().load(_TASK_ID)
    assert ctx is not None and ctx["task_id"] == _TASK_ID


@pytest.mark.asyncio
async def test_load_exact_never_prefix_matches_other_task(replay_dir, no_redis):
    """exact=True（HTTP 消费面）：绝不前缀命中到**另一个任务**的文件。

    变异点：HTTP 面传 exact=False → 本用例红（拿到的是他任务回放）。
    """
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}abcd.json").write_text(
        json.dumps({"task_id": _TASK_ID + "abcd", "steps": []}), encoding="utf-8"
    )
    engine = ReplayEngine()
    assert await engine.load(_TASK_ID, exact=True) is None, "精确模式不得前缀命中"
    hit = await engine.load(_TASK_ID)
    assert hit is not None and hit["task_id"] == _TASK_ID + "abcd", (
        "默认模式保留 CLI 的前缀便利语义（本次改动不得破坏）"
    )


@pytest.mark.asyncio
async def test_load_rejects_unsafe_task_id(replay_dir, no_redis, tmp_path):
    """读侧白名单：路径穿越/非法 task_id 一律 None（与写侧同防线）。

    变异点：去掉 _SAFE_TASK_ID 校验（还原裸拼）→ 本用例红（读到基准目录外的文件）。
    """
    replay_dir.mkdir(parents=True, exist_ok=True)
    # 基准目录外的"机密"文件：若读侧裸拼，'../secret' 会命中它。
    (tmp_path / "secret.json").write_text(json.dumps({"task_id": "secret"}), encoding="utf-8")
    engine = ReplayEngine()
    for bad in ("..", "../secret", "a/b", "a\\b", "", "x" * 100):
        assert await engine.load(bad) is None, f"非法 task_id {bad!r} 必须拒绝"
        assert await engine.has_replay(bad) is False


@pytest.mark.asyncio
async def test_load_corrupted_local_file_degrades_to_none(replay_dir, no_redis):
    """截断/半写 JSON 按"无回放数据"降级，绝不把坏文件炸成异常。"""
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}.json").write_text('{"task_id": "trunc', encoding="utf-8")
    assert await ReplayEngine().load(_TASK_ID, exact=True) is None


# ── has_replay：Redis → 本地 → False 三态 ────────────────────────────────────


@pytest.mark.asyncio
async def test_has_replay_three_states(replay_dir, monkeypatch):
    engine = ReplayEngine()
    # ① 全无 → False（诚实：不虚标）
    monkeypatch.setattr("app.core.redis.redis_client", None)
    assert await engine.has_replay(_TASK_ID) is False
    # ② 本地文件存在（可读形状） → True
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}.json").write_text(
        json.dumps({"task_id": _TASK_ID, "steps": []}), encoding="utf-8"
    )
    assert await engine.has_replay(_TASK_ID) is True
    # ③ Redis 命中（本地无文件）→ True
    other = "dddddddddddd"
    monkeypatch.setattr(
        "app.core.redis.redis_client",
        _FakeRedis({f"agent:replay:{other}": json.dumps({"task_id": other, "steps": []})}),
    )
    assert await engine.has_replay(other) is True
    assert await engine.has_replay(_TASK_ID) is True, "Redis 不可用/未命中时回退本地判定"


@pytest.mark.asyncio
async def test_has_replay_does_not_prefix_match(replay_dir, no_redis):
    """has_replay 只认精确文件——前缀命中会把'有回放'标到错的任务上。"""
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}zzz.json").write_text(
        json.dumps({"task_id": _TASK_ID + "zzz", "steps": []}), encoding="utf-8"
    )
    assert await ReplayEngine().has_replay(_TASK_ID) is False


# ── F1/F3：载荷结构校验（畸形 = 诚实降级，绝不 500 裸抛）─────────────────────

# 三种畸形：合法 JSON 但不是 dict / 是字符串 / 是缺必须键的 dict
_MALFORMED_PAYLOADS = [
    ("list", [1, 2, 3]),
    ("string", "not-a-dict"),
    ("missing_keys", {"query": "缺 task_id 与 steps"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("name,payload", _MALFORMED_PAYLOADS)
async def test_load_rejects_malformed_payloads_both_backends(
    replay_dir, monkeypatch, name, payload
):
    """畸形载荷在 Redis 与本地两条链都按无数据降级（F1）。

    变异点 M6：去掉 _is_valid_replay_ctx 校验 → 本用例红（load 返回畸形载荷；
    API 层随之 500 裸抛）。
    """
    engine = ReplayEngine()
    # ① Redis 命中畸形
    monkeypatch.setattr(
        "app.core.redis.redis_client",
        _FakeRedis({f"agent:replay:{_TASK_ID}": json.dumps(payload)}),
    )
    assert await engine.load(_TASK_ID, exact=True) is None, f"{name}: Redis 畸形必须按无数据"
    assert await engine.has_replay(_TASK_ID) is False, f"{name}: has_replay 必须如实标否（F3）"
    # ② Redis 无 → 本地文件畸形
    monkeypatch.setattr("app.core.redis.redis_client", None)
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}.json").write_text(json.dumps(payload), encoding="utf-8")
    assert await engine.load(_TASK_ID, exact=True) is None, f"{name}: 本地畸形必须按无数据"
    assert await engine.has_replay(_TASK_ID) is False


@pytest.mark.asyncio
async def test_corrupt_redis_falls_back_to_valid_local(replay_dir, monkeypatch):
    """Redis 载荷畸形但本地文件可读 → 仍以本地兜底供回放（一层坏不拖垮整链）。"""
    monkeypatch.setattr(
        "app.core.redis.redis_client",
        _FakeRedis({f"agent:replay:{_TASK_ID}": json.dumps([1, 2, 3])}),
    )
    replay_dir.mkdir(parents=True, exist_ok=True)
    (replay_dir / f"{_TASK_ID}.json").write_text(
        json.dumps({"task_id": _TASK_ID, "steps": []}), encoding="utf-8"
    )
    engine = ReplayEngine()
    ctx = await engine.load(_TASK_ID, exact=True)
    assert ctx is not None and ctx["task_id"] == _TASK_ID
    assert await engine.has_replay(_TASK_ID) is True
