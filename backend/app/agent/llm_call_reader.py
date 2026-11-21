"""LLM 调用 JSONL 的纯只读解析（issue #510 MVP）。

复用 :func:`app.agent.llm_call_log.resolve_log_path` 的落点/配置语义，按
``task_id`` / ``request_id`` / 时间范围过滤调用记录。设计约束：

- **只读**：不创建、不追加、不改写任何文件；日志不存在时返回空列表；
- **绝不返回正文**：按固定白名单投影字段，原行若夹带 prompt/completion
  正文字段（或任意额外键）一律丢弃；
- **有界 I/O**：最多读取 ``max_lines`` 个物理行（含坏行/无关行），达到上限且
  文件未读完时记录固定告警；
- **坏行跳过**：非法 JSON / 非对象行 / 单行超限 / 时间戳不可解析的行均跳过。

时间过滤用 UTC aware datetime 比较，与写入端 ``ts`` 的 ISO-8601（带
``+00:00`` 偏移）一致；无论是否带时间过滤，输出记录的时间戳都必须可解析。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.llm_call_log import resolve_log_path

logger = logging.getLogger(__name__)

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
    # 层级 trace 扩展字段：旧行没有这些键时投影结果也不放键。
    "span_id",
    "parent_span_id",
    "span_type",
    "start_time",
    "end_time",
)

DEFAULT_MAX_LINES = 10_000
# 单行上限：阻止单行超大 JSON 吃掉无界内存（评审 IMPORTANT #1）。
MAX_LINE_BYTES = 1_048_576


def _discard_rest_of_line(stream: Any) -> None:
    """放弃当前超限行的剩余字节；分块读取，精确停在换行之后。"""
    while True:
        chunk = stream.read(MAX_LINE_BYTES)
        if not chunk:
            return
        newline_at = chunk.find(b"\n")
        if newline_at != -1:
            # 回退到换行之后，不能吞掉下一行的开头（文本模式 read 无法精确定位）。
            stream.seek(newline_at + 1 - len(chunk), 1)
            return


def _utc_timestamp(value: object) -> datetime | None:
    """解析写入端 ISO 时间戳；naive 按 UTC，坏值返回 None（统一跳过）。"""
    if not isinstance(value, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(value))
    except ValueError:
        return None


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
        # 二进制有界读取：避免单行/全文件无界内存，并精确处理超限行边界。
        with open(target, "rb") as stream:
            for line_no in range(max_lines):
                raw_line = stream.readline(MAX_LINE_BYTES + 1)
                if raw_line == b"":  # EOF
                    break
                if line_no == max_lines - 1 and stream.read(1):
                    logger.warning("LLM 调用日志达到扫描行数上限，结果可能不完整")
                    stream.seek(-1, 1)
                if len(raw_line) > MAX_LINE_BYTES:
                    logger.warning("LLM 调用日志单行超过上限，已跳过")
                    _discard_rest_of_line(stream)
                    continue
                try:
                    line = raw_line.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except ValueError:
                    continue  # 坏行跳过
                if not isinstance(raw, dict):
                    continue
                # 时间戳统一校验：无时间过滤时坏时间戳也跳过，输出时间戳保证可解析。
                ts = _utc_timestamp(raw.get("ts"))
                if ts is None:
                    continue
                if task_id is not None and raw.get("task_id") != task_id:
                    continue
                if request_id is not None and raw.get("request_id") != request_id:
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


def build_trace_tree(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 ``span_id`` / ``parent_span_id`` 把记录组装成只读层级树。

    语义（MVP，纯函数）：

    - 不修改传入记录，也不输出正文：节点只含白名单字段，``children`` 为新增键；
    - ``parent_span_id`` 指向不存在的 span（或为空）→ 挂到 ``root`` 列表；
    - 自环、互环、环上挂子节点都不会死循环：祖先链一旦回到访问过的节点，
      当前节点就提升为 ``root``；祖先链断在不存在的 span 不算环，子孙照常
      挂到直接 parent；
    - 缺少 ``span_id`` 的记录按孤儿处理；重复 ``span_id`` 时只有第一条作为
      ``parent`` 查找目标，后续记录仍作为独立节点保留（不覆盖、不丢数据）。

    输出节点形如 ``{**projected_record, "children": [...]}``，保持输入顺序。
    """
    nodes: list[dict[str, Any]] = []
    for record in records:
        projected = {key: record[key] for key in _ALLOWED_FIELDS if key in record}
        projected["children"] = []
        nodes.append(projected)

    # 第一遍：记录 index（同一 span_id 只认第一条），避免后续节点指向第二条。
    index: dict[str, int] = {}
    for position, node in enumerate(nodes):
        span_id = node.get("span_id")
        if isinstance(span_id, str) and span_id and span_id not in index:
            index[span_id] = position

    # 第二遍：按 parent 连接。沿 span_id 祖先链找第一个真实祖先；
    # visited 同时防止自环/互环和重复 id 导致的死循环。
    roots: list[dict[str, Any]] = []
    for node in nodes:
        parent_id = node.get("parent_span_id")
        if not isinstance(parent_id, str) or not parent_id:
            roots.append(node)
            continue
        parent_pos = index.get(parent_id)
        if parent_pos is None:
            # 直接 parent 不存在 → 孤儿，挂 root。
            roots.append(node)
            continue
        # 祖先链只用于检测环：若回到自己或出现重复 id，提升为 root 而不链接，
        # 杜绝死循环；祖先链断在上层（parent 自身存在）不算环，仍挂直接 parent。
        own_id = node.get("span_id")
        visited: set[str] = set()
        cursor = parent_id
        while True:
            if cursor == own_id or cursor in visited:
                roots.append(node)
                break
            visited.add(cursor)
            ancestor_pos = index.get(cursor)
            if ancestor_pos is None:
                nodes[parent_pos]["children"].append(node)
                break
            ancestor_parent = nodes[ancestor_pos].get("parent_span_id")
            if not isinstance(ancestor_parent, str) or not ancestor_parent:
                nodes[parent_pos]["children"].append(node)
                break
            cursor = ancestor_parent

    return roots
