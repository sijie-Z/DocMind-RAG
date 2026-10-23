"""示例内置 hook：工具调用审计（issue #474 §7 任务 6）。

它演示 waterfall 的正确写法：``await next(payload)`` 继续链路，并把下游的
最终决策原样返回——**不改变**任何行为，只产出审计日志（敏感字段脱敏）。
若要拦截，可在此返回 ``HookDecision(action="deny", ...)`` 且不调用 next。

启用方式（backend/config/hooks.yaml）::

    hooks:
      - name: audit_tool_calls
        event: tools/pre_execute
        module: app.agent.hooks.builtin.audit_tool_calls
        enabled: true
        priority: 100
        timeout_seconds: 2.0
        failure_policy: fail_open

注意：hook 总开关是 ``HOOKS_ENABLED=true``；默认关闭时本 hook 不会执行。
"""

from __future__ import annotations

import logging

from app.agent.hooks.models import HookContext, HookDecision, redact_arguments

logger = logging.getLogger("docmind.hooks.audit")


async def handler(ctx: HookContext, nxt) -> HookDecision | None:
    """记录每次工具调用（工具名/风险/脱敏参数），再交还控制权给链路。"""
    payload = ctx.payload if isinstance(ctx.payload, dict) else {}
    logger.info(
        "audit tool call tool=%s risk=%s call_id=%s org=%s user=%s arguments=%s",
        payload.get("tool_name", ""),
        payload.get("risk_level", ""),
        payload.get("tool_call_id", ""),
        ctx.organization_id,
        ctx.user_id,
        redact_arguments(payload.get("arguments", {})),
    )
    if nxt is None:  # tools/pre_execute 是 waterfall 事件，next 必然存在；防御式分支
        return HookDecision(action="allow")
    return await nxt(ctx.payload)
