"""headless 入口：一次性执行 agent 查询后退出（issue #479 §5.5）。

- ``--dry-run``：只校验参数与（已加载的）配置，stdout ``{"status":"ready"}``，
  exit 0；不连 DB、不调用 LLM、不启动 HTTP。
- 非 dry-run：调用 ``AgentService.chat``（async generator），累加 chunk 内容，
  正常结束输出单行 ``{"task_id":..., "status":"completed", "answer":...}``；
  error 事件或异常 → stderr 脱敏错误 + exit 1。
- 缺 ``--query/--user-id/--org-id`` 或 id 非正整数 → exit 2。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.core.log_safety import log_safe


def _validate_args(args: argparse.Namespace) -> tuple[str, int, int] | None:
    missing = [
        name
        for name, value in (
            ("--query", args.query),
            ("--user-id", args.user_id),
            ("--org-id", args.org_id),
        )
        if value is None
    ]
    if missing:
        print(f"headless 缺少必填参数: {', '.join(missing)}", file=sys.stderr)
        return None
    if args.user_id <= 0 or args.org_id <= 0:
        print("--user-id / --org-id 必须是正整数", file=sys.stderr)
        return None
    return args.query, args.user_id, args.org_id


def run(args: argparse.Namespace) -> int:
    parsed = _validate_args(args)
    if parsed is None:
        return 2
    query, user_id, org_id = parsed

    if args.dry_run:
        print(json.dumps({"status": "ready"}, ensure_ascii=True))
        return 0

    return asyncio.run(_run_chat(query, user_id, org_id))


async def _run_chat(query: str, user_id: int, org_id: int) -> int:
    from app.agent.service import agent_service

    task_id = ""
    chunks: list[str] = []
    try:
        async for event in agent_service.chat(
            query=query,
            history=[],
            organization_id=org_id,
            user_id=user_id,
            session_id=None,
            config=None,
            referenced_document_ids=None,
        ):
            event_task_id = getattr(event, "task_id", "")
            if event_task_id:
                task_id = event_task_id
            if event.type == "chunk":
                chunks.append(event.content or "")
            elif event.type == "error":
                print(log_safe(event.content or "agent error"), file=sys.stderr)
                return 1
    except Exception as exc:  # noqa: BLE001 - headless 必须把失败如实映射为 exit 1
        print(log_safe(exc), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {"task_id": task_id, "status": "completed", "answer": "".join(chunks)},
            ensure_ascii=True,
        )
    )
    return 0
