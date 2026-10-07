"""Subagent delegation tool — 把 `subagent.py` 的委派能力接成 LLM 工具（issue #199）。

对接前的状态：`delegate_task` / `run_parallel_subtasks` 是**零调用者**的孤岛——
子循环、受限工具集、3 并发上限都写好了，但没有任何入口。本模块把它注册进
registry，主循环的 Executor 即可在工具列表里看到 `delegate_subtask` 并按需调用。

设计要点（对齐 #199 验收标准：真实调用 / 上下文正确 / 结果回传 / 错误传播 /
并发上限 / 可关联）：
- **递归防护**：本工具 tag 为 "delegation"；子代理工具集限定 search/analysis
  （`subagent.SUBAGENT_ALLOWED_TAGS`），因此子代理看不到本工具，不会无限套娃。
- **并发上限**：任务数 ≤3（与 `run_parallel_subtasks` 的并发上限一致）。
- **超时**：registry 层 `timeout=600s`（默认 30s 会把跑分钟级的子代理掐死）；
  每个子任务另有 `PER_TASK_TIMEOUT` 墙钟上限，超时以标注形式进入该条结果。
- **错误传播**：单个子任务异常 → 该条结果标 `[Error: …]`（不拖垮整批）；
  整体配置错误（无 LLM 客户端等）→ `ToolResult.fail`（走 registry 的 tool_error 路径）。
- **结果回传**：每条结论截断到 `RESULT_SNIPPET_LIMIT` 字符，防止父代理上下文被压爆；
  汇总时按任务顺序编号，父代理可追溯每条结论对应哪个子任务。
"""
import logging
import time

from app.agent.registry import ToolResult, register_tool

logger = logging.getLogger(__name__)

MAX_TASKS = 3
PER_TASK_TIMEOUT = 300.0
RESULT_SNIPPET_LIMIT = 1200


@register_tool(
    name="delegate_subtask",
    description=(
        "把 1-3 个**互相独立**的子任务委派给子代理并行执行。子代理有独立上下文与"
        "受限工具集（检索/分析类），完成后各自返回简洁结论。适用于可拆分、互不依赖的"
        "多步子任务（例如分别查清几份材料后再汇总）。注意：子任务之间不共享状态，"
        "也不要把有先后依赖的步骤放进同一次调用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "子任务列表，1-3 个；每个用一句话说清目标与期望产出",
                "minItems": 1,
                "maxItems": 3,
            },
            "context": {
                "type": "string",
                "description": "可选的背景信息，会被原样交给每个子代理",
                "default": "",
            },
        },
        "required": ["tasks"],
    },
    tags=["delegation"],
    timeout=600.0,
)
async def delegate_subtask(
    tasks: list[str],
    context: str = "",
    organization_id: int | None = None,
) -> str | ToolResult:
    if not tasks:
        return ToolResult.fail(code="validation_error", message="tasks 不能为空")
    if len(tasks) > MAX_TASKS:
        return ToolResult.fail(
            code="validation_error",
            message=f"最多 {MAX_TASKS} 个子任务（与子代理并发上限一致），收到 {len(tasks)} 个",
        )

    try:
        from app.dependencies import get_rag_pipeline

        client = get_rag_pipeline().openai_client
    except Exception as e:
        client = None
        logger.warning("[DELEGATE] 获取 LLM 客户端失败: %s", e)
    if client is None:
        return ToolResult.fail(
            code="invalid_configuration",
            message="LLM 客户端未配置，无法委派子任务",
        )

    from app.agent.subagent import run_parallel_subtasks

    start = time.perf_counter()
    results = await run_parallel_subtasks(
        client=client,
        tasks=tasks,
        organization_id=organization_id or 1,
        parent_context=context,
        per_task_timeout=PER_TASK_TIMEOUT,
    )
    elapsed = time.perf_counter() - start

    timed_out = sum(1 for r in results if r.startswith("[子任务超时"))
    failed = sum(1 for r in results if r.startswith("[Error"))
    logger.info(
        "[DELEGATE] 子任务完成 %d 个（超时 %d / 失败 %d）| 用时 %.1fs",
        len(results),
        timed_out,
        failed,
        elapsed,
    )

    sections = []
    for i, (task, result) in enumerate(zip(tasks, results, strict=True), 1):
        snippet = result[:RESULT_SNIPPET_LIMIT]
        if len(result) > RESULT_SNIPPET_LIMIT:
            snippet += "…（已截断）"
        sections.append(f"【子任务{i}】{task}\n{snippet}")
    return "\n\n".join(sections)
