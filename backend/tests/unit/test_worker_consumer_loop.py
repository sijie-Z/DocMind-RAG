"""Unit tests for the worker consume loop: polling, commits, heartbeat (issue #156);
failure offset semantics (issue #301 批 D / #30).

用假 consumer 注入轮询脚本与异常，不依赖真实 Kafka，也不使用真实 sleep；
用手动 touch 记录器验证"每次轮询刷新心跳"，而不是在测试里等时间流逝。

#30 语义（复现原文：`_orchestrate/round2/_docs301/repro_30_baseline.txt`）：
文档级失败（``process`` 返回 False 或抛异常）**不得**提交偏移（修前 False 被
忽略、照样 commit）。重试预算未尽 → 抛 ``RedeliveryHalt`` 停住消费，容器重启后
从上次提交点重投；预算耗尽（``job.retry_count`` 达上限或没有账本）→ 终局提交
（死信语义，文档/任务已 FAILED + ERROR 日志）。
"""

from types import SimpleNamespace

import pytest

from app.worker.consumer import (
    MAX_DELIVERY_ATTEMPTS,
    RedeliveryHalt,
    _record_failed_attempt,
    run_consume_loop,
)


class _Stop(Exception):
    """哨兵异常：假 consumer 脚本耗尽后抛出，用于结束死循环。"""


class FakeConsumer:
    """最小 Kafka consumer 替身：按脚本返回 getmany 批次，记录 commit 次数。"""

    def __init__(self, batches, events=None):
        self._batches = list(batches)
        self.commits = 0
        self._events = events if events is not None else []

    async def getmany(self, timeout_ms=0):
        if not self._batches:
            raise _Stop
        item = self._batches.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def commit(self):
        self.commits += 1
        self._events.append("commit")


def _msg(value):
    return SimpleNamespace(value=value)


@pytest.mark.asyncio
async def test_heartbeat_refreshed_on_every_poll_including_idle():
    # 三次空轮询（模拟空闲等待消息）→ 心跳应被刷新三次
    consumer = FakeConsumer([{}, {}, {}])
    beats = []

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, touch=lambda: beats.append("beat"))

    assert beats == ["beat", "beat", "beat"]


@pytest.mark.asyncio
async def test_message_processed_then_committed_with_job_id():
    events = []

    async def fake_process(document_id, job_id):
        events.append(("process", document_id, job_id))

    consumer = FakeConsumer(
        [{"topic-0": [_msg({"document_id": "doc-1", "job_id": 7})]}],
        events=events,
    )

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    # 先处理、后提交（失败消息不提交的语义依赖此顺序）
    assert events == [("process", "doc-1", 7), "commit"]


@pytest.mark.asyncio
async def test_non_dict_message_skipped_and_committed():
    processed = []

    async def fake_process(document_id, job_id):
        processed.append(document_id)

    consumer = FakeConsumer([{"topic-0": [_msg(["not", "a", "dict"])]}])

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    assert processed == []
    assert consumer.commits == 1


@pytest.mark.asyncio
async def test_missing_document_id_committed_without_processing():
    processed = []

    async def fake_process(document_id, job_id):
        processed.append(document_id)

    consumer = FakeConsumer([{"topic-0": [_msg({"job_id": 1})]}])

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    assert processed == []
    assert consumer.commits == 1


@pytest.mark.asyncio
async def test_processing_error_not_committed_and_halts_for_redelivery():
    """#30：process 抛异常 = 文档级失败 → 不提交、停住消费等重投（修前：不提交但继续循环）。

    修前语义「继续循环 + 不提交」其实保护不了失败偏移：aIoKafka 的 commit() 提交
    消费位点，getmany 返回批次时位点已推进过整批——后续任一成功消息的 commit 会把
    失败偏移一并带过。停住（RedeliveryHalt 穿出循环 → 进程退出 → 容器重启）才让
    "不提交"成为真承诺。

    变异点：把失败分支改回「log 后继续循环」（或无条件 commit）→ 本用例红。
    """
    events = []

    async def fake_process(document_id, job_id):
        if document_id == "doc-1":
            raise RuntimeError("boom")
        events.append(("processed", document_id))

    consumer = FakeConsumer(
        [{"topic-0": [_msg({"document_id": "doc-1"}), _msg({"document_id": "doc-2"})]}],
        events=events,
    )
    recorded = []

    async def fake_record(job_id):
        recorded.append(job_id)
        return 1  # 预算未尽

    with pytest.raises(RedeliveryHalt):
        await run_consume_loop(
            consumer,
            process=fake_process,
            touch=lambda: events.append("beat"),
            record_attempt=fake_record,
        )

    # doc-1 失败：记账一次、不提交、后续消息未被处理（重启后整批重投）
    assert recorded == [None]
    assert events == ["beat"]
    assert consumer.commits == 0, "失败消息偏移不得提交"


@pytest.mark.asyncio
async def test_failed_processing_false_not_committed_and_halts_when_budget_remains():
    """#30 核心用例：process 返回 False（修前被忽略照样 commit）→ 不提交 + 停住。

    复现对比（repro_30_baseline.txt）：修前注入 False → consumer.commits == 1。

    变异点：`if processed is False` 判断去掉（恢复"不看返回值直接 commit"）→ 红。
    """
    calls = []

    async def fake_process(document_id, job_id):
        calls.append((document_id, job_id))
        return False  # 文档级失败：doc/job 已在 processor 内落 FAILED

    consumer = FakeConsumer([{"topic-0": [_msg({"document_id": "doc-bad", "job_id": 7})]}])

    async def fake_record(job_id):
        return 1  # 预算未尽

    with pytest.raises(RedeliveryHalt) as ei:
        await run_consume_loop(
            consumer,
            process=fake_process,
            touch=lambda: None,
            record_attempt=fake_record,
        )

    assert calls == [("doc-bad", 7)]
    assert consumer.commits == 0
    assert "doc-bad" in str(ei.value)


@pytest.mark.asyncio
async def test_failed_processing_terminal_commit_after_budget_exhausted():
    """#30：重试预算耗尽 → 终局提交（死信语义），循环继续处理后续消息。"""
    events = []

    async def fake_process(document_id, job_id):
        if document_id == "doc-bad":
            return False
        events.append(("processed", document_id))
        return True

    consumer = FakeConsumer(
        [{"topic-0": [_msg({"document_id": "doc-bad", "job_id": 7}), _msg({"document_id": "doc-ok", "job_id": 8})]}]
    )

    async def fake_record(job_id):
        return MAX_DELIVERY_ATTEMPTS  # 已耗尽

    with pytest.raises(_Stop):
        await run_consume_loop(
            consumer,
            process=fake_process,
            touch=lambda: None,
            record_attempt=fake_record,
        )

    # 毒消息终局提交后循环继续：doc-ok 处理并提交；共 2 次提交（终局 + 成功）
    assert events == [("processed", "doc-ok")]
    assert consumer.commits == 2


@pytest.mark.asyncio
async def test_success_returns_commit_after_failure_streak_not_halted():
    """回归对照：正常 True 返回照常提交（不触发任何失败路径）。"""
    consumer = FakeConsumer([{"topic-0": [_msg({"document_id": "doc-1"})]}])

    async def fake_process(document_id, job_id):
        return True

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    assert consumer.commits == 1


class TestRecordFailedAttempt:
    """_record_failed_attempt 的记账/边界语义（默认实现，注入假会话）。"""

    @pytest.mark.asyncio
    async def test_missing_job_id_returns_max_terminal(self):
        assert await _record_failed_attempt(None) == MAX_DELIVERY_ATTEMPTS

    @pytest.mark.asyncio
    async def test_missing_job_row_returns_max_terminal(self, monkeypatch):
        import app.core.database as db_mod

        session = SimpleNamespace(get=None)

        class _Ctx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *a):
                return False

        async def _get(model, job_id):
            return None

        session.get = _get
        monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _Ctx())
        assert await _record_failed_attempt(7) == MAX_DELIVERY_ATTEMPTS

    @pytest.mark.asyncio
    async def test_bump_increments_and_returns(self, monkeypatch):
        import app.core.database as db_mod

        job = SimpleNamespace(retry_count=1)
        commits = []

        class _Session:
            async def get(self, model, job_id):
                return job

            async def commit(self):
                commits.append(True)

        class _Ctx:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _Ctx())
        assert await _record_failed_attempt(7) == 2
        assert job.retry_count == 2 and commits

    @pytest.mark.asyncio
    async def test_db_failure_treated_as_retryable(self, monkeypatch):
        import app.core.database as db_mod

        class _Ctx:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(db_mod, "AsyncSessionLocal", lambda: _Ctx())
        assert await _record_failed_attempt(7) == 0  # 保守按可重投（重启后自愈）


@pytest.mark.asyncio
async def test_poll_failure_propagates_to_caller():
    # 轮询异常（如 Kafka 断连）沿旧语义向上抛给 consume()，由容器重启兜底
    consumer = FakeConsumer([RuntimeError("kafka down")])

    with pytest.raises(RuntimeError, match="kafka down"):
        await run_consume_loop(consumer, touch=lambda: None)

    assert consumer.commits == 0
