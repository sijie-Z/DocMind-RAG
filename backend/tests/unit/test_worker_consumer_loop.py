"""Unit tests for the worker consume loop: polling, commits, heartbeat (issue #156);
failure offset semantics (issue #301 批 D / #30, 评审 B1 修订).

用假 consumer 注入轮询脚本与异常，不依赖真实 Kafka，也不使用真实 sleep；
用手动 touch 记录器验证"每次轮询刷新心跳"，而不是在测试里等时间流逝。

#30 位点语义（复现原文：`_orchestrate/round2/_docs301/repro_30_baseline.txt`；
评审 B1 归档：`_orchestrate/round2/_rv316_docs301/`）：

- 文档级失败（``process`` 返回 False 或抛异常）**不得**提交该条偏移（修前 False
  被忽略、照样 commit）；
- 成功消息一律**按条显式提交** ``{tp: msg.offset + 1}``，绝不用无参 ``commit()``
  ——aIoKafka 的 ``getmany`` 返回批次时位点已推进到批尾+1，无参 commit 会把同批
  未处理/失败消息一并划过（批内 k≥1 失败不重投 + 批尾静默丢弃，评审 B1）；
- 重试预算未尽 → 抛 ``RedeliveryHalt`` 停住，重启从"最后一条成功"的下一格重投
  （失败消息与批尾全保）；预算耗尽（``job.retry_count`` 达上限或没有账本）→
  按条终局提交（死信语义）。

本文件的假 consumer（``PositionFakeConsumer``）**忠实建模位点推进语义**（评审 S2
同法）——旧的"只计数"替身无法发现 B1 洞，不再使用。
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


class PositionFakeConsumer:
    """忠实建模 aIoKafka 0.13 位点语义的替身（评审 B1/S2）：

    - ``getmany`` **返回批次时** position 推进到批尾+1
      （``consumer/fetcher.py`` 的 ``_update_position``）；
    - 无参 ``commit()`` 提交当前 position（会把已 yield 未处理的整批划过——
      修前缺陷的机制面；此替身下面无参提交立刻体现在 ``commits`` 的 None 与
      ``committed`` 跳到批尾上）；
    - 显式 ``commit({tp: OffsetAndMetadata})`` 只提交给定位点。
    """

    def __init__(self, batches, topic="topic-0", partition=0, start_offset=0):
        self._batches = list(batches)
        self._topic = topic
        self._partition = partition
        self.position = start_offset
        self.committed: dict[tuple[str, int], int] = {}
        self.commits: list = []  # 每次 commit 的实参（None 表示无参提交）

    async def getmany(self, timeout_ms=0):
        if not self._batches:
            raise _Stop
        item = self._batches.pop(0)
        if isinstance(item, BaseException):
            raise item
        for records in item.values():
            for m in records:
                if getattr(m, "offset", None) is not None:
                    self.position = m.offset + 1  # 返回即推进（fetcher 语义）
        return item

    async def commit(self, offsets=None):
        self.commits.append(offsets)
        if offsets is None:
            # 无参：提交当前 position（含已 yield 但未处理的消息）
            self.committed[(self._topic, self._partition)] = self.position
        else:
            for tp, om in offsets.items():
                self.committed[(tp.topic, tp.partition)] = om.offset


def _msg(value, offset):
    return SimpleNamespace(value=value, topic="topic-0", partition=0, offset=offset)


def _all_explicit(consumer) -> bool:
    """所有提交都是按条显式提交（无 None）。"""
    return bool(consumer.commits) and all(c is not None for c in consumer.commits)


@pytest.mark.asyncio
async def test_heartbeat_refreshed_on_every_poll_including_idle():
    # 三次空轮询（模拟空闲等待消息）→ 心跳应被刷新三次
    consumer = PositionFakeConsumer([{}, {}, {}])
    beats = []

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, touch=lambda: beats.append("beat"))

    assert beats == ["beat", "beat", "beat"]


@pytest.mark.asyncio
async def test_message_processed_then_committed_explicitly_with_job_id():
    events = []

    async def fake_process(document_id, job_id):
        events.append(("process", document_id, job_id))

    consumer = PositionFakeConsumer(
        [{"topic-0": [_msg({"document_id": "doc-1", "job_id": 7}, offset=5)]}]
    )

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    # 先处理、后提交；提交必须按条显式（{tp: offset+1}），不是无参提交
    assert events == [("process", "doc-1", 7)]
    assert _all_explicit(consumer)
    assert consumer.committed == {("topic-0", 0): 6}


@pytest.mark.asyncio
async def test_non_dict_message_skipped_and_committed():
    processed = []

    async def fake_process(document_id, job_id):
        processed.append(document_id)

    consumer = PositionFakeConsumer(
        [{"topic-0": [_msg(["not", "a", "dict"], offset=5)]}]
    )

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    assert processed == []
    assert _all_explicit(consumer)
    assert consumer.committed == {("topic-0", 0): 6}


@pytest.mark.asyncio
async def test_missing_document_id_committed_without_processing():
    processed = []

    async def fake_process(document_id, job_id):
        processed.append(document_id)

    consumer = PositionFakeConsumer(
        [{"topic-0": [_msg({"job_id": 1}, offset=5)]}]
    )

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    assert processed == []
    assert _all_explicit(consumer)
    assert consumer.committed == {("topic-0", 0): 6}


@pytest.mark.asyncio
async def test_processing_error_at_batch_head_not_committed_and_halts():
    """#30 批首失败（k=0，既有用例保持）：抛异常 = 文档级失败 → 不提交、停住。

    本批 [o10 失败, o11 未处理]：整批未提交（committed 为空），重启从 o10 重投。
    变异点：把失败分支改回「log 后继续循环」（或无条件提交）→ 本用例红。
    """
    events = []

    async def fake_process(document_id, job_id):
        if document_id == "doc-1":
            raise RuntimeError("boom")
        events.append(("processed", document_id))

    consumer = PositionFakeConsumer(
        [
            {
                "topic-0": [
                    _msg({"document_id": "doc-1"}, offset=10),
                    _msg({"document_id": "doc-2"}, offset=11),
                ]
            }
        ]
    )
    recorded = []

    async def fake_record(job_id):
        recorded.append(job_id)
        return 1  # 预算未尽

    with pytest.raises(RedeliveryHalt) as ei:
        await run_consume_loop(
            consumer,
            process=fake_process,
            touch=lambda: events.append("beat"),
            record_attempt=fake_record,
        )

    # doc-1 失败：记账一次、零提交、后续消息未被处理（重启后整批重投）
    assert recorded == [None]
    assert events == ["beat"]
    assert consumer.commits == [], "失败批首不得有任何提交"
    assert consumer.committed == {}
    assert "no offset committed in this process run" in str(ei.value)


@pytest.mark.asyncio
async def test_failed_processing_false_at_batch_head_halts_when_budget_remains():
    """#30 核心用例：process 返回 False（修前被忽略照样 commit）→ 不提交 + 停住。

    复现对比（repro_30_baseline.txt）：修前注入 False → consumer.commits == 1。

    变异点：`if processed is False` 判断去掉（恢复"不看返回值直接 commit"）→ 红。
    """
    calls = []

    async def fake_process(document_id, job_id):
        calls.append((document_id, job_id))
        return False  # 文档级失败：doc/job 已在 processor 内落 FAILED

    consumer = PositionFakeConsumer(
        [{"topic-0": [_msg({"document_id": "doc-bad", "job_id": 7}, offset=7)]}]
    )

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
    assert consumer.commits == []
    assert "doc-bad" in str(ei.value)
    assert "no offset committed in this process run" in str(ei.value)


@pytest.mark.asyncio
async def test_failed_processing_terminal_commit_after_budget_exhausted():
    """#30：重试预算耗尽 → 按条终局提交（死信语义），循环继续处理后续消息。"""
    events = []

    async def fake_process(document_id, job_id):
        if document_id == "doc-bad":
            return False
        events.append(("processed", document_id))
        return True

    consumer = PositionFakeConsumer(
        [
            {
                "topic-0": [
                    _msg({"document_id": "doc-bad", "job_id": 7}, offset=7),
                    _msg({"document_id": "doc-ok", "job_id": 8}, offset=8),
                ]
            }
        ]
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

    # 毒消息终局提交后循环继续：doc-ok 处理并提交；两次都是按条显式提交
    assert events == [("processed", "doc-ok")]
    assert _all_explicit(consumer)
    assert consumer.commits and len(consumer.commits) == 2
    assert consumer.committed == {("topic-0", 0): 9}


@pytest.mark.asyncio
async def test_multi_message_batch_failure_redelivers_failed_and_tail():
    """[o100 成功 → o101 失败 → o102 未处理]：按条提交 → committed 停在 100+1；
    o101 重投、o102 不丢（评审 B1 要求的多消息用例）。

    修前（无参 commit）：o100 的提交把 getmany 已推进到的 position（103）一并
    提交——o101 永不重投、o102 被静默丢弃（doc 卡 PENDING/job 卡 QUEUED），
    而 halt 文本还声称"未提交"。

    变异点：commit 改回无参 → committed 变成 103、重启重投集为空 → 本用例红。
    """
    calls = []

    async def fake_process(document_id, job_id):
        calls.append(document_id)
        return document_id != "doc-101"

    async def fake_record(job_id):
        return 1  # 预算未尽

    records = [
        _msg({"document_id": "doc-100", "job_id": 100}, offset=100),
        _msg({"document_id": "doc-101", "job_id": 101}, offset=101),
        _msg({"document_id": "doc-102", "job_id": 102}, offset=102),
    ]
    consumer = PositionFakeConsumer([{"topic-0": records}], start_offset=100)

    with pytest.raises(RedeliveryHalt) as ei:
        await run_consume_loop(
            consumer,
            process=fake_process,
            touch=lambda: None,
            record_attempt=fake_record,
        )

    # 失败即停：o102 不得在本轮被处理；提交只到"最后一条成功"的下一格
    assert calls == ["doc-100", "doc-101"], "失败即停：批尾 o102 不得在本轮处理"
    assert _all_explicit(consumer)
    assert consumer.committed == {("topic-0", 0): 101}, (
        "必须只提交到最后成功点 100+1（修前无参提交会到 103）"
    )
    assert "last committed offset = 100" in str(ei.value), (
        "halt 文本必须与事实对齐（修前谎报 offset NOT committed）"
    )

    # 重启模拟：Kafka 从已提交位点重投 —— 重投集必须 = 批内未提交的全部
    resume_from = consumer.committed[("topic-0", 0)]
    redelivered = [m for m in records if m.offset >= resume_from]
    assert [m.value["document_id"] for m in redelivered] == ["doc-101", "doc-102"], (
        "o101 必须重投、o102 不得被静默丢弃"
    )

    resumed_calls = []

    async def ok_process(document_id, job_id):
        resumed_calls.append(document_id)
        return True

    restarted = PositionFakeConsumer(
        [{"topic-0": redelivered}], start_offset=resume_from
    )
    with pytest.raises(_Stop):
        await run_consume_loop(restarted, process=ok_process, touch=lambda: None)

    assert resumed_calls == ["doc-101", "doc-102"], "重启后两条都须被重新投递并处理"
    assert restarted.committed == {("topic-0", 0): 103}


@pytest.mark.asyncio
async def test_success_returns_commit_explicitly():
    """回归对照：正常 True 返回按条显式提交（不触发任何失败路径）。"""
    consumer = PositionFakeConsumer(
        [{"topic-0": [_msg({"document_id": "doc-1"}, offset=5)]}]
    )

    async def fake_process(document_id, job_id):
        return True

    with pytest.raises(_Stop):
        await run_consume_loop(consumer, process=fake_process, touch=lambda: None)

    assert _all_explicit(consumer)
    assert consumer.committed == {("topic-0", 0): 6}


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
    consumer = PositionFakeConsumer([RuntimeError("kafka down")])

    with pytest.raises(RuntimeError, match="kafka down"):
        await run_consume_loop(consumer, touch=lambda: None)

    assert consumer.commits == []
