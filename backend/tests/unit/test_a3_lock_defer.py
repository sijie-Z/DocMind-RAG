"""A3-P0：锁竞争不得假成功——延迟重投（不提交偏移）与锁 TTL 心跳续期。

事故形态：worker 拿不到「文档处理锁」时直接 ``return True``，consumer 据此提交
偏移——持锁者若随后崩溃，这条投递已被当作「已了结」提交掉，文档永久卡
PARSING / job 卡 QUEUED，没有任何重投路径。

修后语义：
- ``processor.process`` 锁竞争返回 ``DEFERRED``（区别于 True/False 的第三种
  终局），不触碰文档状态；
- consumer 收到 ``DEFERRED``：**不提交偏移**、不计重试预算、不触发停驻，
  回卷到该消息偏移做延迟重投；
- 处理期间的锁由心跳按 TTL 续期（token 匹配，避免续到别人的锁）。

变异点：锁竞争改回 ``return True`` → 第一个用例红；consumer 对 DEFERRED
走 commit → 第二个用例红；去掉心跳任务 → 续期接线用例红。
"""

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from aiokafka import AIOKafkaConsumer
from aiokafka.structs import TopicPartition


class _Stop(Exception):
    """哨兵异常：假 consumer 脚本耗尽后抛出，用于结束死循环。"""


def _make_processor():
    from app.worker.doc_processor import DocumentProcessor

    return DocumentProcessor()


class TestLockContentionDeferred:
    @pytest.mark.asyncio
    async def test_process_returns_deferred_and_touches_nothing(self, monkeypatch):
        """拿不到锁：返回 DEFERRED（非 True），不回写状态、不释放他人锁。"""
        from app.worker.doc_processor import DEFERRED

        processor = _make_processor()
        events = []

        async def fake_lock(_document_id):
            events.append("lock")
            return False

        async def fake_load(*_a, **_k):  # pragma: no cover - 不应被调用
            events.append("load")
            return SimpleNamespace()

        async def fake_release(_document_id):  # pragma: no cover - 不应被调用
            events.append("release")

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)

        outcome = await processor.process("doc-1", 7)

        assert outcome is DEFERRED, "锁竞争必须返回第三种终局，而不是假成功 True"
        assert events == ["lock"], "未持锁时不得加载/回写文档，也不得释放别人的锁"


class _FakeConsumer:
    """最小 consumer：记录 commit/seek；批脚本耗尽抛 _Stop。"""

    def __init__(self, batches):
        self._batches = list(batches)
        self.commits = []
        self.seeks = []

    async def getmany(self, timeout_ms=0):
        if not self._batches:
            raise _Stop
        return self._batches.pop(0)

    async def commit(self, offsets=None):
        self.commits.append(offsets)

    def seek(self, tp, offset):
        # 与 aiokafka 0.14 一致：seek 是同步方法（返回 None，不可 await）。
        self.seeks.append((tp, offset))


def _msg(value, offset, topic="topic-0", partition=0):
    return SimpleNamespace(value=value, topic=topic, partition=partition, offset=offset)


class TestConsumerDeferredRedelivery:
    @pytest.mark.asyncio
    async def test_defer_uses_real_aiokafka_sync_seek_contract(self, monkeypatch):
        """独立对抗评审 blocker：aiokafka 的 seek 是同步方法，await 它会崩。

        这里直接对**真实** aiokafka API 做签名断言，并用同步 seek 替身跑
        ``_defer_message``；任何重新加回 await 的改动都会在本用例抛 TypeError。
        """
        import app.worker.consumer as consumer_mod

        assert inspect.iscoroutinefunction(AIOKafkaConsumer.seek) is False, (
            "aiokafka 的 seek 是同步方法；若升级后改为协程，此契约测试需同步更新"
        )

        monkeypatch.setattr(consumer_mod, "DEFER_RETRY_DELAY_SECONDS", 0)

        class _SyncSeekConsumer:
            def __init__(self):
                self.seeks = []

            def seek(self, tp, offset):
                self.seeks.append((tp, offset))

        consumer = _SyncSeekConsumer()
        await consumer_mod._defer_message(
            consumer, _msg({"document_id": "doc-1"}, offset=9)
        )
        assert consumer.seeks == [(TopicPartition("topic-0", 0), 9)]

    @pytest.mark.asyncio
    async def test_deferred_message_not_committed_and_rewound(self, monkeypatch):
        """DEFERRED → 不提交、不记账、不回 halt；回卷到本条偏移待重投。"""
        import app.worker.consumer as consumer_mod
        from app.worker.doc_processor import DEFERRED

        monkeypatch.setattr(consumer_mod, "DEFER_RETRY_DELAY_SECONDS", 0)

        consumer = _FakeConsumer([
            {"topic-0": [
                _msg({"document_id": "doc-1", "job_id": 7}, offset=10),
                _msg({"document_id": "doc-2", "job_id": 8}, offset=11),
            ]}
        ])
        calls = []
        recorded = []

        async def fake_process(document_id, job_id):
            calls.append((document_id, job_id))
            return DEFERRED

        async def fake_record(job_id):  # pragma: no cover - 不得被调用
            recorded.append(job_id)
            return 1

        with pytest.raises(_Stop):  # 循环继续轮询，脚本耗尽退出
            await consumer_mod.run_consume_loop(
                consumer,
                process=fake_process,
                touch=lambda: None,
                record_attempt=fake_record,
            )

        assert calls == [("doc-1", 7)], "同分区后一条本轮不得处理（回卷后会重投）"
        assert consumer.commits == [], "延迟重投的消息绝不能提交偏移"
        assert recorded == [], "锁竞争不是失败，不得消耗重试预算"
        assert consumer.seeks == [(TopicPartition("topic-0", 0), 10)], (
            "必须回卷到该消息偏移，等待持锁者完成后的重投"
        )

    @pytest.mark.asyncio
    async def test_true_still_commits_explicitly(self):
        """回归对照：True 语义不变（按条显式提交）。"""
        import app.worker.consumer as consumer_mod

        consumer = _FakeConsumer([
            {"topic-0": [_msg({"document_id": "doc-1"}, offset=5)]}
        ])

        async def fake_process(document_id, job_id):
            return True

        with pytest.raises(_Stop):
            await consumer_mod.run_consume_loop(
                consumer, process=fake_process, touch=lambda: None
            )

        assert len(consumer.commits) == 1
        assert consumer.commits[0][TopicPartition("topic-0", 0)].offset == 6
        assert consumer.seeks == []


class _FakeRedis:
    def __init__(self, renew_result=1):
        self.calls = []
        self._renew_result = renew_result

    async def set(self, key, value, nx=False, ex=None):
        self.calls.append(("set", key, value, nx, ex))
        return True

    async def eval(self, script, numkeys, *args):
        self.calls.append(("eval", script, numkeys, args))
        return self._renew_result

    async def delete(self, key):
        self.calls.append(("delete", key))
        return 1


class TestLockHeartbeat:
    @pytest.mark.asyncio
    async def test_acquire_stores_token_and_renewal_extends_ttl(self, monkeypatch):
        """续期必须带 token 匹配（只续自己的锁）并返回结果。"""
        import app.core.redis as redis_mod
        import app.worker.doc_processor as dp_mod

        fake = _FakeRedis(renew_result=1)

        async def fake_get_redis():
            return fake

        monkeypatch.setattr(redis_mod, "get_redis", fake_get_redis)

        processor = dp_mod.DocumentProcessor()
        assert await processor._acquire_processing_lock("doc-1") is True
        token = processor._lock_tokens["doc-1"]
        assert token and token != "1", "锁值必须是本 run 的 token，而不是固定 1"

        assert await processor._renew_processing_lock("doc-1") is True
        renewal = [c for c in fake.calls if c[0] == "eval"]
        assert renewal, "续期必须走 compare-and-expire 脚本"
        assert token in renewal[-1][3]
        assert str(dp_mod._LOCK_TTL_SECONDS) in renewal[-1][3]

        await processor._release_processing_lock("doc-1")
        release = [c for c in fake.calls if c[0] == "eval"][-1]
        assert token in release[3], "释放也必须校验 token（不得删掉别人续上的锁）"

    @pytest.mark.asyncio
    async def test_heartbeat_task_renews_until_cancelled(self, monkeypatch):
        import app.worker.doc_processor as dp_mod

        processor = dp_mod.DocumentProcessor()
        monkeypatch.setattr(dp_mod, "_LOCK_HEARTBEAT_INTERVAL_SECONDS", 0.01)
        renewed = []

        async def fake_renew(document_id):
            renewed.append(document_id)
            return True

        monkeypatch.setattr(processor, "_renew_processing_lock", fake_renew)

        task = asyncio.create_task(processor._lock_heartbeat_loop("doc-1"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert renewed and set(renewed) == {"doc-1"}

    @pytest.mark.asyncio
    async def test_renewal_failure_marks_lock_lost(self, monkeypatch):
        """续期发现 token 不再持有锁 → 记入 _lock_lost（供阶段边界中止）。"""
        import app.core.redis as redis_mod
        import app.worker.doc_processor as dp_mod

        fake = _FakeRedis(renew_result=0)

        async def fake_get_redis():
            return fake

        monkeypatch.setattr(redis_mod, "get_redis", fake_get_redis)

        processor = dp_mod.DocumentProcessor()
        assert await processor._acquire_processing_lock("doc-1") is True
        assert await processor._renew_processing_lock("doc-1") is False
        assert "doc-1" in processor._lock_lost

    @pytest.mark.asyncio
    async def test_lock_lost_at_stage_boundary_aborts_without_terminal_writes(
        self, monkeypatch
    ):
        """解析期间锁被接管 → 下一阶段边界中止，返回 DEFERRED，不写终态。"""
        from app.worker.doc_processor import DEFERRED

        processor = _make_processor()
        events = []

        async def fake_lock(_document_id):
            return True

        async def fake_load(document_id, _job_id):
            return SimpleNamespace(
                file_path="f", organization_id="1", id=document_id
            )

        async def fake_parse(*_a, **_k):
            # 模拟心跳在解析期间续期失败（锁已过期并被新 worker 接管）
            processor._lock_lost.add("doc-1")
            return {
                "chunks": [{"chunk_text": "hi", "chunk_index": 0, "chunk_length": 2}]
            }

        async def fake_embeddings(texts):  # pragma: no cover - 应在边界中止
            events.append("embed")
            return [[1.0]]

        async def fake_success(*_a, **_k):
            events.append("success")

        async def fake_failed(*_a, **_k):
            events.append("failed")

        async def fake_release(_document_id):
            events.append("release")

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
        monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
        monkeypatch.setattr(processor, "_get_embeddings", fake_embeddings)
        monkeypatch.setattr(processor, "_mark_success", fake_success)
        monkeypatch.setattr(processor, "_mark_failed", fake_failed)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)

        outcome = await processor.process("doc-1", 7)

        assert outcome is DEFERRED, "锁丢失必须按未了结处理（重投），而不是成功/失败"
        assert events == ["release"], "锁丢失后不得再写成功/失败终态"

    @pytest.mark.asyncio
    async def test_lock_lost_after_success_still_runs_graph_extraction(self, monkeypatch):
        """N2：成功已提交后锁才丢失，不得返回 DEFERRED 永久跳过图谱抽取。"""
        processor = _make_processor()
        events = []

        async def fake_lock(_document_id):
            return True

        async def fake_load(document_id, _job_id):
            return SimpleNamespace(
                file_path="f", organization_id="1", id=document_id
            )

        async def fake_parse(*_a, **_k):
            return {
                "chunks": [{"chunk_text": "hi", "chunk_index": 0, "chunk_length": 2}]
            }

        async def fake_embeddings(texts):
            return [[1.0]]

        async def fake_replace(*_a, **_k):
            pass

        async def fake_success(*_a, **_k):
            # 模拟心跳在成功写入后才把锁丢失标记写进集合
            processor._lock_lost.add("doc-1")
            events.append("success")

        async def fake_graph(*_a, **_k):
            events.append("graph")

        async def fake_release(_document_id):
            events.append("release")

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
        monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
        monkeypatch.setattr(processor, "_get_embeddings", fake_embeddings)
        monkeypatch.setattr(processor, "_replace_document_state", fake_replace)
        monkeypatch.setattr(processor, "_mark_success", fake_success)
        monkeypatch.setattr(processor, "_extract_graph_safely", fake_graph)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)

        outcome = await processor.process("doc-1", 7)

        assert outcome is True
        assert events == ["success", "graph", "release"], (
            "成功已提交后不得因锁丢失返回 DEFERRED；图谱抽取必须照常执行"
        )

    @pytest.mark.asyncio
    async def test_process_starts_and_cancels_heartbeat(self, monkeypatch):
        """process 持锁期间必须挂心跳；结束时必须取消（不留后台任务）。"""
        from app.worker.doc_processor import DEFERRED  # noqa: F401 - 契约导出

        processor = _make_processor()
        state = {"started": False, "cancelled": False}

        async def fake_lock(_document_id):
            return True

        async def fake_load(_document_id, _job_id):
            # 真实处理阶段会 await I/O；这里让渡一次事件循环，心跳任务才能启动
            await asyncio.sleep(0)
            return SimpleNamespace(file_path="f", organization_id="1", id="doc-1")

        async def fake_parse(*_a, **_k):
            return {"chunks": [{"chunk_text": "hi", "chunk_index": 0, "chunk_length": 2}]}

        async def fake_embeddings(texts):
            return [[1.0] for _ in texts]

        async def fake_replace(*_a, **_k):
            pass

        async def fake_success(*_a, **_k):
            pass

        async def fake_graph(*_a, **_k):
            pass

        async def fake_release(_document_id):
            pass

        async def fake_heartbeat(_document_id):
            state["started"] = True
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                state["cancelled"] = True
                raise

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
        monkeypatch.setattr(processor.parser, "parse_document", fake_parse)
        monkeypatch.setattr(processor, "_get_embeddings", fake_embeddings)
        monkeypatch.setattr(processor, "_replace_document_state", fake_replace)
        monkeypatch.setattr(processor, "_mark_success", fake_success)
        monkeypatch.setattr(processor, "_extract_graph_safely", fake_graph)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor, "_lock_heartbeat_loop", fake_heartbeat)

        assert await processor.process("doc-1", 7) is True
        assert state == {"started": True, "cancelled": True}, (
            "持锁期间必须运行心跳；process 退出时必须取消心跳任务"
        )
