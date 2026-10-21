"""issue #301 批 A（recon 表一 #10/#11）：Agent 运行状态诚实化。

#10：checkpoint.mark_terminal 的 DB 写失败被静默（warning + 返回值，
     调用方根本不看）→ agent_tasks 永远 RUNNING（前端"进行中"、还被认为
     可 resume），没有任何可观测信号。
#11：无工具模式（enable_tools=False）LLM 调用失败 → 只发一条 error 事件，
     ctx.failures 空、run_report clean_success=true，任务终态仍是 COMPLETED
     （零产出"假成功"）。

修复语义（对齐 #293 已落地范式：ctx.failures 唯一写者 / run_report 非全绿）：
- mark_terminal：写失败重试一次（新会话）；仍失败 → logger.error 响亮留痕
  （含后果：任务行停留原状态、可 resume 收敛）并返回 False；从不抛异常
  （清理路径契约：_checkpoint_error 内抛异常会顶掉原始异常）。选择"库内
  重试 + 调用方检查返回值"而非 library-level 抛错，就是为了保住这条契约；
- service 收尾：必须消费返回值——写失败时响亮记一笔 + 向客户端如实补发
  一条 error 事件；任务行保持 RUNNING（语义自洽：可 resume 收敛），
  绝不静默从"进行中"滑过去；
- #11：直接回答失败 → ctx.complete_step(..., "failed")（点亮 ctx.failures，
  报告非全绿）+ run_failed 标志；service 收尾按标志置 FAILED
  （FAILED→RUNNING 是既有重试边，可恢复），不再 COMPLETED。

变异矩阵（每条精确红）：
    M10a mark_terminal 去掉重试（单次尝试即放弃）→ 用例 1 红；
    M10b 双败路径降级为 debug/静默（无 ERROR）→ 用例 2 红；
    M10c service 收尾恢复不消费返回值（不补发信号）→ 用例 3 红；
    M11a 删循环里 ctx.complete_step(...) 记账 → 用例 4 的 clean_success 红；
    M11b service 收尾恢复无条件 COMPLETED → 用例 4 的任务状态断言红。
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.service import AgentService
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus

QUERY = "总结一下这份项目资料"
USER_ID = 7


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """隔离与 checkpoint 无关的副作用（replay 落盘 / 经验回写）。"""

    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'honesty.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


def _fake_openai_client(text: str = "步骤结果"):
    """直答型假 client：每次调用返回 content（无 tool_calls）。"""

    async def _create(**kwargs):
        msg = SimpleNamespace(content=text, tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client


def _failing_openai_client(exc_text: str = "LLM down (simulated)"):
    """全败假 client：任何 LLM 调用都抛异常（模拟 key 失效/服务挂）。"""

    async def _create(**kwargs):
        raise RuntimeError(exc_text)

    client = MagicMock()
    client.chat.completions.create = _create
    return client


def _tool_call_client(tool_name: str, args_json: str, synthesis: str = "降级合成答案"):
    """带 tools 的主调恒发一个 tool_call；不带 tools 的合成调用返回文本。"""
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        msg = MagicMock()
        if tools:
            tc = MagicMock()
            tc.id = "tc1"
            tc.function.name = tool_name
            tc.function.arguments = args_json
            msg.tool_calls = [tc]
            msg.content = ""
        else:
            msg.tool_calls = None
            msg.content = synthesis
        msg.role = "assistant"
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    client.chat.completions.create = _create
    return client


def _agent_config(**overrides) -> AgentConfig:
    base = dict(
        enable_reflection=False,
        enable_memory=False,
        enable_skills=False,
        enable_experience=False,
        enable_thinking=False,
    )
    base.update(overrides)
    return AgentConfig(**base)


async def _get_task(factory, task_id: str) -> AgentTask | None:
    async with factory() as db:
        return await db.get(AgentTask, task_id)


def _task_id_of(events) -> str:
    ids = {e.task_id for e in events if e.task_id}
    assert ids, "事件必须盖上 task_id"
    assert len(ids) == 1
    return ids.pop()


# ── #10：mark_terminal 写失败重试 + 响亮 + 返回值被消费 ─────────────────────


class _CommitHiccup(Exception):
    pass


def _flaky_session_factory(real_factory, state: dict):
    """包装会话工厂：对 commit() 按 state["fail_commits"] 注入 N 次失败。

    state 字段：fail_commits（剩余注入次数）、attempts（commit 被调用次数）。
    """

    class _SessionProxy:
        def __init__(self, session):
            self._session = session

        async def commit(self):
            state["attempts"] += 1
            if state["fail_commits"] > 0:
                state["fail_commits"] -= 1
                raise _CommitHiccup("db hiccup")
            return await self._session.commit()

        def __getattr__(self, name):
            return getattr(self._session, name)

    class _Ctx:
        def __init__(self):
            self._ctx = real_factory()

        async def __aenter__(self):
            return _SessionProxy(await self._ctx.__aenter__())

        async def __aexit__(self, *exc):
            return await self._ctx.__aexit__(*exc)

    return _Ctx


@pytest.mark.asyncio
async def test_mark_terminal_retries_once_on_write_failure(db_factory, monkeypatch, caplog):
    """写失败重试一次即成功：恰好两次 commit，行到位 COMPLETED。

    变异 M10a（去掉重试）→ 第一次 commit 失败即放弃 → attempts==1、返回 False → 红。
    """
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
    assert task_id
    state = {"fail_commits": 1, "attempts": 0}
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", _flaky_session_factory(db_factory, state))

    with caplog.at_level(logging.DEBUG, logger="app.agent.checkpoint"):
        ok = await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED)

    assert ok is True, "第一次 commit 失败后必须重试一次并成功"
    assert state["attempts"] == 2, f"应恰好两次写入尝试，实际 {state['attempts']}"
    assert any(r.levelno == logging.WARNING and "重试" in r.getMessage() for r in caplog.records)
    row = await _get_task(db_factory, task_id)
    assert row.status == TaskStatus.COMPLETED and row.completed_at is not None


@pytest.mark.asyncio
async def test_mark_terminal_write_failure_loud_and_row_untouched(db_factory, monkeypatch, caplog):
    """两次都失败：返回 False + ERROR 级响亮留痕；行保持 RUNNING（可恢复）。

    变异 M10b（双败降噪为 debug/warning）→ 无 ERROR 记录 → 红。
    """
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
    assert task_id
    state = {"fail_commits": 2, "attempts": 0}
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", _flaky_session_factory(db_factory, state))

    with caplog.at_level(logging.DEBUG, logger="app.agent.checkpoint"):
        ok = await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED)

    assert ok is False
    assert state["attempts"] == 2, "恰好重试一次（共两次尝试）"
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "双败必须响亮（ERROR 级日志）——写失败不得静默"
    assert task_id in errors[-1].getMessage()

    row = await _get_task(db_factory, task_id)
    assert row.status == TaskStatus.RUNNING, "写失败后行保持原状态（可 resume 收敛，不伪造完成）"
    assert row.is_recoverable is True, "RUNNING 语义自洽：可恢复"


@pytest.mark.asyncio
async def test_chat_terminal_write_failure_surfaces_and_stays_recoverable(
    db_factory,
    monkeypatch,
    caplog,
):
    """端到端（#10）：终态写失败 → ERROR 日志 + 如实事件；任务保持 RUNNING，
    且写路径恢复后 resume 能收敛到 COMPLETED（语义自洽闭环）。

    变异 M10c（service 不消费返回值）→ 无补发事件 → 红。
    """
    client = _fake_openai_client()
    svc = AgentService(openai_client=client)
    real_mark = checkpoint.mark_terminal
    seen: list[TaskStatus] = []

    async def _dead_mark(task_id, to, *, error=None, error_type=None):
        seen.append(to)
        return False

    monkeypatch.setattr(checkpoint, "mark_terminal", _dead_mark)

    with caplog.at_level(logging.DEBUG, logger="app.agent.service"):
        events = [
            e
            async for e in svc.chat(
                QUERY,
                organization_id=1,
                user_id=USER_ID,
                config=_agent_config(),
            )
        ]

    task_id = _task_id_of(events)
    assert seen and seen[-1] == TaskStatus.COMPLETED, "收尾必须尝试置 COMPLETED"
    assert any(r.levelno >= logging.ERROR for r in caplog.records), "写失败必须响亮留痕"

    surface = [e for e in events if e.type == "error" and "收尾失败" in e.content]
    assert len(surface) == 1, (
        f"写失败必须向客户端如实补发信号（修复前完全静默）: {[e.type for e in events]}"
    )
    assert surface[0].task_id == task_id, "补发事件必须盖章 task_id"

    row = await _get_task(db_factory, task_id)
    assert row.status == TaskStatus.RUNNING and row.is_recoverable, (
        "写失败后不得被伪造成 COMPLETED；RUNNING 是可收敛态"
    )

    # 语义收敛：写路径恢复后，resume 把 RUNNING 收敛到 COMPLETED
    monkeypatch.setattr(checkpoint, "mark_terminal", real_mark)
    events2 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID)]
    assert any(e.type == "done" for e in events2)
    assert (await _get_task(db_factory, task_id)).status == TaskStatus.COMPLETED


# ── #11：无工具模式 LLM 全败不得判 COMPLETED / 报 clean_success ─────────────


@pytest.mark.asyncio
async def test_tooless_llm_failure_marks_failed_and_report_not_green(db_factory):
    """无工具模式 LLM 全败：任务 FAILED（非 COMPLETED）+ 报告非全绿。

    修复前：任务 COMPLETED、run_report clean_success=true、ctx.failures 空
    （零产出假成功）。本用例在修复前全红。

    变异 M11a（删 ctx.complete_step 记账）→ clean_success 断言红；
    变异 M11b（service 恢复无条件 COMPLETED）→ 任务状态断言红。
    """
    svc = AgentService(openai_client=_failing_openai_client())
    cfg = _agent_config(enable_tools=False)

    events = [e async for e in svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=cfg)]

    task_id = _task_id_of(events)
    row = await _get_task(db_factory, task_id)
    assert row.status == TaskStatus.FAILED, (
        f"无工具模式 LLM 全败不得判 COMPLETED（零产出假成功），实际 {row.status}"
    )
    assert row.is_recoverable is True, "FAILED→RUNNING 是既有重试边：可恢复"
    assert row.last_error and "LLM" in row.last_error, "失败原因必须落 last_error"

    assert any(e.type == "error" and "LLM" in e.content for e in events), "错误必须如实流出"

    report_events = [e for e in events if e.type == "run_report"]
    assert report_events, "run_report 必须产出"
    assert "clean_success: False" in report_events[-1].content, "零产出 run 不得报干净成功"
    assert "FAILURE:" in report_events[-1].content

    ctx_events = [e for e in events if e.type == "execution_context"]
    assert ctx_events and ctx_events[-1].content["failures"], (
        "失败必须进 ctx.failures（#293 范式的唯一写者：complete_step）"
    )


@pytest.mark.asyncio
async def test_tooless_success_stays_completed_and_all_green(db_factory):
    """反向对照（防过度修复）：无工具模式正常出答 → 仍 COMPLETED + 全绿。"""
    svc = AgentService(openai_client=_fake_openai_client(text="你好，这是直接回答"))
    cfg = _agent_config(enable_tools=False)

    events = [e async for e in svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=cfg)]

    task_id = _task_id_of(events)
    assert (await _get_task(db_factory, task_id)).status == TaskStatus.COMPLETED
    assert not any(e.type == "error" for e in events)
    report_events = [e for e in events if e.type == "run_report"]
    assert report_events and "clean_success: True" in report_events[-1].content


# ── #11 分界（评审 P3-4）：部分步骤失败 → 保持 COMPLETED 不翻转 ─────────────


@pytest.mark.asyncio
async def test_tools_partial_step_failure_stays_completed_not_flipped(db_factory):
    """既有分界语义钉防（评审 P3-4）：受限工具集里模型发未列名工具 → 该步骤
    失败进 ctx.failures / 报告非全绿，但任务终态保持 COMPLETED 不翻转。

    分界：只有"无工具直答 LLM 全败（零产出）"才置 run_failed→FAILED（#11）；
    "部分步骤失败但有降级产出"是既有 COMPLETED 语义。变异 S3（任意
    ctx.failures 非空 → run_failed）此前在旧套件零红——本用例精确抓红。
    """
    cfg = _agent_config(enable_planning=False, tool_tags=["search", "analysis"])
    svc = AgentService(openai_client=_tool_call_client("delegate_subtask", "{}"))

    events = [e async for e in svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=cfg)]

    task_id = _task_id_of(events)
    row = await _get_task(db_factory, task_id)
    assert row.status == TaskStatus.COMPLETED, (
        f"部分步骤失败保持既有语义（不翻转），实际 {row.status}——只有零产出全败才 FAILED"
    )
    report_events = [e for e in events if e.type == "run_report"]
    assert report_events and "clean_success: False" in report_events[-1].content, (
        "报告必须非全绿（部分步骤失败进 ctx.failures）"
    )
    assert any(e.type == "chunk" and e.content for e in events), "降级输出必须存在"
    surface = [e for e in events if e.type == "error" and "收尾失败" in (e.content or "")]
    assert not surface, "成功写路径不得出现收尾失败噪音"
