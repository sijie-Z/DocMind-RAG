# DocMind Agent 核心审计

> 审计对象：本仓库（`sijie-Z/DocMind-RAG`）的 `develop` 分支，基线 @ `e5b2eb1`。
> 审计方式：纯静态阅读，未运行任何代码。
> 审计人：Explore 子 agent（只读），由监督方落盘。落盘时间：2026-10-06。

---

## 一、现有 Agent 架构地图

### 1.1 有两条并行的执行路径（重要）

| 路径 | 入口 | 状态 |
|---|---|---|
| **生产路径** | `POST /api/v1/agent/chat` → `app/api/v1/endpoints/agent.py:87` → `agent_service.chat` (`app/agent/service.py:45`) → `PERAgentLoop.run` (`app/agent/loop.py:216`) | 唯一挂在 `app.main` 上的 Agent 路由（`app/main.py:572`） |
| **Legacy / benchmark 路径** | `app/agent_api.py` 是**独立 FastAPI app**（`app/agent_api.py:9` 注释 `uvicorn app.agent_api:app --port 8010`）→ `Orchestrator` (`app/agent/orchestrator.py:36`) → `ExecutorAgent` (`app/agent/agents/executor_agent.py`) | **未挂载**。`agent_api` 在 `app/main.py` / `api/v1/router.py` / `run.py` 中零命中 |

`Orchestrator` 的唯一消费者是离线评估 `app/agent/evaluation.py:30,309,475`。

### 1.2 生产路径的层次（自上而下）

1. **API 层 / SSE** — `agent.py:87` `agent_chat()`；`:103` `event_stream()`；`:137` 逐条写 `data: {json}\n\n`；`:162` `StreamingResponse(media_type="text/event-stream")`。安全门 `_merge_disabled_tools` `:42`（服务端强制禁用高危工具，客户端无法解除）。
2. **Service 层** — `service.py:45` `AgentService.chat()`：从 Redis 读 config（`:75`）、拼 `agent_id`（`:81`）、构造 `PERAgentLoop`（`:84`）、事件原样转发（`:101`）、存 config（`:106`）。
3. **PER 主循环** — `loop.py:216` `PERAgentLoop.run()`：
   - Phase 0a 上下文压缩 `:246-250`（`context_engine.fit_async(history)`）
   - Phase 0 记忆召回 `:252-265`
   - Phase 1 规划 `:282-291`（`_plan_once` → `Planner.plan`）
   - Phase 2 执行 `:293-331`
   - Phase 2b 质量门 `:354-393`（`quality_gate_check`）
   - Phase 2c 经验写回 `:395-404`（`_write_experiences`）
   - Phase 3 反思 `:406-477`（含 retry / replan 分支）
   - Phase 3b 对抗审查 `:479-543`（`Reviewer.review`）
   - Phase 4 收尾 `:545-584`
   - 外层 `while True`（`:277`）只为 replan 存在
4. **Plan 数据模型** — `planner.py:93` `PlanStep` / `:119` `Plan`（`is_complete :140`、`get_ready_steps :143`、`progress :134`）
5. **Planner** — `planner.py:159` / `plan()` `:166`。**先走规则模板**：`_try_structured_planning` `:471` → `_build_comparison_plan :492` / `_build_analysis_plan :568` / `_build_research_plan :606`（`:202-209` 注释：实验证明 LLM Planner 不会拆解任务，avg_steps≈1，故改为模板）；模板未命中才走 LLM 流式规划 `:296-393`。
6. **Executor** — `executor.py:64`，`execute()` `:110`（`while not plan.is_complete` `:125`）；串行/并行两模式（`:174` / `:199`）；单步 `_execute_single_step :212`；并行 `_execute_steps_parallel :271`；重试 `_execute_step_with_retry :354`；单次尝试 `_execute_step_once :520`。
7. **Reflector** — `reflector.py:91`，`reflect()` `:104`。快通道 `_quick_pass_check :241`；否则 LLM 判定 `pass/retry/replan` `:155-201`。
8. **Reviewer** — `reviewer.py`（157 行），`loop.py:488` 调用。
9. **Tool 系统** — `registry.py:827` 全局单例 `tool_registry`；`@register_tool` 装饰器 `:830`。
10. **Tool 实现** — `core_tools.py`（14）、`tools/*.py`（16）
11. **记忆** — `memory_bridge.py:27` `AgentMemoryBridge` → `services/memory_service.py:479` `AgentMemorySystem`（ShortTerm/LongTerm/Working/Reflective 四层，`:86/124/312/407`）
12. **经验** — `experience/store.py:32` `ExperienceStore`（Redis + 本地 JSON），`experience/extractor.py`
13. **可观测性** — `observability.py:26` Langfuse；`core/prometheus.py:109-118`；`exec_context.py:58`；`run_report.py:33`
14. **死代码层（已实现但零消费者）** — `subagent.py`、`skills.py`、`curated_skills.py`（仅 API 展示）、`orchestrator.py`、`message.py`、`agents/executor_agent.py`、`escalation.py`、`core/circuit_breaker.py`、`core/trace_logger.py`、`models/agent_task.py`

---

## 二、8 个必答问题

### Q1. PER 循环本体 / 状态 / 续跑 / 上限

- **Planner** `planner.py:159`；**Executor** `executor.py:64`；**Reflector** `reflector.py:91`；**主循环** `loop.py:277`。串联方式 = 每个组件都是 `AsyncGenerator[AgentEvent, None]`，loop 用 `async for ...: yield event` 转发；Plan 通过 `plan_sink` list 回传（`loop.py:281,291,669`）。

- **状态存哪**：
  - **主状态在内存**：`ExecutionContext` 是 `@dataclass`，`loop.py:240` 每次请求新建，`exec_context.py:59` docstring 明写 "Created per query, destroyed after response"。
  - **Working memory 在内存**：`memory_bridge.py:76,147-157,257`，run 作用域由 `_run_id` 隔离。
  - **Redis 只存旁路快照**：`ctx.save()` `exec_context.py:191` → `agent:replay:{task_id}`；Plan → `agent:plan:{plan_id}`（`planner.py:807-838`）；config → `agent:config:{session_id}`；SkillManager → `agent:skills`；Experience → `agent:experience:store`。
  - **DB**：`agent_tasks` 表存在（`models/agent_task.py:76`，migration `004`），但**零运行时读写**。

- **能不能续跑**：**不能**。
  - `Plan.load_plan()`（`planner.py:841`）**全仓无调用者**
  - `ExecutionContext.load()`（`exec_context.py:226`）唯一调用者是 `replay/engine.py:39`，`ReplayEngine` 自身无调用者
  - `models/agent_task.py:7` docstring 自述："This is Phase 1 (data model only). The executor does NOT use it yet."
  - `AgentService.save_session_state` / `load_session_state`（`service.py:173,198`）**无调用者**
  - **结论**：请求中断/进程重启后，本次 run 的 Plan 完成度、step 结果、重试计数全部丢失，只能从头重跑。

- **上限**：
  - 重规划 `MAX_REPLANS_PER_RUN = 1`（`loop.py:53,434,451`）
  - 步数上限 `max_plan_steps = 10`（`config.py:37`，`planner.py:240,728`）
  - 每步重试 `max_retries_per_step = 3`（`config.py:40`）→ `max_attempts = 4`（`executor.py:373`），耗尽后走 `fallback_chain`/`fallback_tool`（`executor.py:448-498`）
  - 单轮工具调用 `max_tool_calls_per_turn = 5`（`config.py:39`，`executor.py:673`）
  - **`max_iterations = 10`（`config.py:38`）是死配置**：loop.py / executor.py 一次都没读它。PER 循环没有迭代上限，真正边界是 `(步数 × 重试次数) × (1 + MAX_REPLANS_PER_RUN)`。
  - **没有全局墙钟 deadline / token 预算**：grep `deadline|wall_clock|total_timeout` 在 `app/agent/**` 只命中 `message.py:135`。

### Q2. 工具注册表

- **数量 30 个**（已剔除 docstring 示例）：`core_tools.py` 14、`code_execution.py` 2、`data_analysis.py` 2、`tools/skills.py` 3、`translation.py` 2、`utility.py` 3、`web_search.py` 2、`mcp_bridge.py:156`、`feishu/bitable.py:30`
- **注册**：`@register_tool`（`registry.py:830`）→ `register()`（`:308`）→ `dict[str, ToolEntry]`（`:293`）。导入即注册（`service.py:27-28` 强制 import）。
- **Schema**：**显式手写 JSON Schema**（`registry.py:181`），`to_openai_schema()` `:202`。**不是 docstring 推导**。可选 `output_schema`（`:187`）在 `:542-552` 做输出校验。
- **调用**：`execute_detailed()` `registry.py:436`；executor 侧 `executor.py:704-713` 用 `asyncio.wait_for` 再包一层。
- **失败处理**：`ToolResult`/`ToolError`（`registry.py:56-159`，23 种 `ToolErrorType`）；post-hook `_builtin_error_mapper_hook` `:669-818` 关键词映射；→ `RetryPolicy`（`retry_policy.py:51-156`）→ `executor.py:379-402` 抖动指数退避。
- **超时**：工具级 `ToolEntry.timeout` 默认 30s（`registry.py:190,526-529`）；executor 再套 `TOOL_TIMEOUT_SECONDS = 30.0`（`executor.py:78`）。
- **限速**：`entry.rate_limit`（`registry.py:512-524`）→ `:419-434`（Redis `incr` + 60s 窗口，**Redis 不可用时 fail-open 放行**）
- **工具级熔断：没有**。`core/circuit_breaker.py:1-20` 自述"未接入生产路径"（issue #106 §4c 已核实）。现有的只有 **Executor 的连续失败熔断** `MAX_CONSECUTIVE_FAILURES = 3`（`executor.py:77,144-159`），作用域单次 run。

### Q3. ContextEngine

- **文件** `context.py:40`。**只有一个调用点** —— `loop.py:248`，对**调用方传入的 history** 做 `fit_async`。**不**管理 system prompt、工具定义、记忆召回、检索结果、step 结果。
- **预算**：`max_context_tokens` 默认 **8000**（`config.py:69`），`tail_window=6`（`loop.py:142`）
- **超了怎么办（三级降级）**：① `_compress_messages_async` `:145` → LLM 摘要 `:190`（`max_tokens=min(300,...)`，只喂最后 20 条），整段换成一条 `role=system` 的 `[Conversation summary]` ② LLM 失败 → `_compress_messages_fast` `:170`：每条截 200 字符 ③ 兜底 `_truncate_messages` `:225`：逐条 token 裁切。**这是"截断/丢弃"，不是压缩。**
- **分层**：**没有**。system prompt 是**字符串拼接**，散在三处、互不知晓对方的 token 占用：
  - `loop.py:749-793` `_build_messages`（仅 tool-less 模式）：system + `memory_context` + `context_docs` 前 5 条（每条约 500 字符 `:768`）+ personality
  - `planner.py:243-274`：`PLANNING_SYSTEM_PROMPT` + 工具描述 + `memory_context` + **经验库文本** + replan 反馈
  - `executor.py:555-563`：`EXECUTION_SYSTEM_PROMPT` + `memory_context`（**每步重新召回一次** `:552`）+ 前置步骤结果
- **Token 估算**：`estimate_tokens` `context.py:24`（tiktoken `cl100k_base`，退化 `len/2.5`）
- **按需加载：没有**。工具定义全量注入（`executor.py:876` 全 30 个）
- **死代码**：`loop.py:44` `MAX_RESULT_TOKENS = 3000` + `loop.py:56` `_smart_truncate()`（按工具类型分别保留结构）**定义了但全仓零调用**。实际生效的是散落硬编码：`executor.py:243 [:2000]`、`:815 [:1500]`、`:914 [:200]`

### Q4. 技能与经验

**（a）仓库根 `skills/`**：只有 1 个 —— `skills/ai-podcast/SKILL.md` + 2 个 reference。
- 加载器 `curated_skills.py:93` `CuratedSkillRegistry`（frontmatter 解析 `:144-173`、reference 懒加载缓存 `:221`）
- **运行时没有读用**。`curated_skill_registry` 全仓 6 处全在 `api/v1/endpoints/curated_skills.py`（只读展示 API）
- `curated_skills.py:61` 在 prompt 里写 "调用 load_skill_reference 函数获取"，但**这个工具函数不存在**（grep `load_skill_reference` 只命中这一行字符串）
- **结论：`skills/` 是文件摆设，仅通过 REST 能被前端看到，从不进入任何 LLM 提示词。**

**（b）SkillManager（程序性记忆）** `skills.py:62`，Redis `agent:skills`
- `learn_from_plan_success` `:192` / `get_tool_hints_for_query` `:181` / `record_success` `:152` / `record_failure` `:160` **零调用者**
- `skill_manager` 单例唯一外部引用是 `GET /agent/skills` 只读列表
- **结论：学习闭环是断的 —— 永远学不到新技能，也永远不会被规划器读取。**

**（c）Experience** `experience/store.py:32`
- **存储**：**不是 DB，不是向量库**。Redis `agent:experience:store`（30 天 TTL `:318`）+ 本地 JSON `benchmark/experiences.json`（`:28`）。检索是**关键词/字面重叠打分**（`_score_relevance :176-215`：场景精确 +3.0、英文词重叠 ×2.0、中文 bigram ×1.5），负迁移软惩罚（`:162-169`）
- **写入**：`loop.py:708-747` `_write_experiences()`（2026-10 才补上，`:395-400` 注释自述此前回路是断的）。失败步骤 → `extract_from_runtime_error`；成功门槛**刻意极高** —— 必须质检**全 A**（`:738`）
- **读回影响决策**：`planner.py:253-262` —— 把 `format_for_planner(query, top_k=3)` 追加进 **Planner 的 system prompt**。**只影响规划，不影响执行器/反思器的单步决策。**

### Q5. 子 agent / 任务委派

- **文件存在**：`subagent.py` —— `delegate_task :24`（复用 `PERAgentLoop`，关掉 planning/reflection/memory/thinking，工具限 `SUBAGENT_ALLOWED_TAGS = ["search","analysis"]`），`run_parallel_subtasks :73`，并发上限 `max_concurrency=3`（`:98-101`）
- **实现方式**：**同进程协程**（`asyncio.gather`），非独立进程/服务
- **超时：没有**（`delegate_task` 无 timeout，`_gather_with_concurrency` 无 `wait_for`）
- **结果回传**：`run_parallel_subtasks :84-96` 只把 `event.type == "chunk"` 拼成字符串 —— **无结构化摘要、无 token/步数回传、无失败隔离**
- **是否接线**：grep 全仓（backend + frontend）**除 `agent/__init__.py:16` 一句 docstring 外零命中**
- **结论：子 agent 能力是完全未接线的死代码。**

### Q6. 沙箱 / 代码执行

- **工具**：`execute_python`（`tools/code_execution.py:263`）、`execute_sql`（`:433`）。二者都在 `DEFAULT_DISABLED_TOOLS`（`config.py:18-22`）里**默认禁用**，且 `agent.py:42-48` `_merge_disabled_tools` 服务端强制合并，**客户端无法解除**
- **模式选择**：`SANDBOX_MODE` 默认 `"auto"`（`core/config/ai.py:108`），`code_execution.py:288-291`：探测 Docker 可用性，可用走 docker，否则**静默回落 AST 检查 + 进程内 exec**
- **Docker 隔离**（`docker_sandbox.py:41-111`）：`--network=none`（默认）、`--memory=256m`、`--cpus=1`、`--read-only` + `--tmpfs=/tmp:exec`、`--security-opt=no-new-privileges`、只挂脚本为 `:ro`、30s 超时（`asyncio.wait_for` + `proc.kill()`）、镜像默认 `python:3.11-slim`。**但没有磁盘配额、没有 PID 限制、没有镜像白名单校验。**
- **AST 兜底**（`code_execution.py:215-260`）：禁 import `:243`、禁 `eval/exec/open/getattr/...` `:227`、**禁一切下划线开头的属性访问** `:251`、禁 `format/format_map/get_field/vformat` `:235`（防 `Formatter` 逃逸）、字符串字面量禁 dunder `:237,257`；代码长度上限 5000 `:223`；`run_in_executor` + `wait_for(timeout=10.0)` `:337-340`；输出截 10000 `:346`
  - **已知缺口**：线程超时**杀不掉死循环线程**（`wait_for` 只是不再等，线程继续占 CPU）
- **SQL 沙箱**（`code_execution.py:453-502`）：四层校验 `_is_safe_select_sql :105`（仅 SELECT、黑名单含 union/with/into/information_schema、禁子查询 `:132`、禁注释、表白名单 `:35`、列白名单 `:50`、表达式里每个标识符必须命中列白名单 `:199-210`、函数白名单 `:95`）；强制租户注入 `_inject_org_filter :363-430`（含 OR 逃逸防护 `:398`）；无 `organization_id` 时 **fail-closed** 拒绝 `:463-464`；强制 `LIMIT 100` `:471-473`；5s 超时 `:483-486`
- `tools/mcp_bridge.py:156` `mcp_call`（注释标为高危），默认禁用

### Q7. 可观测性

**记录了什么**：
1. `ExecutionContext`（`exec_context.py:58`）—— `steps`（`StepRecord`：step_id/description/tool/status/result_summary/error/duration_ms/**tokens_used**）、`findings`、`decisions`、`failures`、`duration_ms`、`total_tokens`
2. `run_report`（`run_report.py:33`）—— 只读投影，自述 "changes NO runtime behavior"，4 块：plan reasoning / steps / tool_calls / evaluation（`clean_success :74`、`fatal_issues`、`review_recommendation`、`grade_hint :109`）
3. Prometheus（`core/prometheus.py:109-118`）：`agent_planning_total`、`agent_planning_latency_seconds`、`agent_execution_steps_total`、`agent_tool_calls_total{tool,result}`、`agent_tool_latency_seconds`、`agent_reflection_decisions_total{decision}`、`agent_memory_recalls_total{result}`、`agent_feedback_total{feedback_type}`
4. Langfuse（`observability.py:26`）：planner span `planner.py:195`、executor step span + llm generation span（带 `usage=response.usage`）`executor.py:539,587,639`、reflection span `reflector.py:123`、tool span `registry.py:464`、memory recall span `memory_bridge.py:110`。**未配置 key 时整体禁用**
5. 工具调用日志：`ToolRegistry._log` **进程内 ring buffer 5000 条**（`registry.py:298-299,624-640`）。**不落盘、不跨进程**
6. `ExecutionContext.save()` 写 Redis `agent:replay:{task_id}`，TTL 7 天

**能不能回答三个问题**：

| 问题 | 能否 | 证据 |
|---|---|---|
| **这次任务花了多少 token？** | **不能** | `ctx.total_tokens`（`exec_context.py:82`）初始化为 0，**全仓零赋值点**；`StepRecord.tokens_used` 传参处从不传（`executor.py:795-802`）；`response.usage` 只在 `executor.py:639` 进 Langfuse，**不进 ctx/日志/指标**；`LLM_TOKENS` 指标只服务 RAG（`rag/pipeline.py:286-287`）。`monitoring.py:611-623` 的 `total_tokens_today` 是**另一条链路**（chat_message 表） |
| **走了几步？** | **能** | `ctx.completed_steps` + `run_report` 的 `steps` 数组；`agent_execution_steps_total`；`plan.completed_steps/total_steps` |
| **哪一步开始错的？** | **部分能** | `run_report` 有 per-step `status/error` + `failures` 列表 + `decisions` 时间序。**但没有全局时间轴**：`StepRecord` 只有 `duration_ms` 没有墙钟时间戳；`Decision` 也没有时间戳。多步并行时无法判定先后 |

**其他观测缺口**：`core/trace_logger.py:86` `AgentTracer` / `:172` `TraceExporter`（含 JSONL 落盘）**只被未挂载的 `agent_api.py:54,60,227` 使用**；`agent/tracing.py:138` `TraceStore` 是**内存 ring buffer（max 2000）**，只被 `orchestrator.py:251` / `executor_agent.py:253` / `evaluation.py` 使用，生产路径不写。

### Q8. 前端 ↔ 后端接口

- **SSE 流式**。`POST /api/v1/agent/chat` → `StreamingResponse(media_type="text/event-stream")`（`agent.py:162-170`，带 `X-Accel-Buffering: no`）。每条 `data: {json}\n\n`。**不是 WebSocket**
- **事件类型**：`events.py:21-35` 定义 13 种 —— `thinking / plan_start / plan_step / plan_complete / tool_call / tool_result / tool_error / reflection / chunk / done / error / execution_step_result / run_report`。另有 `execution_context` 事件（`loop.py:570`）**不在 Literal 里**（类型标注与实现不一致）
- **能不能中断**：**客户端能，服务端无 API**。前端 `api/agent.ts:40,113` 每调用一个 `AbortController`；服务端 grep `cancel|interrupt|/stop` 在 `agent.py` **零命中**。中断 = 客户端断开 TCP，Starlette 取消 SSE 生成器。loop 有局部保护（`loop.py:314-331` `finally`；`executor.py:348-352` `finally` 取消孤儿任务，`:307-310` 注释说明是为防客户端断连后步骤继续跑）。**但因为没有 checkpoint，中断 = 本次 run 全部工作作废。**
- **会话持久化**：`_save_session_interaction` `agent.py:579` 只把 **user query + 最终 answer** 写 `chat_messages` 表，**不存 Plan/step/中间状态**

---

## 三、能力清单表

| 能力项 | 状态 | `file:line` | 备注 |
|---|---|---|---|
| PER 三阶段循环 | **有** | `loop.py:216,277` | 生产路径唯一入口 |
| 有界重规划 | **有** | `loop.py:53,434,451` | 上限硬编码 1，不可配置 |
| Plan DAG + 依赖排序 | **有** | `planner.py:143,775` | 有环时直接清空所有依赖 `:802-805` |
| 并行步骤执行 | **有** | `executor.py:199,271-352` | `asyncio.create_task`，无并发上限 |
| 循环迭代上限 | **半成品** | `config.py:38` | 死配置 |
| 全局墙钟/token 预算 | **没有** | — | 循环可跑很久 |
| 状态持久化 | **半成品** | `exec_context.py:191` | **只写不读**，无 resume |
| Checkpoint / 断点续跑 | **没有** | `planner.py:841`；`models/agent_task.py:7` | 表已建（migration 004），零读写 |
| 工具注册表 | **有** | `registry.py:282,830` | 30 个工具 |
| 工具 JSON Schema | **有** | `registry.py:202` | 手写 |
| 工具超时 | **有** | `registry.py:526-529`；`executor.py:78` | 双层 30s |
| 工具重试 + 抖动退避 | **有** | `executor.py:354-442`；`retry_policy.py:38` | 23 种错误码 → 策略表 |
| 工具 fallback chain | **有** | `executor.py:448-498` | — |
| 工具限速 | **有（fail-open）** | `registry.py:419-434,512-524` | Redis 挂了就放行 |
| 工具级熔断 | **没有** | `core/circuit_breaker.py:1-20` 自述未接线 | 只有 run 内 `MAX_CONSECUTIVE_FAILURES=3` |
| ContextEngine | **半成品** | `context.py:40`，唯一调用点 `loop.py:248` | 只管调用方传入的 history |
| 上下文分层预算 | **没有** | — | system prompt 散在 3 处各自拼接 |
| 上下文压缩（LLM 摘要） | **有** | `context.py:145,190` | — |
| Micro-compact / 工具定义按需加载 | **没有** | `executor.py:876` | 全量注入 30 个 |
| 工具结果截断 | **半成品** | `loop.py:56` `_smart_truncate` **零调用** | 精心写的分层截断器是死代码 |
| `skills/` 被运行时读用 | **没有** | `curated_skills.py:266` | 只有 1 个 skill（ai-podcast） |
| Skill 程序性记忆学习闭环 | **没有** | `skills.py:181,192` 零调用者 | `GET /agent/skills` 永远是空列表 |
| Experience 存储 | **有** | `experience/store.py:32` | Redis + JSON，**非 DB / 非向量库** |
| Experience 写入 | **有**（新接线） | `loop.py:402,708-747` | 成功门槛=质检全 A |
| Experience 读回影响决策 | **有（仅规划）** | `planner.py:258` | 只进 Planner system prompt |
| 负迁移防护 | **有** | `store.py:162-169` | 软惩罚非硬过滤 |
| 子 agent 委派 | **没有** | `subagent.py:24,73` **零调用者** | — |
| 并行子任务并发上限 | **有（未接线）** | `subagent.py:100` | max_concurrency=3 |
| 子 agent 超时 | **没有** | `subagent.py:73-101` | 无 `wait_for` |
| Docker 沙箱 | **有（默认关）** | `docker_sandbox.py:41-111` | 网络/内存/CPU/只读/超时全限 |
| AST 沙箱兜底 | **有（默认关）** | `code_execution.py:215-360` | 线程超时杀不死死循环 |
| SQL 只读白名单 + 租户强制 | **有（默认关）** | `code_execution.py:105,363,453` | 四层校验，fail-closed |
| 高危工具服务端强制禁用 | **有** | `agent.py:42-48`；`config.py:18` | 客户端无法解除 |
| Langfuse tracing | **有（可选）** | `observability.py:26` | 未配 key 即禁用 |
| Prometheus 指标 | **有** | `prometheus.py:109-118` | 8 个 Agent 指标 |
| 工具调用日志 | **有（内存）** | `registry.py:298,624-640` | ring buffer 5000，不落盘 |
| per-step 时间戳 | **没有** | `exec_context.py:23-32` | 无墙钟时间 |
| **Token 核算** | **没有** | `exec_context.py:82` | 三个问题里唯一答不出的 |
| Run Report | **有** | `run_report.py:33` | 只读投影，含 `clean_success` |
| 离线评估 harness | **有（独立 app）** | `evaluation.py:1-20`；`agent_api.py:9` | 未挂载到 main |
| 人工升级（escalation） | **没有** | `escalation.py:51` 仅 `executor.py:26,102` 实例化，**从不查询** | 死配置 |
| Orchestrator 多 agent 协调 | **没有（生产）** | `orchestrator.py:36` | 仅 `evaluation.py` 用 |
| SSE 中断（客户端） | **有** | `frontend/src/api/agent.ts:40,113` | — |
| 服务端取消 API | **没有** | grep `cancel\|interrupt\|/stop` in `agent.py` 零命中 | — |

---

## 四、与 harness 模式的差距表（重点）

| # | Harness 能力 | DocMind 现状 | 差距 | 代价 |
|---|---|---|---|---|
| 1 | **Agent teams 与子 agent 委派** | `subagent.py:24,73` 已写好但**零调用者** | ① 无触发决策（何时该委派由谁判）② 无结构化结果回传（`:92-96` 只拼 chunk 文本）③ 无超时/隔离 ④ 无父子 token 归集 | **大**。需把"委派"接进 Planner（规则模板 + LLM 分支都要能产出 `delegate` 型 step）、定义 `SubtaskResult`、给 `executor._execute_step_once` 加委派分支、给 `_gather_with_concurrency` 加 `wait_for`。属架构级新增。 |
| 2 | **Middleware chain 式分层流水线** | 只有 `ToolRegistry` 的 pre/post hook（`registry.py:261-265,475-500,589-595`）和 HTTP 层 `core/middleware.py`。**Agent 执行管线本身没有 middleware 抽象** —— Planner/Executor/Reflector 是 loop 里硬编码的 5 个 Phase，各 Phase 的 system prompt 在 3 个文件里各自拼接 | 无法在不改 loop 的前提下插桩/拦截/改写请求（统一注入预算、统一 PII 脱敏、统一 span） | **中**。hook 基础设施（`HookContext`/`PreHook`/`PostHook`/`ToolAbortError`）已在 `registry.py` 跑通，抽同构的 `AgentPhaseHook` 挂到 5 个 Phase 边界是机械工作；难点是把 3 处 system prompt 拼装收敛成可插拔 builder |
| 3 | **渐进式 skill 加载** | `curated_skills.py:93` 有完整加载器（frontmatter `:144`、reference 懒加载缓存 `:221`、`get_summary`/`get_full_prompt` `:56,66`），但**只被 REST 端点调用**；`skills/` 下只有 1 个 skill | ① 没有"技能摘要进 system prompt → 模型按需 `load_skill` 拉正文 → 再按需拉 reference"的两级渐进 ② `curated_skills.py:61` 承诺的 `load_skill_reference` 工具**根本不存在** ③ 技能与 Planner 的 `tool_hint` 无关联 | **中**。缺 (a) 注册 `load_skill(name, reference=None)` 工具（约 50 行）(b) 在 `planner.py:243` 注入 `list_summaries()`（约 10 行）(c) 决定技能与工具 schema 的 token 竞争。**本表性价比最高。** |
| 4 | **上下文压缩与 micro-compact** | 有单点 `ContextEngine.fit_async`（`loop.py:248`），三级降级。但只作用于**调用方传入的 history**；其余全部在预算之外各自膨胀。`_smart_truncate`（`loop.py:56`）是死代码 | ① 无统一预算核算 ② 无工具级/step 级 micro-compact ③ 无 compaction 触发时机策略（现在只在 run 开头跑一次）④ step 结果线性增长（`executor.py:896-916` 只截 200 字符缓解） | **中**。已有 `estimate_tokens` 与 `_truncate_messages` 可复用。(a) 在 `executor._execute_step_once` 组装 messages 后加统一预算检查 (b) 给 `AgentEvent`/`ExecutionContext` 加"当前上下文占用" (c) 复活 `_smart_truncate` 接到 `executor.py:815`。不改架构，纯加固 |
| 5 | **沙箱隔离** | Docker 沙箱质量不错，AST 兜底做了较深逃逸防护。**但** `SANDBOX_MODE` 默认 `"auto"`，本机无 Docker 时**静默回落 AST**；且两个工具默认禁用 | ① AST 线程无法被超时杀死 ② 无磁盘配额 / PID 限制 / 镜像白名单 ③ "auto 静默回落"是安全姿态的可观测性缺口（只在 `code_execution.py:305` 打一条 warning） | **小～中**。(a) auto 回落改为显式上报（事件 + 指标）(b) 加镜像白名单校验 (c) 若要修线程超时需换 `multiprocessing` 或强制 Docker-only（**这项是"中"**） |
| 6 | **可恢复会话与 checkpoint** | 表已建、状态机已定义（`models/agent_task.py:33,71,151`）、序列化已有（`exec_context.py:136,191`）。**运行时零读写** | ① 循环内无 checkpoint 边界（`executor._execute_single_step` 完成后最自然）② 无 resume 入口 ③ SSE 无 `Last-Event-ID` 重放 ④ 无并发/幂等保护 | **大**。需引入"可中断点 + 快照写入 + 恢复时重建 Plan/WorkingMemory/ctx"的完整语义，还要处理工具副作用幂等、跨进程 run 锁、SSE 重连语义。属新架构 |
| 7 | **轨迹记录与指标** | 4 套部分重叠：`ExecutionContext`、`AgentEvent` SSE、Prometheus 8 指标、Langfuse（可选）。另有 2 套**未接线**：`tracing.py:138` `TraceStore`、`core/trace_logger.py:86,172` | ① 4 套互不同步，无法从任一入口重建完整轨迹 ② `StepRecord`/`Decision` **都没有墙钟时间戳** ③ 生产路径 trace 不落盘 ④ `EventType` Literal 缺 `execution_context` | **中**。数据都在，缺**统一**：(a) 加 `timestamp`（2 个 dataclass + 3 个写入点，小）(b) 统一到单一 source of truth 并删冗余（中）(c) `TraceExporter` JSONL 落盘接到生产路径（小） |
| 8 | **任务级成本核算** | **完全缺失**。`total_tokens` 零赋值；`StepRecord.tokens_used` 从不传；`response.usage` 只进 Langfuse；`LLM_TOKENS` 指标只服务 RAG。`budget_exceeded` 错误码已定义（`registry.py:73`）但**无处产生** | ① 无法回答"这次任务花了多少 token" ② 无法做 per-task/user/org 成本归集 ③ 无法做预算超限熔断 | **小～中（收益最高）**。(a) 5 个调用点各加一行累加 `ctx.total_tokens`（`executor.py:633`、`planner.py:308`、`reflector.py:166`、`loop.py:343`、`executor.py:827`）(b) `ctx.complete_step(tokens=...)` 补上 (c) 加 `agent_tokens_total{model,direction}` 指标（模板在 `prometheus.py:90`）(d) 真要做预算熔断需在 loop 加检查点（中） |

### 优先级建议（按 收益/代价 排序）

1. **Token 核算**（#8，小）—— 5 个调用点各加一行，立刻补齐可观测性三问中唯一答不出的那个
2. **渐进式 skill 加载**（#3，中）—— 加载器现成，缺一个工具 + 一段 prompt 注入
3. **轨迹时间戳统一**（#7a，小）—— 2 个 dataclass + 3 个写入点
4. **上下文预算统一**（#4，中）—— 复活 `_smart_truncate`，在 executor 组装处加检查
5. **沙箱策略层**（#5，小～中）—— auto 回落显式上报 + 镜像白名单
6. **Middleware chain**（#2，中）—— 照抄 registry 的 hook 模式挂到 5 个 Phase 边界
7. **子 agent 委派**（#1，大）—— 需要 Planner 产出委派型 step
8. **Checkpoint / resume**（#6，大）—— 需要新的循环语义与幂等保证

---

## 五、"未核实" / 搜过没找到

### 未核实（有代码证据但未做动态验证）

- **SSE 断连时 Starlette 是否真的取消 `PERAgentLoop` 生成器**：读到了 `executor.py:348-352` 的 `finally` + 注释 `:307-310` 声称处理了断连，**未运行验证**。框架行为，静态读码无法确证
- **`SANDBOX_MODE` 在本机实际取什么值**：`backend/.env:91` 有 `SANDBOX_MODE=auto`，但 Docker 是否可用未探测
- **Langfuse 是否在部署中启用**：`.env` 中相关 key 存在与否未读（属凭据）
- **`experience/extractor.py` 的抽取质量**：未逐行审 `extract_from_runtime_error` / `extract_from_runtime_success`（520 行）
- **`optimization.py`（419 行）、`reviewer.py`、`evaluation.py`（616 行）、`mining/*`**：只做 grep 级连接性核对，未做逻辑审计
- **前端 `views/agent/index.vue`**：只读了 abort 相关行号，未逐行审流式渲染
- **`services/memory_service.py`（~700 行）**：只读了类/方法清单，未审 LongTermMemory 检索实现（`:256` `search_semantic`、`:246` `_cosine_similarity`）
- **DB 层面 `agent_tasks` 表是否真的被建出来**：读了 migration 004 与 006，未连库确认

### 搜过但没找到（关键词 + 扫描目录）

扫描目录：`backend/app/**`（含 `agent/`、`agent/tools/`、`agent/experience/`、`agent/mining/`、`agent/replay/`、`agent/agents/`、`core/`、`services/`、`api/`、`models/`、`rag/`）、`backend/tests/`、`backend/alembic/`、`frontend/src/**`、`skills/`

| 没找到 | 关键词 | 结果 |
|---|---|---|
| 任何对 `max_iterations` 的**读取** | `max_iterations` | 5 处命中，全在 config 定义/序列化与调用方赋值 |
| 任何对 `total_tokens` 的**赋值** | `total_tokens` | 只有初始值 0 与序列化读取 |
| checkpoint / resume 的**运行时实现** | `checkpoint`, `resume`, `reconnect`, `last_event_id` | `last_event_id` 零命中；`checkpoint`/`resume` 只命中 model 注释 |
| micro-compact | `micro_compact`, `compact` | `compact` 零命中 |
| agent 级 middleware chain | `middleware` in `app/agent/**` | 零命中 |
| 服务端取消/中断 API | `cancel`, `interrupt`, `/stop` in `agent.py` | 零命中 |
| 子 agent 的调用者 | `subagent`, `delegate_task`, `run_parallel_subtasks` | 仅 `agent/__init__.py:16` docstring |
| `load_plan` 的调用者 | `load_plan`, `agent:plan:` | 仅 `planner.py` 自身 |
| `ExecutionContext.load` 的生产调用者 | `ExecutionContext.load`, `ReplayEngine` | 仅 `replay/engine.py:39`（ReplayEngine 自身无调用者） |
| `AgentTask` 的运行时读写 | `AgentTask`, `agent_tasks` | 仅 model 定义 + `models/__init__.py:2` + migration。**零 CRUD** |
| `load_skill_reference` 工具 | `load_skill_reference` | 仅 `curated_skills.py:61` 一句 prompt 字符串，**函数不存在** |
| `curated_skill_registry` 的 Agent 侧调用 | `curated_skill_registry` | 8 处全在 `api/v1/endpoints/curated_skills.py` |
| SkillManager 的学习方法调用者 | `learn_from_plan_success` 等 | 仅 `skills.py` 自身定义 |
| `EscalationConfig` 的读取 | `escalation`, `should_escalate` | 仅 `executor.py:26,102` import + 实例化，**从不查询** |
| `budget_exceeded` 的**生产者** | `budget_exceeded` | 错误码/策略有定义，无任何代码产生它 |
| `_smart_truncate` 的调用者 | `_smart_truncate`, `MAX_RESULT_TOKENS` | 仅 `loop.py:44,56` 定义处 |
| `ContextEngine` 的其他调用点 | `ContextEngine`, `fit_async` | 仅 `loop.py:139,144,248` |
| `AgentTracer` / `TraceExporter` 的生产调用者 | `AgentTracer`, `TraceExporter` | 仅未挂载的 `agent_api.py:54,60,227` |
| 墙钟 deadline / 总超时 | `deadline`, `wall_clock`, `total_timeout` | `app/agent/**` 内仅 `message.py:135` |
| 独立的 agent 进程/服务 | `multiprocessing`, `celery`, `ProcessPool` in `app/agent/**` | 零命中 |

### 一处类型标注漂移（顺带发现）

`events.py:21-35` 的 `EventType` Literal **不含 `execution_context`**，但 `loop.py:570` 实际 yield 了 `type="execution_context"`。运行时不报错（dataclass 不做运行时校验），但任何基于 `EventType` 做穷尽 switch 的前端/类型检查会漏掉这个事件。
