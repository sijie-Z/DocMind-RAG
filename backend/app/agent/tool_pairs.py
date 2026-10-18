"""call/result 配对平衡断言与安全切点（C3a）。

消息形态（OpenAI 兼容）：
- call 载体：assistant 消息，``tool_calls: [{id, type, function: {...}}]``；
- result 载体：``role="tool"`` 消息，``tool_call_id`` 指向 call 的 id。

两个事实：

1. 历史压缩/截断的切点（``messages[:-tail_window]``、``[-HISTORY_CAP:]``）
   可能落在 call 与 result 之间——一半进摘要、一半留尾部，模型就会收到
   孤儿 tool 消息（或只有 call 没有 result 的残缺对话）。切点必须按配对
   边界回退/前移：call 与它的全部 result 必须同侧。
2. 历史本身可能损坏（孤儿 result / 悬空 call）。这类输入必须显式抛
   ``ToolPairingError``，不得静默丢弃或继续拼接。

本模块只做纯函数（无 DB / 无 IO），供 ``context.ContextEngine`` 与
``checkpoint.build_envelope`` 共用。
"""

from __future__ import annotations

from typing import Any


class ToolPairingError(ValueError):
    """历史消息的 tool_call / tool_result 配对损坏（孤儿 result 或悬空 call）。"""


def call_ids_of(msg: Any) -> list[str]:
    """消息携带的 tool_call id 列表（assistant.tool_calls）；非 call 载体 → []。"""
    if not isinstance(msg, dict):
        return []
    raw = msg.get("tool_calls")
    if not isinstance(raw, list):
        return []
    ids: list[str] = []
    for tc in raw:
        if isinstance(tc, dict):
            cid = tc.get("id")
        else:  # 兼容 SDK 对象形态（Pydantic model 等）
            cid = getattr(tc, "id", None)
        if cid:
            ids.append(str(cid))
    return ids


def result_call_id_of(msg: Any) -> str:
    """tool 结果消息指向的 call id；非结果载体 → ""。"""
    if not isinstance(msg, dict) or msg.get("role") != "tool":
        return ""
    cid = msg.get("tool_call_id")
    return str(cid) if cid else ""


def validate_tool_pairs(messages: list[dict[str, Any]]) -> None:
    """校验 call/result 配对完整；损坏即抛 ``ToolPairingError``（不静默）。

    - 孤儿 result：``role="tool"`` 的 tool_call_id 找不到先前登记的 call；
    - 悬空 call：消息结束仍有 call 没等到 result；
    - 无 tool_call_id 的 tool 消息同样视为损坏。
    """
    open_calls: dict[str, int] = {}
    for idx, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            rid = result_call_id_of(msg)
            if not rid:
                raise ToolPairingError(f"tool 消息缺少 tool_call_id（index={idx}）")
            if rid not in open_calls:
                raise ToolPairingError(
                    f"孤儿 tool result: {rid}（index={idx}，无对应 call）"
                )
            open_calls.pop(rid)
            continue
        for cid in call_ids_of(msg):
            open_calls[cid] = idx
    if open_calls:
        missing = ", ".join(sorted(open_calls))
        raise ToolPairingError(f"悬空 tool call（无 result）: {missing}")


def balance_cut(messages: list[dict[str, Any]], cut: int) -> int:
    """把切点调整到配对边界：call 与其全部 result 必须同侧。

    ``cut`` = 旧部（将被压缩）的长度；返回调整后的值（只会 <= 传入值）。
    规则：
    - 尾部首条是 result、其 call 在旧部 → 切点退到该 call 之前（整对进尾部）；
    - 旧部末条是 call、其 result 在尾部 → 切点退到该 call 之前（整对进尾部）。
    每次调整严格减小 cut，必然终止。
    """
    n = len(messages)
    cut = max(0, min(cut, n))
    while 0 < cut < n:
        head = messages[cut]
        rid = result_call_id_of(head)
        if rid:
            owner = None
            for i in range(cut - 1, -1, -1):
                if rid in call_ids_of(messages[i]):
                    owner = i
                    break
            if owner is not None:
                cut = owner
                continue
        tail_results = {
            result_call_id_of(m) for m in messages[cut:] if result_call_id_of(m)
        }
        cids = call_ids_of(messages[cut - 1])
        if cids and any(c in tail_results for c in cids):
            cut -= 1
            continue
        break
    return cut
