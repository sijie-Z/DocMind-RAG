"""LLM 用量归一化（issue #202）——一处提取 usage → 指标 + ExecutionContext。

背景：PER 环的六个 LLM 调用点（planner 流式 / executor 主调 / executor 合成 /
reflector / loop 无工具分支 / reviewer）此前都丢弃了 `response.usage`，于是
`ExecutionContext.total_tokens` **全仓零赋值**——系统答不出"这次任务花了多少
token"（任务级成本核算的地基）。

**统一走这一个入口**，而不是"每个调用点各加一行"：点状方案在新增调用点
（subagent / retry / 流式）时必漏，本模块的存在就是让"漏"在评审时一眼可见。

指标口径与 RAG 侧一致（`rag/pipeline.py` 的 report_tokens）：
`LLM_TOKENS.labels(direction="input"/"output")`。
"""
from app.core.prometheus import LLM_TOKENS


def record_llm_usage(response, ctx=None) -> int:
    """提取 response.usage → Prometheus 指标 + ctx.total_tokens 累计。

    参数:
        response: 任何带 `.usage` 的对象（OpenAI 响应，或包了流式 usage 的
                  `SimpleNamespace(usage=...)`）；无 usage 时计 0、不抛错。
        ctx:      ExecutionContext（可选；鸭子类型，只要求 `add_tokens(int)`）。

    返回本次计入的 token 总数（prompt + completion；无 usage 时 0）——
    供 step 级记账（`complete_step(tokens=...)`）使用。
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    try:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
    except (TypeError, ValueError):
        # 防御：非数值字段（如 int() 转换不了的字符串）→ 计 0 不抛错。
        # 注意（评审 minor-5 更正）：MagicMock 不在此列——int(MagicMock()) == 1，
        # 它会记入极小值（1+1=2）而不是归零；此前的注释声称"MagicMock 计 0"不实。
        return 0
    if prompt:
        LLM_TOKENS.labels(direction="input").inc(prompt)
    if completion:
        LLM_TOKENS.labels(direction="output").inc(completion)
    total = prompt + completion
    if total and ctx is not None:
        ctx.add_tokens(total)
    return total
