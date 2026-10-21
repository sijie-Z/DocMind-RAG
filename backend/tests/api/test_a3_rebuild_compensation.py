"""A3 窄口：Kafka 与进程内调度双失败时，重建任务必须可被启动对账回收。

事故形态（修前）：重建端点先 commit job（``local_fallback=True``）→ 删除 ES
旧索引 → Kafka 发送失败 → 降级 ``asyncio.create_task`` 进程内处理。若
create_task 也抛错（事件循环关闭 / 资源耗尽），旧代码在该分支把 job 收敛为
FAILED：

- 启动对账 ``reconcile_local_jobs`` 只扫描 ``local_fallback=True`` 且状态 ∈
  {QUEUED, PROCESSING} 的任务——FAILED 掉出扫描范围；
- ES 旧索引已删，而文档若原本是 INDEXED，重建入口的条件更新
  （``WHERE status != INDEXED``）不会改写它。

最终形成**永久**「任务 FAILED、文档仍宣称 INDEXED、检索却查不到」的窄窗口。

修复（复用既有 A3-P0 机制，不新增列/不新增状态机）：本地调度失败 ≠ 任务
终局。job 保持 QUEUED + local_fallback=True（任务落库时已写入），错误原因
如实落库；下次进程启动时由 ``reconcile_local_jobs`` 回收重跑（文档锁互斥、
处理幂等）。本用例用真实 sqlite 复现完整窗口，并断言该 job 确实在对账扫描
范围内、会被重新派发。

变异点：把失败分支恢复为「job.status = FAILED + finished_at」→ 本用例红：
- job 不再保持 QUEUED（状态断言失败）；
- 对账查询不再命中该任务，``recovered == 0``（回收断言失败）。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.v1.endpoints.knowledge as knowledge_mod
import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.core.database import Base
from app.exceptions import AppError
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.rag.cache import RetrievalCache, SemanticCache
from app.services.local_processing import reconcile_local_jobs


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    """真实 sqlite 会话工厂（全链：端点直接写库，不用 MagicMock）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'a3_rebuild.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # 端点内局部 import 的 RAG 缓存清理是 Redis 操作，测试中打桩
    monkeypatch.setattr(RetrievalCache, "clear_for_org", AsyncMock(return_value=None))
    monkeypatch.setattr(SemanticCache, "clear_for_org", AsyncMock(return_value=None))
    yield session_factory
    await engine.dispose()


async def _seed_indexed_document(factory):
    async with factory() as session:
        session.add(
            Document(
                id="doc-a3",
                filename="report.pdf",
                file_path="7/report.pdf",
                file_size=10,
                file_type=DocumentType.PDF,
                title="A3 报告",
                status=DocumentStatus.INDEXED,
                organization_id=7,
                uploaded_by=1,
            )
        )
        await session.commit()


class TestRebuildDispatchFailureKeepsJobRecoverable:
    @pytest.mark.asyncio
    async def test_job_stays_queued_and_is_reconciled_after_create_task_failure(
        self, factory, monkeypatch
    ):
        """复现窗口：Kafka 抛错 + create_task 抛错；job 必须留在对账范围内。"""
        await _seed_indexed_document(factory)

        es_delete = AsyncMock(return_value=None)
        monkeypatch.setattr(knowledge_mod.knowledge_service, "delete_es_by_document_id", es_delete)
        monkeypatch.setattr(
            knowledge_mod.kafka_producer,
            "send_message",
            AsyncMock(side_effect=RuntimeError("kafka down")),
        )
        monkeypatch.setattr(knowledge_mod, "create_notification", AsyncMock())

        async with factory() as session:
            doc = await session.get(Document, "doc-a3")
            monkeypatch.setattr(
                knowledge_mod,
                "get_document_for_user",
                AsyncMock(return_value=doc),
            )

            captured_coro = {}

            def _create_task_boom(coro):
                # 不替生产代码关闭协程：端点失败分支必须自行 close()。
                # 断言 cr_frame 为 None 才能判别该收尾是否真的存在（假
                # create_task 代为关闭会让生产代码删掉 close 也依然全绿）。
                captured_coro["obj"] = coro
                raise RuntimeError("no running event loop")

            # patch 作用域只罩住端点调用：reconcile_local_jobs 随后要用真实
            # create_task 重新派发，不能被同一个补丁污染。
            with patch.object(knowledge_mod.asyncio, "create_task", _create_task_boom):
                with pytest.raises(AppError, match="本地处理任务启动失败"):
                    await knowledge_mod.rebuild_document_knowledge(
                        "doc-a3",
                        current_user=SimpleNamespace(id=1),
                        db=session,
                    )

        # 未被 create_task 接管的协程必须由端点失败分支显式关闭
        assert captured_coro["obj"].cr_frame is None, (
            "create_task 失败后必须显式 close() 协程，避免 never awaited 泄漏"
        )

        # 窗口前提：ES 旧索引确已删除、Kafka 确已尝试
        es_delete.assert_awaited_once_with("doc-a3")

        async with factory() as session:
            jobs = (await session.execute(select(KnowledgeProcessingJob))).scalars().all()
        assert len(jobs) == 1, "重建只允许落一个 job"
        job = jobs[0]

        assert job.status == KnowledgeJobStatus.QUEUED, (
            "本地调度失败不是任务终局：job 必须保持 QUEUED 留在启动对账扫描范围"
            "（恢复为 FAILED 会让 ES 已删的重建任务永久失联）"
        )
        assert job.local_fallback is True, "job 必须保留本机兜底标记（启动对账的扫描条件）"
        assert job.finished_at is None, "非终局任务不得写 finished_at"
        assert job.error_message and "本地处理任务启动失败" in job.error_message, (
            "调度失败原因必须如实落库"
        )

        async with factory() as session:
            doc = await session.get(Document, "doc-a3")
        assert doc.status == DocumentStatus.INDEXED, (
            "修复不改写已 INDEXED 终态（并发成功的 run 可能已重新索引；回收重跑会以 PARSING 接管）"
        )

        # 对账回收：真实查询 + 真实派发，fake_process 记录是否真被调用
        processed = []

        async def fake_process(document_id, job_id):
            processed.append((document_id, job_id))
            return True

        recovered = await reconcile_local_jobs(process=fake_process, session_factory=factory)
        await asyncio.sleep(0)  # 让 create_task 派发的协程真正执行

        assert recovered == 1, "启动对账必须命中该 job 并重新派发（修前 job=FAILED 掉出扫描范围）"
        assert processed == [("doc-a3", job.id)], (
            "对账必须把文档与 job 一起重新送进统一处理链（唯一恢复路径）"
        )

    @pytest.mark.asyncio
    async def test_reason_write_failure_does_not_block_reconciliation(self, factory, monkeypatch):
        """真实 DB 层失败下：原始调度错误与通知都不得被掩盖，任务可回收。

        失败注入必须在事务内执行一条**真正失败的 SQL**：若在触碰 DB 之前用
        Python 层 raise，session 没有失败事务，``rollback()`` 是 no-op、ORM
        实例不会过期，测不到「rollback 后访问已过期对象」的真实路径。

        变异点：
        - 去掉失败分支的兜底 try/except：job 原因写入的 DB 异常冒泡，被外层
          包装成 ``AppError("重建知识库失败: ...")`` → 本用例的
          ``match="本地处理任务启动失败"`` 断言红；
        - 把通知参数快照挪回 rollback 之后（直接访问 ``current_user.id`` /
          ``doc.title``）：真实 rollback 已过期 ORM 实例，异步懒加载抛
          MissingGreenlet → 同样红。
        """
        await _seed_indexed_document(factory)

        monkeypatch.setattr(
            knowledge_mod.knowledge_service,
            "delete_es_by_document_id",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            knowledge_mod.kafka_producer,
            "send_message",
            AsyncMock(side_effect=RuntimeError("kafka down")),
        )
        notify = AsyncMock()
        monkeypatch.setattr(knowledge_mod, "create_notification", notify)

        async with factory() as session:
            doc = await session.get(Document, "doc-a3")
            monkeypatch.setattr(
                knowledge_mod,
                "get_document_for_user",
                AsyncMock(return_value=doc),
            )

            original_execute = session.execute

            async def flaky_execute(stmt, *args, **kwargs):
                # 只打断 job 失败原因的 UPDATE；在事务内执行一条真实失败 SQL，
                # 让 rollback 真正发生并过期 session 内的 ORM 实例。
                if "knowledge_processing_jobs" in str(stmt):
                    return await original_execute(text("SELECT * FROM no_such_table"))
                return await original_execute(stmt, *args, **kwargs)

            monkeypatch.setattr(session, "execute", flaky_execute)

            def _create_task_boom(coro):
                coro.close()
                raise RuntimeError("no running event loop")

            with patch.object(knowledge_mod.asyncio, "create_task", _create_task_boom):
                # 原始调度错误必须如实成为 AppError（不得被原因写入失败掩盖）
                with pytest.raises(AppError, match="本地处理任务启动失败"):
                    await knowledge_mod.rebuild_document_knowledge(
                        "doc-a3",
                        current_user=SimpleNamespace(id=1),
                        db=session,
                    )

        # rollback 前快照的通知参数必须可安全使用：通知不能被静默丢弃
        notify.assert_awaited_once()
        assert notify.await_args.kwargs["user_id"] == 1
        assert "A3 报告" in notify.await_args.kwargs["content"]

        async with factory() as session:
            job = (await session.execute(select(KnowledgeProcessingJob))).scalars().one()
        assert job.status == KnowledgeJobStatus.QUEUED
        assert job.local_fallback is True
        assert job.error_message is None, "原因写入失败时不得伪造成功文案"

        processed = []

        async def fake_process(document_id, job_id):
            processed.append((document_id, job_id))
            return True

        recovered = await reconcile_local_jobs(process=fake_process, session_factory=factory)
        await asyncio.sleep(0)

        assert recovered == 1, "原因写入失败的任务仍必须被启动对账回收"
        assert processed == [("doc-a3", job.id)]
