"""JSON-RPC 传输与分发（issue #480 §5.3）。

- :mod:`app.rpc.dispatcher`：dispatcher（id 表/并发/双向/取消）+ 8 个方法；
- :mod:`app.rpc.server`：stdio NDJSON 循环（``--profile sdk``）；
- :mod:`app.rpc.http`：``POST /api/v1/rpc``（仅 web profile 的 agent bundle，
  ``RPC_HTTP_ENABLED=true`` 时可用）。
"""

from app.rpc.dispatcher import Dispatcher, RPCError, RPCService

__all__ = ["Dispatcher", "RPCError", "RPCService"]
