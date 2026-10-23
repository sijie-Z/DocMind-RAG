"""ACP v1（Zed Agent Client Protocol）stdio 适配器（issue #481）。

模块划分：

- :mod:`app.acp.server` — 连接循环、方法分发（复用 ``app.rpc.dispatcher``）、
  身份校验、prompt/审批/取消的驱动逻辑；
- :mod:`app.acp.mapping` — ``AgentEvent`` → ``session/update`` 与审批 →
  ``session/request_permission`` 的纯映射；
- :mod:`app.acp.session` — ACP 会话 ↔ ``ChatSession`` 映射、每会话锁、
  串行 sender、取消旗。

ACP 指 Zed 发起的 **Agent Client Protocol**（agentclientprotocol.com），
与本仓库 ``AGENTS.md`` 的 AI Collaboration Protocol 无关。协议版本锁定 v1；
``session/load`` 明确返回 ``-32601``。
"""

__all__ = ["mapping", "server", "session"]
