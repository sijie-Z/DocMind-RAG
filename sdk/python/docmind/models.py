"""DocMind SDK 数据模型（issue #480 §5.6）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class DocMindError(Exception):
    """统一错误：``code``（服务端 ``error_code`` 或 HTTP 状态）、``status``（HTTP）。"""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(f"{code}: {message}")


@dataclass
class Session:
    """会话摘要（``sessions.create/list/fork`` 返回）。"""

    id: str
    title: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            id=str(data.get("id", "")),
            title=str(data.get("title") or ""),
            raw=dict(data),
        )


@dataclass
class SessionEvent:
    """SSE 事件：``type`` + 原始 ``data``（``id`` 供 Last-Event-ID 续传）。"""

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    id: str | None = None

    @property
    def content(self) -> str:
        value = self.data.get("content")
        return value if isinstance(value, str) else ""

    @property
    def task_id(self) -> str | None:
        value = self.data.get("task_id") or self.data.get("taskId")
        return str(value) if value else None

    @property
    def approval_id(self) -> str | None:
        """审批流里 ``approval_required`` 事件的审批 id（= 任务 id）。"""
        return self.task_id
