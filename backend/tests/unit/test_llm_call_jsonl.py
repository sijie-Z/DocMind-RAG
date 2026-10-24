"""issue #510 第一步/第二步验收：LLM 调用级 JSONL + ID 语义。

口径（issue 验收）：

- 一次 LLM 调用 → `llm_calls.jsonl` 恰好一行，字段固定为
  ts/task_id/request_id/model/tokens_in/tokens_out/latency_ms/prompt_hash/
  tool_calls/error；
- **不含 prompt/completion 正文**（prompt 只留 sha256 摘要）；
- task_id = 一次 Agent run（与 ExecutionContext.task_id / SSE / RunReport /
  agent_tasks.id 同源）；request_id = 一次 HTTP 请求（与 X-Request-ID 同源）；
- 观测写入失败绝不影响 run（返回 False，不抛异常）。
"""

import asyncio
import hashlib
import json
import re
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.llm_call_log import hash_prompt
from app.agent.run_report import build_run_report
from app.agent.usage import record_llm_call_error, record_llm_usage
from app.core.config import settings

REQUIRED_FIELDS = {
    "ts",
    "task_id",
    "request_id",
    "model",
    "tokens_in",
    "tokens_out",
    "latency_ms",
    "prompt_hash",
    "tool_calls",
    "error",
}

PROMPT_SECRET = "PROMPT_BODY_SHOULD_NEVER_BE_LOGGED"
COMPLETION_SECRET = "COMPLETION_BODY_SHOULD_NEVER_BE_LOGGED"


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": PROMPT_SECRET},
    ]


def _response(
    *,
    model: str = "deepseek-test",
    prompt_tokens: int = 120,
    completion_tokens: int = 30,
    tool_calls: list | None = None,
    content: str = COMPLETION_SECRET,
):
    return SimpleNamespace(
        model=model,
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
    )


def _read_records(path) -> list[dict]:
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _chunk(content=None, usage=None, model=None):
    c = MagicMock()
    if content is None:
        c.choices = []
    else:
        c.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]
    c.usage = usage
    c.model = model
    return c


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        self._it = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None


@pytest.fixture()
def log_path(tmp_path, monkeypatch):
    """默认单文件落点指向 tmp；开关显式置真（不依赖外部 env）。"""
    path = tmp_path / "llm_calls.jsonl"
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(path))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)
    return path


# ── 1. 一次调用一行 + 字段齐全 + 不含正文 ─────────────────────────────────


def test_single_llm_call_writes_one_line_with_required_fields(log_path):
    ctx = ExecutionContext(query="q", task_id="task-510", request_id="req-510")
    resp = _response(tool_calls=[SimpleNamespace(id="call-1"), SimpleNamespace(id="call-2")])

    assert record_llm_usage(resp, ctx, latency_ms=123.46, messages=_messages()) == 150
    assert ctx.total_tokens == 150, "既有 total_tokens 记账语义不得被 JSONL 改动"

    records = _read_records(log_path)
    assert len(records) == 1, "一次 LLM 调用必须恰好一行（追加写）"
    rec = records[0]
    assert set(rec) == REQUIRED_FIELDS, f"字段集必须与规格一致，实际 {sorted(rec)}"
    assert rec["task_id"] == "task-510"
    assert rec["request_id"] == "req-510"
    assert rec["model"] == "deepseek-test"
    assert rec["tokens_in"] == 120
    assert rec["tokens_out"] == 30
    assert rec["latency_ms"] == 123.5
    assert rec["prompt_hash"] == hash_prompt(_messages())
    assert rec["tool_calls"] == 2
    assert rec["error"] is None
    datetime.fromisoformat(rec["ts"])  # 机器可解析的 ISO-8601

    raw = log_path.read_text(encoding="utf-8")
    assert PROMPT_SECRET not in raw, "prompt 正文绝不能落盘"
    assert COMPLETION_SECRET not in raw, "completion 正文绝不能落盘"
    assert PROMPT_SECRET not in rec["prompt_hash"]


def test_second_call_appends_rather_than_overwrites(log_path):
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    record_llm_usage(_response(), ctx, latency_ms=1.0, messages=_messages())
    record_llm_usage(_response(prompt_tokens=5, completion_tokens=7), ctx, messages=_messages())
    records = _read_records(log_path)
    assert len(records) == 2
    assert [r["tokens_in"] for r in records] == [120, 5]


def test_prompt_hash_is_deterministic_and_order_insensitive():
    a = [{"role": "user", "content": PROMPT_SECRET, "name": "x"}]
    b = [{"name": "x", "content": PROMPT_SECRET, "role": "user"}]
    assert hash_prompt(a) == hash_prompt(b), "规范 JSON（键排序）保证同一提示词同一摘要"
    assert hash_prompt(a).startswith("sha256:")
    assert PROMPT_SECRET not in hash_prompt(a)
    assert hash_prompt(None) is None


# ── 2. ID 语义：task_id / request_id 三处可关联，不做假的统一 trace ────────


def test_id_semantics_correlate_jsonl_run_report_and_sse(log_path):
    ctx = ExecutionContext(query="q", task_id="task-abc", request_id="req-xyz")
    record_llm_usage(_response(), ctx, latency_ms=1.0, messages=_messages())

    rec = _read_records(log_path)[0]
    assert (rec["task_id"], rec["request_id"]) == ("task-abc", "req-xyz")

    report = build_run_report(ctx, "out")
    assert report["task_id"] == "task-abc", "RunReport 与 JSONL 的 task_id 必须同源"
    assert report["request_id"] == "req-xyz"

    sse = AgentEvent(
        type="done", content="", task_id="task-abc", request_id="req-xyz"
    ).to_sse_dict()
    assert sse["task_id"] == "task-abc", "SSE 与 JSONL 的 task_id 必须同源"
    assert sse["request_id"] == "req-xyz", "SSE 与 JSONL 的 request_id 必须同源"


def test_request_id_is_per_request_and_not_persisted_in_checkpoint():
    ctx = ExecutionContext(query="q", task_id="task-1", request_id="req-1")
    snapshot = ctx.to_checkpoint_dict()
    assert "request_id" not in snapshot, "request_id 属于请求，不属于 run 快照"

    restored = ExecutionContext.from_checkpoint_dict(snapshot)
    assert restored.task_id == "task-1", "task_id 随快照恢复（一次 run 只有它）"
    assert restored.request_id is None, "resume 请求必须使用自己的 request_id"


def test_events_without_request_id_keep_legacy_payload():
    sse = AgentEvent(type="chunk", content="hi", task_id="t").to_sse_dict()
    assert "request_id" not in sse, "无请求上下文时既有 SSE 载荷零变化"


# ── 3. 失败调用 / 开关 / 容错 ───────────────────────────────────────────────


def test_failed_call_writes_error_line_without_tokens(log_path):
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    record_llm_call_error(
        ctx,
        model="deepseek-test",
        error=RuntimeError("provider boom"),
        latency_ms=9.9,
        messages=_messages(),
    )
    rec = _read_records(log_path)[0]
    assert set(rec) == REQUIRED_FIELDS
    assert rec["error"] == "provider boom"
    assert (rec["tokens_in"], rec["tokens_out"]) == (0, 0)
    assert rec["prompt_hash"] == hash_prompt(_messages())
    assert rec["latency_ms"] == 9.9
    assert PROMPT_SECRET not in log_path.read_text(encoding="utf-8")


def test_error_text_echoing_prompt_is_redacted(log_path):
    """provider 报错回显输入时，正文片段必须被替换（不靠截断碰运气）。"""
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    err = RuntimeError(f"validation failed: input_value={PROMPT_SECRET!r}")
    record_llm_call_error(ctx, model="m", error=err, messages=_messages())

    rec = _read_records(log_path)[0]
    assert "[redacted]" in rec["error"]
    assert PROMPT_SECRET not in rec["error"]
    assert PROMPT_SECRET not in log_path.read_text(encoding="utf-8")


def test_error_text_with_json_escaped_multiline_prompt_is_redacted(log_path):
    """provider 回显多行正文时常以 JSON 转义形态出现（换行 → \\n）。"""
    multiline = "第一行正文\n第二行正文 SECRET_MARKER"
    msgs = [{"role": "user", "content": multiline}]
    escaped = json.dumps(multiline)[1:-1]
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    record_llm_call_error(ctx, model="m", error=RuntimeError(f"echo={escaped}"), messages=msgs)
    rec = _read_records(log_path)[0]
    assert "[redacted]" in rec["error"]
    assert "SECRET_MARKER" not in log_path.read_text(encoding="utf-8")


def test_error_str_that_raises_never_escapes(log_path):
    class _BadError(Exception):
        def __str__(self):
            raise RuntimeError("str() exploded")

    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    record_llm_call_error(ctx, model="m", error=_BadError())
    rec = _read_records(log_path)[0]
    assert rec["error"] == "_BadError"


def test_usage_without_prompt_still_writes_line_with_null_hash(log_path):
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    record_llm_usage(_response(), ctx)
    rec = _read_records(log_path)[0]
    assert rec["prompt_hash"] is None, "拿不到提示词时如实留空，不伪造摘要"
    assert set(rec) == REQUIRED_FIELDS


def test_disabled_switch_writes_nothing_but_accounting_continues(log_path, monkeypatch):
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", False)
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    assert record_llm_usage(_response(), ctx, latency_ms=1.0, messages=_messages()) == 150
    assert ctx.total_tokens == 150, "关闭 JSONL 不得影响既有用量记账"
    assert not log_path.exists()


def _task_digest(task_id: str) -> str:
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:16]


def test_per_run_split_uses_hashed_task_id_filename(tmp_path, monkeypatch):
    base = tmp_path / "llm_calls.jsonl"
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(base))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", True)

    ctx = ExecutionContext(query="q", task_id="run-510", request_id="r")
    record_llm_usage(_response(), ctx, latency_ms=1.0, messages=_messages())
    assert not base.exists()
    expected = tmp_path / f"llm_calls-run-{_task_digest('run-510')}.jsonl"
    assert expected.exists()
    assert "run-510" not in expected.name, "原始 task_id 绝不进入文件名"
    assert [p.name for p in tmp_path.iterdir()] == [expected.name]


def test_per_run_unsafe_task_id_never_reaches_path(tmp_path, monkeypatch):
    base = tmp_path / "llm_calls.jsonl"
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(base))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", True)

    ctx = ExecutionContext(query="q", task_id="../../evil", request_id="r")
    record_llm_usage(_response(), ctx, latency_ms=1.0, messages=_messages())
    expected = tmp_path / f"llm_calls-run-{_task_digest('../../evil')}.jsonl"
    assert expected.exists()
    assert not base.exists()
    assert list(tmp_path.parent.glob("**/*evil*")) == []
    assert all(
        re.fullmatch(r"llm_calls-run-[0-9a-f]{16}\.jsonl", p.name) for p in tmp_path.iterdir()
    ), "文件名只能含 SHA-256 十六进制摘要，不得出现原始 task_id 字符"


def test_append_has_no_caller_supplied_path_parameter():
    """CodeQL path-injection 的结构性回归钉子：落盘路径只能来自配置。"""
    import inspect

    from app.agent.llm_call_log import append_llm_call_record

    params = inspect.signature(append_llm_call_record).parameters
    assert "path" not in params, "不得重新引入调用方传路径的入口"
    assert list(params) == ["record"]


def test_exec_context_has_no_explicit_log_path_field():
    fields = {f.name for f in ExecutionContext.__dataclass_fields__.values()}
    assert "llm_call_log_path" not in fields, "路径只能来自配置，ExecutionContext 不携带路径"


def test_write_failure_never_breaks_the_run(tmp_path, monkeypatch):
    # 把落点指向一个目录：open(..., "a") 必然失败。
    monkeypatch.setattr(settings, "LLM_CALL_LOG_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CALL_LOG_FILE", str(tmp_path))
    monkeypatch.setattr(settings, "LLM_CALL_LOG_PER_RUN", False)

    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    assert record_llm_usage(_response(), ctx, latency_ms=1.0, messages=_messages()) == 150
    assert ctx.total_tokens == 150
    assert ctx.record_llm_call(model="m", error="manual") is False


def test_production_default_enables_llm_call_log():
    """conftest 在测试进程里置 false 只是隔离；生产默认必须为开启。

    变异点：把 `LLM_CALL_LOG_ENABLED` 代码默认改成 False → 本用例红
    （否则生产功能静默消失而全部测试仍绿）。
    """
    from app.core.config.base import BaseAppSettings

    fields = BaseAppSettings.model_fields
    assert fields["LLM_CALL_LOG_ENABLED"].default is True
    assert fields["LLM_CALL_LOG_FILE"].default == "logs/llm_calls.jsonl"
    assert fields["LLM_CALL_LOG_PER_RUN"].default is False


# ── 3b. 失败行与去重守卫的判别性用例 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_planner_downstream_failure_does_not_double_log(log_path, monkeypatch):
    """变异点：删掉 planner 的 `if not llm_recorded` 守卫 → 成功调用后 `_save_plan`
    抛错会被 after-the-fact 记成第二行失败（本用例断言恰好一行）。"""
    from app.agent.planner import Planner

    plan_json = (
        '{"goal": "g", "reasoning": "r", '
        '"steps": [{"id": "s1", "description": "d", "dependencies": []}]}'
    )
    chunks = [
        _chunk("```json\n"),
        _chunk(plan_json),
        _chunk("\n```"),
        _chunk(None, SimpleNamespace(prompt_tokens=10, completion_tokens=2)),
    ]
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_FakeStream(chunks))
    planner = Planner(client, AgentConfig())
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")

    async def _boom(plan):
        raise RuntimeError(f"save failed: {PROMPT_SECRET}")

    monkeypatch.setattr(planner, "_save_plan", _boom)
    [_ async for _ in planner.plan("q", ctx=ctx)]

    records = _read_records(log_path)
    assert len(records) == 1, "成功记账后的下游异常不得再写一行失败记录"
    assert records[0]["error"] is None
    assert PROMPT_SECRET not in log_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_executor_failed_call_logs_exactly_one_error_row(log_path, monkeypatch):
    """变异点：`!call.success` 分支不置 llm_recorded → span 收尾异常会双写。"""
    import app.agent.observability as obs_mod
    from app.agent.executor import Executor
    from app.agent.planner import Plan, PlanStep

    fake_lf = MagicMock()
    step_span = MagicMock()
    # 第一次 span.end 抛错（模拟观测层收尾异常），第二次正常
    step_span.end.side_effect = [RuntimeError("span end boom"), None]
    fake_lf.span.return_value = step_span
    fake_lf.generation.return_value = MagicMock()
    monkeypatch.setattr(obs_mod, "get_langfuse", lambda: fake_lf)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("provider down"))
    memory = MagicMock()
    memory.get_context_for_query = AsyncMock(return_value="")
    ex = Executor(
        client,
        AgentConfig(timeout=0.5, enable_planning=False, enable_reflection=False),
        memory,
    )
    step = PlanStep(id="s1", description="d")
    plan = Plan(id="p1", goal="g", steps=[step])
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")

    events = [
        e
        async for e in ex._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=False,
            ctx=ctx,
        )
    ]

    assert any(e.type == "tool_error" for e in events)
    records = _read_records(log_path)
    assert len(records) == 1, "同一失败调用只能有一行（span 收尾异常不得双写）"
    assert records[0]["error"] and "provider down" in records[0]["error"]
    assert set(records[0]) == REQUIRED_FIELDS


@pytest.mark.asyncio
async def test_tool_llm_failure_writes_one_redacted_error_row(log_path, monkeypatch):
    """工具内 LLM 调用失败同样必须留一行（M2 回归钉子）。"""
    from app.agent.run_context import RunContext
    from app.agent.tools.translation import translate_text

    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    run_context = RunContext(user_id=0, organization_id=1, extra={"exec_ctx": ctx})
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        side_effect=RuntimeError(f"provider error: {PROMPT_SECRET}")
    )
    monkeypatch.setattr(
        "app.dependencies.get_rag_pipeline",
        lambda: SimpleNamespace(openai_client=client),
    )

    result = await translate_text(PROMPT_SECRET, "zh", run_context=run_context)
    assert result.success is False

    records = _read_records(log_path)
    assert len(records) == 1, "工具内 LLM 失败调用必须恰好一行"
    assert set(records[0]) == REQUIRED_FIELDS
    assert records[0]["model"] != "unknown", "失败行也应记录真实模型名"
    assert "[redacted]" in records[0]["error"]
    assert PROMPT_SECRET not in log_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("tool_name", "call_kwargs"),
    [
        ("summarize_document", {"document_id": "d1"}),
        ("extract_insights", {"document_id": "d1"}),
        ("cross_document_analysis", {"document_ids": ["d1", "d2"]}),
        (
            "generate_report",
            {"title": "报告", "sections": '[{"heading": "h", "content": "c"}]'},
        ),
    ],
)
@pytest.mark.asyncio
async def test_core_tool_llm_failure_writes_one_row(log_path, monkeypatch, tool_name, call_kwargs):
    """core_tools 四个工具内 LLM 失败同样各留恰好一行（M2 覆盖）。"""
    import app.agent.core_tools as core_tools_mod
    from app.agent.run_context import RunContext
    from app.core.elasticsearch import ElasticsearchTools

    async def _search(_query):
        return {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "content": "doc body",
                            "filename": "f.txt",
                            "document_id": "d1",
                        }
                    }
                ]
            }
        }

    monkeypatch.setattr(ElasticsearchTools, "search_documents", _search)
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("tool llm down"))
    monkeypatch.setattr(
        "app.dependencies.get_rag_pipeline",
        lambda: SimpleNamespace(openai_client=client),
    )

    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    run_context = RunContext(user_id=0, organization_id=1, extra={"exec_ctx": ctx})
    tool = getattr(core_tools_mod, tool_name)
    await tool(organization_id=1, run_context=run_context, **call_kwargs)

    records = _read_records(log_path)
    assert len(records) == 1, f"{tool_name}: 失败调用必须恰好一行"
    assert records[0]["error"] and "tool llm down" in records[0]["error"]
    assert records[0]["model"] != "unknown", "失败行也应记录真实模型名"
    assert set(records[0]) == REQUIRED_FIELDS


@pytest.mark.asyncio
async def test_context_summarizer_failure_writes_one_row(log_path):
    """ContextEngine 摘要失败同样留一行（此前 except 静默 return ""）。"""
    from app.agent.context import ContextEngine

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=RuntimeError("summary down"))
    engine = ContextEngine()
    engine.set_llm_client(client)

    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    out = await engine._summarize_with_llm(
        [{"role": "user", "content": "hello there"}], token_budget=1000, ctx=ctx
    )
    assert out == ""

    records = _read_records(log_path)
    assert len(records) == 1
    assert records[0]["error"] and "summary down" in records[0]["error"]
    assert records[0]["model"] != "unknown"
    assert set(records[0]) == REQUIRED_FIELDS


@pytest.mark.asyncio
async def test_tool_pre_call_failure_does_not_write_llm_row(log_path, monkeypatch):
    """MINOR-2 钉子：pipeline 获取失败 = 没有发生 LLM 调用，不得记失败行。"""
    from app.agent.run_context import RunContext
    from app.agent.tools.translation import translate_text

    def _boom():
        raise RuntimeError("pipeline unavailable")

    monkeypatch.setattr("app.dependencies.get_rag_pipeline", _boom)
    ctx = ExecutionContext(query="q", task_id="t", request_id="r")
    run_context = RunContext(user_id=0, organization_id=1, extra={"exec_ctx": ctx})

    result = await translate_text("hello world", "zh", run_context=run_context)
    assert result.success is False
    assert _read_records(log_path) == [], "未发起 provider 调用不得写 LLM 调用行"


# ── 4. 端到端：PER loop run 的 task_id/request_id 真正进入 JSONL ──────────


@pytest.mark.asyncio
async def test_toolless_run_propagates_task_and_request_id_into_jsonl(log_path, monkeypatch):
    """变异点：loop.run 不接 request_id / 不把它写进 ctx → request_id 断言红。"""
    from app.agent.loop import PERAgentLoop

    async def _no_save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _no_save)

    class _FakeMemory:
        def reset_working(self):
            pass

        def get_all_results(self):
            return {}

        def store_step_result(self, *a, **k):
            pass

        def get_step_result(self, *a, **k):
            return None

        async def record_interaction(self, *a, **k):
            pass

    resp = MagicMock()
    msg = MagicMock()
    msg.content = COMPLETION_SECRET
    msg.tool_calls = None
    resp.model = "deepseek-loop-test"
    resp.choices = [MagicMock()]
    resp.choices[0].message = msg
    resp.usage = SimpleNamespace(prompt_tokens=50, completion_tokens=10)

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)

    loop = PERAgentLoop(
        client,
        AgentConfig(
            enable_planning=False,
            enable_tools=False,
            enable_reflection=False,
            enable_memory=False,
            enable_thinking=False,
            enable_experience=False,
            enable_checkpoint=False,
        ),
        organization_id=1,
        user_id=0,
    )
    loop.memory = _FakeMemory()
    loop._write_experiences = AsyncMock()

    events = await asyncio.wait_for(
        _collect_run(loop, "问题", task_id="task-510-e2e", request_id="req-510-e2e"),
        timeout=15.0,
    )
    assert any(e.type == "execution_context" for e in events)

    records = _read_records(log_path)
    assert len(records) == 1, "无工具直答 = 一次 LLM 调用 = 一行"
    rec = records[0]
    assert rec["task_id"] == "task-510-e2e", "task_id 必须与 run/SSE 同源"
    assert rec["request_id"] == "req-510-e2e", "request_id 必须来自本次请求"
    assert rec["model"] == "deepseek-loop-test"
    assert (rec["tokens_in"], rec["tokens_out"]) == (50, 10)
    assert rec["latency_ms"] is not None and rec["latency_ms"] >= 0
    assert rec["prompt_hash"].startswith("sha256:")
    assert "问题" not in log_path.read_text(encoding="utf-8")
    assert COMPLETION_SECRET not in log_path.read_text(encoding="utf-8")


async def _collect_run(loop, query: str, **kwargs) -> list:
    return [e async for e in loop.run(query, **kwargs)]
