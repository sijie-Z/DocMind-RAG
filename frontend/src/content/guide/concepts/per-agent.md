PER 是 DocMind Agent 的核心工作方式：先规划（Plan），再执行（Execute），最后反思（Reflect）。它不是一次性的问答，而是一个带检查点、可暂停、可回放的多步循环。

## 三个阶段

- **规划（Planner）**：把目标拆成步骤 DAG，标注依赖关系、可并行分组、工具提示、风险级别与重试策略。
- **执行（Executor）**：逐步调用工具或 LLM，失败时按策略重试；高风险工具在执行前可能触发人工审批（见「工具与人工审批」）。
- **反思（Reflector）**：运行结束后评估结果，给出 `pass`（通过）、`retry`（重试）或 `replan`（重新规划）。反思本身不可用时，会标记为 `unavailable` / `unparsed` 的降级状态，而不是伪装成通过。

编排循环（PERAgentLoop）负责调度三个阶段，并在关键节点保存检查点。因此任务可以暂停等待审批、在刷新页面后恢复现场，也可以在完成后回放。

## 在哪里能看到它

- 对话页的「深度分析」模式会走这条链路，界面会展示计划树、思考过程、反思结果与运行报告。
- Agent 工作台（`/agent`）提供工具开关、审批卡片、任务记录与回放。

## 工具规模

后端静态注册了 33 个工具（14 个核心工具 + 19 个工具包工具）。其中 `execute_python`、`execute_sql`、`mcp_call` 由服务端强制默认关闭（界面开关无法启用），因此默认可用工具是 30 个；通过 MCP 接入的外部工具会在运行时额外注册，数量可能更多。

## 依据（代码事实）

- `backend/app/agent/planner.py`（规划与步骤 DAG）
- `backend/app/agent/executor.py`（执行、重试、审批预检）
- `backend/app/agent/reflector.py`（反思结论与降级状态）
- `backend/app/agent/loop.py`（PERAgentLoop 编排与检查点）
- `backend/app/agent/config.py`（默认禁用工具）
- `backend/app/agent/service.py`（工具规模注释：core 14 + tools 19）
- `backend/app/agent/registry.py`（工具注册表）
