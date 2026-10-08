"""CI CodeQL 收口：日志安全化（log_safe / CWE-117）行为契约。

- 控制字符（CR/LF/TAB/C0/DEL）转义为可见形态——含换行的值进日志后仍是**单物理行**，
  攻击者无法用 task_id 之类输入伪造日志行（log forging）；
- 代表性用例：把带 `\\n` 的 task_id 喂给真实的 checkpoint 日志点，断言输出单行；
- 变异点：log_safe 去掉换行替换（`return str(value)`）→ 本文件用例红。
"""

import logging

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent.exec_context import ExecutionContext
from app.agent.planner import Plan
from app.core.database import Base
from app.core.log_safety import log_safe


class _LogCollector(logging.Handler):
    """挂在目标 logger 自身（不依赖 root/caplog，避免全局日志污染干扰）。"""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_log_safe_escapes_control_characters():
    assert log_safe("plain text 123") == "plain text 123"
    assert log_safe(None) == ""
    assert log_safe("a\nb") == "a\\nb"
    assert log_safe("a\r\nb") == "a\\r\\nb"
    assert log_safe("a\rb\tc") == "a\\rb\\tc"
    assert log_safe("x\x00y") == "x\\x00y"
    assert log_safe("d\x7f") == "d\\x7f"


def test_log_safe_output_never_contains_real_newlines():
    hostile = "line1\n[FORGED] ADMIN LOGIN OK\nline3\r\nline4\x0b\x0c"
    out = log_safe(hostile)
    assert "\n" not in out and "\r" not in out, "任何输入都不得在输出里制造真实换行"
    assert "line1" in out and "[FORGED]" in out, "值本身保留（可见化），而不是被丢弃"


def test_log_safe_truncates_long_values():
    out = log_safe("a" * 5000)
    assert out.endswith("...[truncated]")
    assert len(out) <= 512 + len("...[truncated]")


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'logsafe.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_checkpoint_log_stays_single_line_for_hostile_task_id(db_factory):
    """代表性用例：含 \\n 的 task_id 走真实 checkpoint 日志点 → 单行不伪造。

    变异点：把 log_safe 去转义（恢复直接插值）→ 记录里出现真实换行 → 红。
    """
    logger = logging.getLogger("app.agent.checkpoint")
    collector = _LogCollector()
    prev_level, prev_disabled = logger.level, logger.disabled
    logger.addHandler(collector)
    logger.setLevel(logging.DEBUG)
    logger.disabled = False
    try:
        ok = await checkpoint.save_checkpoint(
            task_id="evil\n[FORGED] task=admin 已恢复",
            ctx=ExecutionContext(query="q"),
            plan=Plan(id="p", goal="g"),
            trigger="after_each_step",
        )
    finally:
        logger.disabled = prev_disabled
        logger.setLevel(prev_level)
        logger.removeHandler(collector)

    assert ok is False  # 任务不存在：走到日志点（save skipped）
    msgs = [r.getMessage() for r in collector.records]
    assert any("checkpoint save" in m for m in msgs), f"未捕获到目标日志：{msgs}"
    for m in msgs:
        assert "\n" not in m and "\r" not in m, f"日志行被注入换行：{m!r}"
    joined = "\n".join(msgs)
    assert "[FORGED]" in joined, "恶意值应被可见化保留（可审计），而不是静默丢弃"
    assert "\\n" in joined, "换行应以转义形态出现"
