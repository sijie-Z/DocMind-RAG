"""LLM 调用 JSONL 的纯只读解析（issue #510 MVP）。

复用 :func:`app.agent.llm_call_log.resolve_log_path` 的落点/配置语义，按
``task_id`` / ``request_id`` / 时间范围过滤调用记录。设计约束：

- **只读**：不创建、不追加、不改写任何文件；日志不存在时返回空列表；
- **绝不返回正文**：按固定白名单投影字段，原行若夹带 prompt/completion
  正文字段（或任意额外键）一律丢弃；
- **有界 I/O**：最多读取 ``max_lines`` 个物理行（含坏行/无关行），从构造上
  阻止对无界大文件的整文件扫描；
- **坏行跳过**：非法 JSON / 非对象行 / 过滤时时间戳不可解析的行均跳过。

时间过滤用 UTC aware datetime 比较，与写入端 ``ts`` 的 ISO-8601（带
``+00:00`` 偏移）一致；无时间过滤时不解析时间戳。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.llm_call_log import resolve_log_path

# 只允许这些元数据字段离开解析器（与写入端记录字段集一致）。
_ALLOWED_FIELDS = (
    "ts",
    "task_id",
    "request_id",
    "model",
    "tokens_in",
    "tokens_out",
    "latency_ms",
    "prompt_hash",
    "tool_calls",
    "error",
)

DEFAULT_MAX_LINES = 10_000


def _as_utc(value: datetime) -> datetime:
    """naive 视为 UTC（写入端固定 UTC），aware 按真实偏移换算。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def read_llm_calls(
    *,
    task_id: str | None = None,
    request_id: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    max_lines: int = DEFAULT_MAX_LINES,
) -> list[dict[str, Any]]:
    """读取并过滤 LLM 调用记录。

    - 路径只来自 :func:`resolve_log_path`（配置 + ``task_id`` 分 run 语义），
      本函数不接受调用方传路径；
    - ``since`` / ``until`` 为闭区间边界，naive datetime 按 UTC 解释；
    - ``max_lines`` 是扫描的物理行上限，不是返回条数上限，必须 >= 1。

    返回的新字典只含固定白名单字段，绝不共享原始 JSON 对象，也不含正文。
    """
    if max_lines < 1:
        raise ValueError("max_lines must be >= 1")

    target: Path | None = resolve_log_path(task_id)
    if target is None:
        return []

    since_utc = _as_utc(since) if since is not None else None
    until_utc = _as_utc(until) if until is not None else None
    result: list[dict[str, Any]] = []
    try:
        with open(target, encoding="utf-8") as stream:
            for _ in range(max_lines):
                line = stream.readline()
                if line == "":  # EOF
                    break
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue  # 坏行跳过
                if not isinstance(raw, dict):
                    continue
                if task_id is not None and raw.get("task_id") != task_id:
                    continue
                if request_id is not None and raw.get("request_id") != request_id:
                    continue
                if since_utc is not None or until_utc is not None:
                    ts_text = raw.get("ts")
                    if not isinstance(ts_text, str):
                        continue
                    try:
                        ts = _as_utc(datetime.fromisoformat(ts_text))
                    except ValueError:
                        continue
                    if since_utc is not None and ts < since_utc:
                        continue
                    if until_utc is not None and ts > until_utc:
                        continue
                # 固定字段投影：原行的任何额外键（含正文）都不进入返回值。
                result.append({key: raw[key] for key in _ALLOWED_FIELDS if key in raw})
    except FileNotFoundError:
        return []
    return result
