"""内置 hook 集合（issue #474 本期只提供一个审计示例）。"""

from app.agent.hooks.builtin.audit_tool_calls import handler

__all__ = ["handler"]
