# SDK Learnings

本文记录从 OpenAI Agents SDK 与 Claude Agent SDK 中借鉴并落到 DocMind 的
Agent 架构经验。

## OpenAI Agents SDK

- **RunContext**：工具不再靠零散参数拿用户/会话信息，而是接收一个类型化的
  `RunContext`。DocMind 新增 `backend/app/agent/run_context.py`，由 Executor
  构造后传入工具。
- **结构化 ToolResult**：工具统一返回 `ToolResult`，包含 `data/error/meta`。
  DocMind 的 `ToolRegistry` 已经具备该结构，后续应继续补齐 `output_schema`
  校验。
- **Guardrails**：输入/输出护栏作为独立阶段。DocMind 已有 `quality_gate.py`
  与 `reflector.py`，可以继续把“输出护栏”做成可复用 hook。
- **Tracing**：OpenAI Agents SDK 默认全链路追踪。DocMind 已有 Langfuse 与
  OpenTelemetry 接入点，可以继续补齐 tool span。

## Claude Agent SDK

- **Session 持久化**：Claude SDK 强调会话状态可恢复。DocMind 的
  `AgentConfig.save_to_redis` / `AgentMemoryBridge` 已覆盖，API 层也已从 Redis
  加载记忆。
- **工具结果格式与权限提示**：Claude SDK 对敏感工具会先呈现权限确认。DocMind
  目前通过 `requires_auth` / `DEFAULT_DISABLED_TOOLS` 做静态控制，后续可增加
  用户级审批流。
- **事件流**：Claude SDK 的 `query()` 返回结构化事件流。DocMind 的
  `AgentEvent` + SSE 已具备同类形态。

## 已落地改动

- `backend/app/agent/run_context.py`：新增类型化 RunContext。
- `backend/app/agent/executor.py`：工具执行时构造并传入 `run_context`。
- `backend/app/agent/schema_validation.py`：轻量 JSON Schema 校验器。
- `backend/app/agent/registry.py`：工具成功后按 `output_schema` 校验输出，失败返回 `validation_error`。
- `backend/tests/unit/test_run_context.py`：RunContext 序列化与摘要测试。
- `backend/tests/unit/test_schema_validation.py`：schema 校验与 registry 集成测试。

## 下一步

- 将 Guardrails 做成可插拔 hook。
- 为敏感工具增加用户级审批事件。
