"""本机（进程内）文档处理任务：DB 标记 + 启动对账（A3-P0 持久化收口）。

Kafka 不可用时，上传/重建/文件服务改走进程内处理。修前只靠
``asyncio.create_task``：进程在任务完成前退出 = 任务凭空消失，文档永久停在
PENDING/PARSING，且没有任何恢复路径。

现在：
1. 调度前先把 ``knowledge_processing_jobs.local_fallback=True``（状态 QUEUED）
   落库；
2. 进程启动时 ``reconcile_local_jobs`` 按「local_fallback 且状态 ∈
   {QUEUED, PROCESSING}」对账，重新调度；
3. 任务终局由 ``DocumentProcessor._mark_success/_mark_failed`` 写入，终局任务
   不再被回收。

多进程部署下对账可能重复调度同一任务：Redis 可用时由 ``DocumentProcessor``
的文档锁互斥，抢锁失败者按 DEFERRED 退出；Redis 不可用时与既有降级语义一致
（``process`` 不加锁继续处理），此时可能并发重跑——幂等替换保证最终一致。
对账派发的任务若返回 DEFERRED，保持未终局并在下次启动继续回收（有日志）。
"""

import asyncio
import logging

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.worker.doc_processor import DEFERRED, processor

logger = logging.getLogger(__name__)

# 只有未终局的任务需要回收；SUCCESS/FAILED 由处理链写入后即退出对账范围。
ELIGIBLE_JOB_STATUSES = (
    KnowledgeJobStatus.QUEUED,
    KnowledgeJobStatus.PROCESSING,
)

# 持有后台任务引用，避免 asyncio 在任务完成前将其当作垃圾回收
_background_tasks: set[asyncio.Task] = set()


async def _run_local_job(process_call, document_id: str, job_id: int) -> None:
    """执行单个对账任务并如实记录结果（DEFERRED/失败不得静默）。"""
    try:
        outcome = await process_call(document_id, job_id)
    except Exception as e:  # 单任务异常不拖垮其它任务
        logger.error(
            "[LOCAL_JOB] 任务异常 document_id=%s job_id=%s: %s",
            document_id,
            job_id,
            e,
            exc_info=True,
        )
        return
    if outcome is DEFERRED:
        logger.info(
            "[LOCAL_JOB] 任务暂缓（另一处理者持锁）document_id=%s job_id=%s；"
            "保持未终局，待下次启动对账回收",
            document_id,
            job_id,
        )
    elif outcome is False:
        logger.warning(
            "[LOCAL_JOB] 任务失败（文档/任务应已落 FAILED）document_id=%s job_id=%s",
            document_id,
            job_id,
        )


async def reconcile_local_jobs(process=None, session_factory=None) -> int:
    """启动对账：重新调度上次进程退出时未完成的本机任务。

    ``process`` / ``session_factory`` 仅用于测试注入。返回重新调度的任务数。
    单条调度失败不阻断其余任务（各自独立 task，异常在任务内打日志）。
    """
    process_call = process or processor.process
    factory = session_factory or AsyncSessionLocal

    async with factory() as session:
        result = await session.execute(
            select(KnowledgeProcessingJob)
            .where(
                KnowledgeProcessingJob.local_fallback.is_(True),
                KnowledgeProcessingJob.status.in_(ELIGIBLE_JOB_STATUSES),
            )
            .order_by(KnowledgeProcessingJob.created_at)
        )
        jobs = result.scalars().all()

    recovered = 0
    for job in jobs:
        try:
            task = asyncio.create_task(_run_local_job(process_call, job.document_id, job.id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
            recovered += 1
            logger.info(
                "[LOCAL_JOB] 启动对账重新调度 document_id=%s job_id=%s",
                job.document_id,
                job.id,
            )
        except Exception as e:  # pragma: no cover - create_task 失败属运行环境异常
            logger.error(
                "[LOCAL_JOB] 重新调度失败 document_id=%s job_id=%s: %s",
                job.document_id,
                job.id,
                e,
            )
    return recovered
