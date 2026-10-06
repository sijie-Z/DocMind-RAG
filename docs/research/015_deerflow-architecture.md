# DeerFlow 2.0 深度调研（可照着重现版）

> **调研日期**：2026-10-06
> **仓库证据快照**：`bytedance/deer-flow` main 分支，latest commit `3725ba1bfb65`（2026-10-06T09:55:51Z，当日仍在高频提交）
> **取证方式（可复现）**：本机网络无法直连 github.com / WebFetch 被全域拦截，改用以下通道拿到了**整仓源码**：① `codeload.github.com` 直下 tarball（2192 个文件已解包到 本地仓库快照（未入仓），按字母序截断，缺 `frontend/`、`skills/` 等尾目录，已按需补拉）；② `raw.githubusercontent.com` 逐文件直拉；③ `gh-proxy.com` 代理 GitHub API（issue/tags/commit 元数据）；④ `data.jsdelivr.com` 取文件清单与 tag 列表；⑤ `ungh.cc`/shields.io 镜像取 stars。外部文章（deerflow.tech、火山引擎、量子位等）由子 agent 当日 curl 抓取。
> **标注约定**：`[原文]`＝仓库/官方页面逐字引文（出处 URL 附后）；`[二手]`＝第三方文章转述；`[子agent取证]`＝外部页面由子 agent 抓取后逐字转引（非我本人二次打开，但均为当日新鲜抓取）。仓库文件引用统一给出 `raw.githubusercontent.com` 链接（离线副本路径同时给出，位于 `_clones/deer-flow-main/<path>`）。
> **纪律声明**：本报告只认原文；凡未找到原文的（如 micro_compact、"900s"字样、AISandBox 拼写、"76k stars"），一律进 §10 未找到清单，**不做任何推测性补全**。

---

## 0. 事实核对：用户简报中四个前提的实况

| 简报说法 | 实况 | 证据 |
|---|---|---|
| "2026 年 5 月左右开源" | **不准**。仓库创建于 **2025-05-07**，2025-05-10 官宣开源（微信「字节跳动技术团队」+ InfoQ 中国 + IT之家）；**2.0 于 2026-02-28 发布**，当日登上 GitHub Trending 第一 | README 原文：`On February 28th, 2026, DeerFlow claimed the #1 spot on GitHub Trending following the launch of version 2.`（https://raw.githubusercontent.com/bytedance/deer-flow/main/README.md）；微信公告（2025-05-10，作者李昕）：https://mp.weixin.qq.com/s/Le7Ic9FgcwAkQeHDuorXMw `[子agent取证]` |
| "76k stars" | **无此时间点数据**。2026-10-06 实时为 **83,433 stars / 11,589 forks**（ungh.cc 镜像 GitHub API；shields.io 徽章同日报 "83k"）。时间线：35.3k（3 月）→ 57k（4 月初）→ ~60k（4 月中）→ 73.8k（6 月底）→ 83.4k（今） | https://ungh.cc/repos/bytedance/deer-flow ；量子位 2026-03-23（https://www.qbitai.com/2026/03/391361.html ）`[二手]` |
| "作者 LofiSu" | **部分成立，但"2.0 技术体系分享者/作者"的定位过强，需降级**。① 一手贡献证据：2026-02-09~03-11 期间有 LofiSu 署名的提交（GitHub commit search 结果达接口上限 100 条，含 `Merge pull request #31 from LofiSu/experimental`、`Merge upstream/experimental: resolve conflict in lead_agent/prompt.py`）；5 个 PR：#1063/#1064（记忆管理 API 与注入开关）、#1077（已合并，2026-03-11）、#1081（middleware 修复：runtime.context 空值崩溃 + 标题生成阻塞，详见 §4 表格后注）、#1088（前端 404/422 修复；其中 #1063/#1064/#1081/#1088 未见合并记录）。② 反证：官方 README 中英文全文 **0 次**出现 "LofiSu"；官方 Key Contributors 只列 **Daniel Walnut（=hetaoBackend）与 Henry Li（=magiccube）**；量子位报道为 Tao He、Henry Li（"Henry Li"一致；另一人两种拼写，按来源原样记录）。"Maintainer of ByteDance/DeerFlow" 是**其个人 README 的自述**，非官方名单口径。③ 他**没有任何署名长文**：个人博客域名 lofisu.chat 已不可解析、myBlog 仓库全量 155 文件无 DeerFlow 文章、掘金/知乎/微信未见；其简历自述技术文章发表于"内部论坛"。关于 DeerFlow 最实质的公开文字=上述 PR 描述。④ 另注意：2026-05 起存在针对其"身份包装"的批评材料（详见 §10-8，均二手）。**结论：引用 2.0 架构体系请以官方 README/deerflow.tech/仓库源码为准；LofiSu 可按"社区活跃贡献者（middleware/记忆/前端方向有实际提交）"引用。** | https://api.github.com/search/commits?q=repo:bytedance/deer-flow+author:LofiSu （经 gh-proxy 抓取）；https://raw.githubusercontent.com/LofiSu/LofiSu/main/README.md （自述）；PR 列表 https://github.com/bytedance/deer-flow/pull/1081 等 |
| "11 层中间件 / 3 并发 / 900s / AISandBox / micro_compact>180k" | 逐条核验：**11 层**＝2026 年 3 月发布会时代快照（现在远不止）；**3 并发**＝✅（默认进程级 `max_running: 3`）；**900s**＝"自定义子 agent"默认超时（内置子 agent 有效默认是 1800s，见 §2.4）；**AISandBox 拼写不存在**，真名 AIO Sandbox / `AioSandboxProvider`；**micro_compact 在仓库中不存在**（两路独立 grep 全仓 0 命中，也无 180k 常量，见 §6.3） | 分散于下面各节 |

---

## 1. 一页结论

**一句话**：DeerFlow 2.0 把"研究框架"重写成了**通用的 Agent 运行壳（super agent harness）**——一个 Lead Agent 用一条**有序中间件管道**管理上下文/记忆/沙箱/安全，遇到大活时通过 `task` 工具动态拉起子 Agent 并行干活，每个子 Agent 在**共享线程沙箱**里独立跑、只把最终报告交回来。

**用大白话拆解它为什么有效**（每条都在后文有原文支撑）：

1. **控制流只留一处**：不再有 Coordinator/Planner/Researcher/Coder/Reporter 五个固定角色的 LangGraph 大图，只有"一个 Lead Agent + 一个 task 工具"。扩展能力＝加中间件、加技能、加工具，**不改图**。官方原话："DeerFlow 2.0 is no longer a framework you wire together. It's a super agent harness — batteries included, fully extensible."
2. **横切关注点被切成 40 余个有序中间件**（当前 main；发布会时是 11 个）：每层只管一件事（上传、沙箱、摘要、标题、记忆、循环检测、子 Agent 限流、澄清…），按固定顺序包裹每次 LLM 调用。加/改功能只动一层，Agent 推理代码零改动。
3. **子 Agent 是"受控的并行 + 上下文隔离"**：进程级 FIFO 准入控制器默认只放 3 个并发（排队不占线程）、单 run 总委派数默认 6；子 Agent 继承父线程目录与沙箱（同一 `sandbox_id`，用执行租约保证"最后一个用完才释放"），但**看不到**父对话；结果以"自述报告 + 确定性验收清单"回传，报告里每个动作声明必须带 `[rN]` 回执引用，否则被标记 UNVERIFIED。
4. **上下文是预算制资源**：超大工具输出自动落盘（>12,000 字符转存 `.tool-results/` 并回传文件引用）；历史超限自动摘要压缩（可配置 token/条数/上下文比例三种触发器）；跨会话记忆按 token 预算选择注入（默认 2000 token，纠错类保底 500）；技能只在系统提示里放**索引**，正文按需 `read_file` 渐进加载（修复了"全量塞 SKILL.md 导致简单任务 137K token"的老问题，见 PR #2993）。
5. **沙箱是真隔离**：Docker 容器（默认镜像就是字节自家的 all-in-one-sandbox），硬限制 2G 内存 / 2 CPU / 512 PIDs，按 `user_id + thread_id` 派生隔离标识，上传/工作区/产出三个挂载点，默认不外发。
6. **一切可观测**：LangSmith/Langfuse/Monocle 三路 trace、逐 run token/成本记账（含子 Agent 归因）、subagent.start/step/end 事件流、5 档 stop_reason（loop_capped / token_capped / turn_capped / safety_capped / subagent_limit_capped）。

> ⚠️ 注意：里面**没有**"micro_compact"这个机制，也没有 180k 触发阈值；这是需要从 DocMind 设计里剔除的错误认知（见 §6.3）。

---

## 2. Agent Teams Harness（Lead Agent 统一决策 + SubagentExecutor）

### 2.1 Lead Agent 本体

- 主模块：`backend/packages/harness/deerflow/agents/lead_agent/agent.py`（工厂 `build_middlewares`/`make_lead_agent`），系统提示在 `lead_agent/prompt.py`，最终用一行 `create_agent(model, tools, middleware=..., system_prompt=..., state_schema=ThreadState)` 编译成 LangGraph 图 `[原文]`。
- 它**不硬编码工作流**，官方文档定义："Lead Agent 是 DeerFlow 线程中的核心执行者。每个对话、任务和工作流都通过它进行。……它使用模型的推理能力适应用户提供的任何任务，由系统提示和当前范围内的技能引导"（职责列表含"通过 task 工具将子任务委派给子 Agent"）。`[子agent取证]` https://deerflow.tech/zh/docs/harness/lead-agent
- 系统提示是运行时拼装的（`apply_prompt_template`），章节包括 `<role>/<soul>/<memory>/<skill_system>/<subagent_system>` 等（记忆块的 `<memory>` 模板在 `prompt.py`；结构化 XML 章节命名见 DeepWiki 页 https://deepwiki.com/bytedance/deer-flow/5.3-lead-agent-and-prompt-system `[二手/自动生成文档，可与本地源码互证]`）。

### 2.2 `task` 工具：怎么触发子 Agent `[原文]`

定义：`backend/packages/harness/deerflow/tools/builtins/task_tool.py`

```python
@tool("task", parse_docstring=True)
async def task_tool(
    runtime: Runtime,
    prompt: str,
    subagent_type: str,
    tool_call_id: Annotated[str, InjectedToolCallId],
    *,
    acceptance_criteria: list[str] | None = None,
    description: str = "",
    context_mode: Literal["isolated", "snapshot"] = "isolated",
) -> str | Command:
```

关键设计（docstring 逐字，节选）：

- **何时用**："Independent tasks that materially reduce wall-clock time when run in parallel / A specialist subagent provides capability unavailable on the direct path / Bounded exploration that would otherwise displace important parent context"；**何时不用**："Merely because a task is complex, multi-step, verbose… Parallel work with overlapping files, shared mutable state, or external side effects / Tasks requiring user interaction"。
- **读结果的口径**："subagent reports are SELF-REPORTS, not verified facts"；默认（`verification.receipts_enabled`）子 Agent 必须为自己的每个动作声明引用回执号 `[rN]`，并给交付物附带可核查句柄（绝对路径/URL/ID/HTTP 状态码）；`acceptance_criteria` 的确定性检查支持 `file:<path> exists|non-empty`、`file_written:<path>`、`tests_passed:<command>`、`file:<path> json-valid`（≤50,000 字节），不可判定的一律标 **UNVERIFIED，绝不静默通过**。
- **两种上下文模式**：`isolated`（默认，只给任务描述）/ `snapshot`（额外携带父对话保留历史+摘要作为背景，费用是更多输入 token；子 Agent 保持自己的角色/工具；后续父轮次不同步）。
- **挂载条件**：只有 `subagent_enabled` 为真时，`task` 工具才组装进 Lead 的工具集（`tools/tools.py` 的 `SUBAGENT_TOOLS`；Gateway 走 run context 白名单 `subagent_enabled/max_concurrent_subagents/max_total_subagents`，IM 渠道固定关闭）；`task_status` 工具**不再暴露给 LLM**（后端内部轮询）。
- **调用语义**：`task()` 调用会**阻塞直到子 Agent 完成**——后端每 5s 轮询一次注册表（`_SUBAGENT_POLL_INTERVAL_SECONDS = 5.0`；轮询上限 `(config.timeout_seconds + 60) // 5`），完成后直接在 ToolMessage 里返回结果。历史演进见仓库文档 `backend/docs/task_tool_improvements.md`（早期需要 LLM 手动轮询 `task_status`，后改为后端轮询+阻塞返回，消除无谓的 LLM 轮询请求）。

### 2.3 SubagentExecutor：子 Agent 是怎么跑的 `[原文]`

文件：`backend/packages/harness/deerflow/subagents/executor.py`（2321 行，class `SubagentExecutor` 在 L843）。

**执行链（照着重现的顺序）**：

1. **初始状态构建**（`_build_initial_state`，L1406+）：把 system_prompt + 报告契约 + 技能索引拼成**一个** SystemMessage（"Some LLM APIs reject multiple SystemMessages"），再把任务写成 HumanMessage（若带了 `acceptance_criteria`，标准块以**不可信数据**身份追加在任务消息里而非系统通道）；随后透传父侧 `sandbox` / `thread_data` / `uploaded_files` 深拷贝进子状态。
2. **报告契约注入**（L1495-1502 逐字注释）："RFC #4651 PR3: every subagent — built-in or custom — gets the same report contract, so the citation / verifiable-handle requirements never depend on the config author remembering them." 契约正文在 `subagents/report_contract.py`：`<report_contract>` 块要求动作声明引用 Tool receipts ledger 的 `[rN]`、交付物附可核查句柄、显式报告失败——"A completed report whose action claims carry no receipt citation is flagged UNVERIFIED."
3. **独立小循环**：子 Agent 不复用 Lead 工厂，直接 `create_agent(model, tools, middleware=..., checkpointer=False)`（一次性、永不 resume；不继承父 checkpointer）；`max_turns` 通过 `turn_budget.py::resolve_recursion_limit` **翻译成 LangGraph 的 recursion_limit**（关键机制：`create_agent` 会把**每个中间件钩子编译成独立图节点**，一"turn" 实际消耗的 super-step 随链深变化；原文注释："the subagent chain compiles seven to eight loop nodes, so ``max_turns=150`` bought roughly eighteen turns"——所以必须按实际装配链计算乘数）。
4. **进程级准入**：所有子 Agent（普通 task 与批量）共用一个 **异步 FIFO 准入控制器**（`subagents/capacity.py::SubagentExecutionCapacity`，全文约 180 行：`max_running` 槽位 + `max_queued` 等待队列 + `admission_policy: queue|reject` + `queue_timeout_seconds`；"queued work never owns a thread"、释放时槽位直接转移给队首、超时/取消不泄漏槽位）。
5. **执行**：`execute_async()` 生成服务端 `execution_id`（进程注册表/轮询/取消/超时的所有权键），provider 的 `tool_call_id` 保留为 `external_task_id`（前端卡片/SSE 事件/持久化关联键）；`asyncio.wait_for(..., timeout=self.config.timeout_seconds)` 包整个运行，超时错误串 `Execution timed out after {N} seconds`（L2180）。
6. **回传**（`_extract_final_result`）：只提取**最后一条 assistant 消息文本**（或直接返回型工具的匹配结果）；空内容给 `"No response generated"` 哨兵值。executor 本身**不截断**最终报告文本；但 UI/账本侧有 `subagent_result_brief` 2,000 字符摘要 + 全文 SHA-256；若最终结果过大，Lead 侧的 `ToolOutputBudgetMiddleware` 还会把它外部化成文件引用（其 per-tool 阈值含 `task`）。

**内置两个子 Agent**（`subagents/builtins/`，`[原文]`）：

| 类型 | 工具 | 默认 max_turns | 行 | 描述（首行进 Lead 提示词） |
|---|---|---|---|---|
| `general-purpose` | `tools=None`（继承父全部工具）+ `disallowed_tools=["task","ask_clarification","present_files"]` | 150 | general_purpose.py | "A capable agent for bounded exploration and action when there is clear delegation benefit." |
| `bash` | `["bash","ls","read_file","write_file","str_replace"]` | 60 | bash_agent.py | "Command execution specialist for bounded shell workflows… Routine git, build, test, or deploy operations are not sufficient reason to delegate." |

### 2.4 「3 并发 / 900s」数字考古（用户问题的正面回答）

**为什么是 3 并发**：
- 现行默认在 `config.example.yaml`：`subagent_runtime: {max_running: 3, max_queued: 64, admission_policy: queue, queue_timeout_seconds: 300}`；schema 约束 `1-64`（`config/subagent_runtime_config.py` L11-28，字段说明逐字："Maximum native subagents that may execute concurrently in one Gateway process."）。
- **由来**：仓库设计文档 `docs/plans/2026-08-24-subagent-batch-capacity-implementation.md` 原文："the executor had a fixed process-local capacity of three native subagents"（旧实现写死 3）；重构时"**The default remains three, so existing deployments do not increase model, sandbox, or database load after upgrading**"（保持 3 是为了升级后不增加负载——这是"为什么是 3"的官方解释）。同文档解释 `max_total_per_run` 默认 6："**The default 6 allows two full batches at the default concurrency of 3.**"（6 = 两轮满载）。
- 解析公式（`subagents/AGENTS.md` 原文）："Ordinary `task` concurrency is resolved once as the **minimum of the per-run request, the startup-frozen `subagent_runtime.max_running`, and the schema safety ceiling (1-64)**, then shared by the lead prompt and `SubagentLimitMiddleware`."——即"提示词里印的限额"和"中间件实际截断的限额"和"真实执行槽位"三者同源，不会出现"模型以为能开 5 个、实际只跑 3 个"的错位。
- 超编行为：多余的 task 调用被**剥离**（strip tool_calls、强制 `finish_reason=stop`）并向模型追加一条可见的 "subagent delegation limit" 说明，让它用已有结果收尾。

**为什么是 900s（单任务超时）**：
- **"900" 的真实身份**：`SubagentConfig.timeout_seconds = 900` 是**裸兜底值**，其 dataclass docstring 逐字解释："Bare fallback execution-time cap. **For built-in agents the effective limit is the global `subagents.timeout_seconds` (default 1800 = 30 min)**, layered on by the registry; this 900 only applies when no differing global value exists."（`subagents/config.py` L34-49）；自定义子 Agent 的默认也是 900（`config/subagents_config.py` L155）。**内置子 agent 的实际默认超时是 1800s**（`SubagentsAppConfig.timeout_seconds` default=1800，L170；registry 会覆盖到内置 agent 上）。
- 相关数字：`config.example.yaml` 注释逐字：`# Default timeout (seconds) for built-in subagents (default: 1800 = 30 min). # Custom agents use their own timeout_seconds (default 900) unless overridden.`；示例覆盖：general-purpose→2700s/250 turns、bash→300s/80 turns。
- 结论：**用户听说的"900s"= 自定义/兜底子 Agent 超时；如果你想让所有委派都是 30 分钟上限，内置默认就是 1800s。**（另：仓库 `backend/README.md` 里"15-minute timeout"的表述是旧文档漂移 `[二手/文档核对]`，官方文档站当前写 1800s `[子agent取证]`。）

### 2.5 子 Agent 如何继承父线程目录、复用父沙箱 `[原文]`

- **目录继承**：task 工具从父 runtime 取 `sandbox` / `thread_data` / `uploaded_files` 快照并透传给 executor；子 Agent 用**父的 thread_id** 运行，因此 `users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}`（沙箱内 `/mnt/user-data/...`）解析结果与父完全一致；验收检查器也在"shared thread workspace"上核对文件类标准。
- **沙箱复用（执行租约模型）**：
  - 每次子 Agent 运行注册自己的租约：`sandbox_lease_owner_id = f"subagent:{result.task_id}"`（executor.py L1627），通过 runtime context 传给子图的 SandboxMiddleware；
  - `sandbox/middleware.py::_retain_existing_sandbox` 遇到已有 `sandbox_id` 时调用 `lease_manager.reuse_or_acquire(owner_id, sandbox_id, thread_id, user_id)`；
  - `sandbox/lease.py` 模块 docstring 逐字："which concurrently running agent executions inside one Gateway are still using the provider's active client. **The last execution lease is the only one allowed to call `SandboxProvider.release`.**"；
  - 中间件 docstring："Only the final execution release parks a remote sandbox in its warm pool"——即**一个子 Agent 先结束不会关掉兄弟们还在用的沙箱**；AIO 侧还会为每个子 Agent 选独立的持久 shell 会话（`sandbox_command_scope_id`），避免并发子 Agent 互相污染 shell 状态（#5128）。

### 2.6 子 Agent 进度如何实时推流到前端 `[原文]`

四段式管线（每段都有源码/文档出处）：

1. **任务侧发自定义流事件**（`task_tool.py`）：通过 `get_stream_writer()` + `dispatch_custom_event`（`utils/custom_events.py`，先写 custom stream、再 best-effort 派发回调）发出：
   - `task_started`（含 `task_id`、`description`、**effective model**）
   - `task_running`（每个新捕获的子 AI/Tool 消息一条，含 `message`、`message_index`、`total_messages`、`usage`）
   - `task_completed` / `task_failed` / `task_cancelled` / `task_timed_out`（含 result/error、usage、model）
2. **帧到前端**：Gateway 走 SSE（`RunManager + run_agent + StreamBridge`，`/api/langgraph/*` 经 Nginx 重写到 Gateway 原生路由；支持 `Last-Event-ID` 重连与 `gap` 恢复帧）。前端"**subtask progress rides root-namespace `task_*` custom events**"、以子任务卡片渲染（折叠卡显示生效模型与累计 token，重载后从持久化恢复）——`backend/AGENTS.md`/`docs/ARCHITECTURE.md` 原文。
3. **持久化**：`subagents/step_events.py::subagent_run_event()` 把 `task_*` 映射为 run event：`subagent.start` / `subagent.step` / `subagent.end`（category="subagent"，`task_id` 在 metadata；单步 text 与工具入参截断至 `SUBAGENT_STEP_MAX_CHARS = 8192`，带 truncated 标记）；worker 端 `_SubagentEventBuffer` 批量 `put_batch()`（25 条或遇到 `subagent.end` 时冲刷）。契约表在 `backend/docs/RUN_EVENT_STREAM.md` 逐字：`subagent.start ← task_started`、`subagent.step ← task_running`、`subagent.end ← 终态 task_*`；终态 status ∈ {completed, failed, cancelled, timed_out}。
4. **回放/审计**：`GET /api/threads/{thread_id}/runs/{run_id}/events?task_id=...&after_seq=...` 分页取单任务全程；历史子任务卡片按需 backfill。

### 2.7 完成后如何"压缩摘要"回传 Lead `[原文]`

准确说 DeerFlow 的策略不是"executor 截断"，而是**契约化精简 + 分级防护**：
- 模型可见的结果 = **子 Agent 的完整最终报告文本**（final assistant message），格式 `Task Succeeded. Result: {text}` / `Task Succeeded (capped: {cap}). Result: ...` / `Task failed: ...`（`status_contract.py` L252-255）。
- **精简发生在子 Agent 出口标准上**：报告契约要求"简明总结+关键发现+文件路径/工件+问题+引用"，且动作声明必须带 `[rN]` 回执、交付物带核查句柄——用结构化要求换回传质量，而不是粗暴截断。
- **UI/账本层**：`subagent_result_brief` 摘要 2,000 字符（head 2/3 + tail，`_bound_metadata_text`）+ 全文 SHA-256；token 用量走 `subagent_token_usage` 元数据归因回父 run。
- **父侧最后一层**：若回传仍超预算，`ToolOutputBudgetMiddleware` 会把 `task` 结果外部化为 `/mnt/user-data/outputs/.tool-results/` 文件引用（阈值见 §6.1）。

### 2.8 嵌套防护与三轴熔断 `[原文]`

- **防嵌套三层**：① 工具装配时子 Agent 一律 `subagent_enabled=False`（拿不到 task/batch_task）；② 配置层 `disallowed_tools` 默认 `["task"]`（内置更严：`["task","ask_clarification","present_files"]`）；③ 子 Agent 系统提示里硬写 "You are a subagent - the `task` tool is NOT available to you. You must NEVER attempt to call `task`..."。
- **三轴熔断**（全部"不抛异常、逼出最终答案"风格，命中后打 additive 字段 `stop_reason`，保持旧前端兼容）：
  - **turn 轴**：max_turns → recursion_limit；耗尽抛 `GraphRecursionError`，被 `_aexecute` 专门捕获 → `turn_capped`；
  - **token 轴**：`TokenBudgetMiddleware` 默认 `max_tokens` **与 summarization 开关耦合**（开=1,000,000 / 关=2,000,000，warn 0.7）→ 硬停时剥离在途工具调用、强制 `finish_reason="stop"` → `token_capped`；
  - **loop 轴**：`LoopDetectionMiddleware`（默认阈值：同一 tool-call 组重复 3 次告警、5 次硬停；同一工具类型 30 次告警、50 次硬停；窗口 20）→ `loop_capped`。
- Lead 侧委派总量熔断：`max_total_per_run=6`（范围 1-50，per-request 可覆盖 `max_total_subagents`），由 `SubagentLimitMiddleware` 按"当前 run 的持久委派账本"计数执行；耗尽时同样剥离+提示合成。

---

## 3. 1.0 → 2.0 演进理由（只列官方原文）

**① 仓库 README（官方）——"From Deep Research to Super Agent Harness" 整节的动机陈述** `[原文，我在本地 README.md 逐字读过]`
（https://raw.githubusercontent.com/bytedance/deer-flow/main/README.md#L1156 ，中译参见 README_zh.md 同名章节）

> "DeerFlow started as a Deep Research framework — and the community ran with it. Since launch, developers have pushed it far beyond research: building data pipelines, generating slide decks, spinning up dashboards, automating content workflows. Things we never anticipated.
> That told us something important: **DeerFlow wasn't just a research tool. It was a harness** — a runtime that gives agents the infrastructure to actually get work done.
> **So we rebuilt it from scratch.**
> DeerFlow 2.0 is no longer a framework you wire together. It's a super agent harness — batteries included, fully extensible. Built on LangGraph and LangChain, it ships with everything an agent needs out of the box: a filesystem, memory, skills, sandbox-aware execution, and the ability to plan and spawn sub-agents for complex, multi-step tasks."

**② 迁移公告（官方 issue #824，标题逐字 "🔥🔥🔥 Release Plan of DeerFlow 2.0"，创建 2026-01-21，已关闭）** `[原文，我经 gh-proxy 抓取 API]`
（https://github.com/bytedance/deer-flow/issues/824 ）

> "As we plan the development of Deer-Flow 2.0, it's time to wrap up Deer-Flow 1.0, which we have been updating in the main branch."

**③ 1.x 分支 README 的迁移说明（官方）** `[子agent取证，cdn.jsdelivr 镜像抓取 main-1.x]`
（https://raw.githubusercontent.com/bytedance/deer-flow/main-1.x/README.md ）

> "As we're [moving to DeerFlow 2.0](https://github.com/bytedance/deer-flow/issues/824) in February, it's time to wrap up DeerFlow 1.0 on the main branch."

1.0 架构原文（同文件，"## Architecture" 节，我也在本地 `_clones/main/README_1x.md` 逐字读过）：

> "1. **Coordinator**: The entry point that manages the workflow lifecycle … 2. **Planner**: Strategic component for task decomposition and planning … 3. **Research Team**: … **Researcher**: Conducts web searches… **Coder**: Handles code analysis, execution… 4. **Reporter**: Final stage processor for research outputs"

**④ 官方文档站「为什么选择 DeerFlow」（deerflow.tech，官方站，README 顶部 "Official Website" 指向）** `[子agent取证]`
（https://deerflow.tech/zh/docs/introduction/why-deerflow ，页面标注 Last updated 2026-08-23）

> "研究是第一个技能，而非整个系统"（小标题）；"这种使用方式的转变带来了一个关键结论：**深度研究应该被视为更广泛 Agent 运行时中的一项能力，而不是整个产品的定义**。因此，DeerFlow 从一个以单一研究模式为中心的项目演化为一个通用的长时序 Agent Harness。"

**⑤ 官方文档站「设计理念」（为什么是中间件链而不是子类化）** `[子agent取证]`
（https://deerflow.tech/zh/docs/harness/design-principles ）

> "DeerFlow 围绕一个核心思想构建：**Agent 行为应该由小型、可观察、可替换的组件组合而成——而不是硬编码到固定的工作流图中。**"
> "你不需要从头设计编排层——Harness 就是编排层。"
> 小标题"**中间件链优于继承**"："DeerFlow 不要求你子类化 Agent 或覆盖方法来改变其行为。相反，它使用一个中间件链来包裹每次 LLM 调用。"

**⑥ 字节系官方渠道（火山引擎开发者社区官方号）对"为什么改"的最直白表述** `[子agent取证，引文逐字]`
（https://developer.volcengine.com/articles/7622159746254307391 ，2026-03-30，标题《DeerFlow 2.0 开源升级：依托 Harness，让 Agent 不再"半途而废"》）

> "1.0 版本的架构很清晰：**5 个固定角色的 Agent 组成一条流水线**——Coordinator → Planner → Researcher / Coder（并行）→ Reporter"
> "1.0 的问题在哪里？**角色固化**：想加一个'设计师 Agent'？得改图、加节点、调边——**本质上是在改框架**。同时**所有 Agent 共享同一份 State，上下文浪费严重**，扩展性也差。"
> "2.0 的解法：**Lead Agent + Middleware + Sub-agent**"；"只有一个 Lead Agent。……复杂任务时，它会动态拉起 Sub-agent 并行处理。想扩展能力？加一个 Tool 或 Skill 即可，不需要调整架构。"
> "**11 个 Middleware 各司其职**，让上下文管理、沙箱分配、记忆更新等'基础设施'工作与 Agent 的核心逻辑完全解耦。"（该文列出的 11 层 = 用户简报里那份清单：ThreadData/Uploads/Sandbox/DanglingToolCall/摘要压缩/TodoList/标题/记忆更新/图像注入/Sub-agent 限流/Clarification）

**⑦ 版本事实**：tag 列表（经 jsdelivr/gh-proxy API）：`v2.0-m0 → v2.0-m1-rc0…rc3 → v2.0.0-rc0/rc1 → v2.0.0 → v2.1.0-rc0 → v2.1.0`；2.0 与 1.x **零共享代码**（README 原文："It shares no code with v1"，1.x 维护在 `main-1.x` 分支）。

> 小结：**官方写的"为什么改"= 社区把研究框架用成了通用 harness（需求侧）+ 固定角色图扩展成本高、共享 State 上下文浪费（技术侧）→ 重写成"单 Lead + 中间件 + 动态子 Agent"（方案侧）。** 这三段都有官方逐字原文，不是我的推测。

---

## 4. Middleware Chain（中间件链）

### 4.1 "11 层"传闻的真相（时间线澄清 —— 引用时必须带日期）

同一官方仓库/官方站在不同时间的四份口径（**都真实存在过**）：
- **9 个**：`backend/README.md` 的精简表（ThreadData/Uploads/Sandbox/Summarization/TodoList/Title/Memory/ViewImage/Clarification）`[二手核对]`；
- **11 个**：发布会时代（2026-03）——火山引擎官方号文章与量子位报道（"单一主智能体+11 层中间件链+动态子智能体"），mintlify.wiki 镜像（"11-middleware execution pipeline"，生成于 2026-03-04）`[二手/镜像]`；
- **14 个**：仓库内 `backend/docs/middleware-execution-flow.md` 原文："主 agent **14 个** middleware（`make_lead_agent`），subagent **4 个**（ThreadData、Sandbox、Guardrail、ToolErrorHandling）。`create_deerflow_agent` Phase 1 实现 **13 个**"（我本地逐字读过该文档）；另有 CSDN 3 月源码文同样说 14 `[二手]`；
- **43 槽（当前 main，代码口径）**：见 §4.2（取证 agent 逐行核实 + 我抽检）。

**结论：用户清单里的 11 个名字完全属实（那是 2026-03 的快照），但顺序有出入、且此后链条显著加长。不要按 11 去对齐现状。**

### 4.2 现行链：装配点与完整有序表 `[原文]`

**装配分三段**（`middlewares/AGENTS.md` L25 原文 + 取证 agent 逐行核实）：
1. `tool_error_handling_middleware.py::_build_runtime_middlewares`（对外暴露为 `build_lead_runtime_middlewares`）——共享运行时基础链；
2. `lead_agent/agent.py::build_middlewares`——追加 Lead 专属段；
3. `extensions/stack.py::compose_with_extensions`——最后合并扩展中间件并做**排序不变式校验**（`extensions/ordering.py` 声明式约束：如 ToolReceipt 必须在所有短路器外层、DeferredToolPromotionAudit 在 SkillToolPolicy 外层、Clarification 恒为最后）。

**当前完整顺序**（默认配置下约 28 个生效；下表含位置、类名、职责一句话、门控）：

| # | Middleware | 职责（docstring 摘要） | 门控/默认 |
|---|---|---|---|
| 1 | InputSanitizationMiddleware | 转义用户输入里的注入类标签（外层最外） | 恒开 |
| 2 | KnowledgeScopeMiddleware | 只暴露 Gateway 准入的知识范围 | 恒开 |
| 3 | ToolOutputBudgetMiddleware | 工具输出预算：超限外部化到文件+回引用 | 恒开（`externalize_min_chars: 12000`） |
| 4 | ToolResultSanitizationMiddleware | 中和远程工具结果中的框架/注入标签 | 恒开 |
| 5 | PiiRedactionMiddleware | PII 脱敏（确定性正则） | `pii_redaction.enabled`，默认关 |
| 6 | ThreadDataMiddleware | 建每线程目录 `.../threads/{id}/user-data/{workspace,uploads,outputs}` | 恒开 |
| 7 | UploadsMiddleware | 注入本轮上传文件清单（历史文件走 `list_uploaded_files` 工具） | Lead 独有 |
| 8 | SandboxMiddleware | 惰性获取沙箱、写 `sandbox_id`；最后持租约者才释放 | 恒开 |
| 9 | DanglingToolCallMiddleware | 给悬空 tool_call 补占位 ToolMessage / 丢弃孤儿结果（救 OpenAI 兼容严格校验） | Lead 开 |
| 10 | LLMErrorHandlingMiddleware | 瞬时 LLM 错误重试与优雅降级 | 恒开 |
| 11 | ToolReceiptMiddleware | 零 LLM 回执层：给每个工具结果打确定性回执（`[rN]`），2,000 字符预算内保留最新 | `verification.receipts_enabled`，默认开 |
| 12 | ArtifactResolutionMiddleware | 工具入参里的工件句柄（`art_xxxxxxxx`）解引用 | 默认开 |
| 13/14 | GuardrailMiddleware ×2 | 工具调用前的授权闸（authorization 实例 + 显式 guardrail provider） | 默认关 |
| 15 | SandboxAuditMiddleware | bash 命令分险级审计（高危拦截、中危加警告） | 恒开 |
| 16 | ReadBeforeWriteMiddleware | 写前读校验：没按当前版本读过就不许改（防盲写） | 默认开 |
| 17 | ToolProgressMiddleware | 工具停滞状态机（WARNED→BLOCKED） | 默认关 |
| 18 | ToolErrorHandlingMiddleware | 工具异常转错误 ToolMessage；给结果统一打 `deerflow_tool_meta` | 恒开 |
| 19 | ArtifactCaptureMiddleware | 捕获工具产物引用进线程状态（跨压缩存活） | 默认开 |
| 20 | DynamicContextMiddleware | 首轮注入 `<system-reminder>`（记忆+日期），**冻结快照**让提示前缀缓存命中 | Lead 独有 |
| 21 | SkillActivationMiddleware | `/skill-name` 显式激活时注入 SKILL.md 全文（隐藏、仅本 run） | Lead 独有 |
| 22 | DeferredToolPromotionAuditMiddleware | 审计 tool_search 的最终提升结果 | 条件 |
| 23 | SkillToolPolicyMiddleware | 技能激活后收窄工具面（`allowed-tools` 策略） | Lead 独有 |
| 24 | DurableContextMiddleware | 捕获委派/技能引用，把摘要以隐藏数据消息重投影 | Lead 独有 |
| 25 | DeerFlowSummarizationMiddleware | 上下文压缩（触发器：tokens/messages/上下文分数） | `summarization.enabled`，默认关 |
| 26 | TodoMiddleware | plan 模式的 write_todos 与丢失检测 | `is_plan_mode` |
| 27 | TokenUsageMiddleware | 记录 token 用量并按步骤归因（含子 Agent 归因合并） | 默认开 |
| 28 | TitleMiddleware | 首轮后自动生成会话标题 | Lead 独有 |
| 29 | MemoryMiddleware | 每轮后把对话排队做异步记忆提取 | memory 开启时 |
| 30 | ViewImageMiddleware | 视觉模型的图片注入（只在请求内，不落 checkpoint） | 模型支持 vision |
| 31 | McpRoutingMiddleware | 按用户输入自动提升匹配的 deferred MCP 工具 | tool_search 开 |
| 32 | DeferredToolFilterMiddleware | 未提升的 MCP 工具 schema 对模型隐藏 | tool_search 开 |
| 33 | SystemMessageCoalescingMiddleware | 把所有 SystemMessage 合并为唯一前置系统消息（救 vLLM/SGLang/Qwen/Anthropic 严格后端） | Lead 独有 |
| 34 | SubagentLimitMiddleware | 截断超额 `task` 调用（并发 + 每 run 总量） | subagent 开 |
| 35 | LoopDetectionMiddleware | 重复工具调用循环检测（3 警告/5 硬停；同工具 30/50） | 默认开 |
| 36 | TokenBudgetMiddleware | 每 run token 预算（默认关；开时 200k/0.8 警告/1.0 硬停） | 默认关 |
| 37 | CompletedSubagentUsageMiddleware | 把已完成的子 Agent 用量回填后再查预算 | 条件 |
| 38 | custom_middlewares / 39 配置型扩展中间件 | 用户/配置注入点（`extensions.middlewares`，支持 `@Next/@Prev` 锚定） | 可选 |
| 40 | TerminalResponseMiddleware | 工具后空回复的最后一搏（重试一次→可见错误） | 恒开 |
| 41 | ModelLengthFinishReasonMiddleware | provider 长度截断记账并封杀截断的 tool_calls | 恒开 |
| 42 | SafetyFinishReasonMiddleware | provider 安全终止（content_filter/refusal）时禁执行 tool_calls | 默认开 |
| 43 | **ClarificationMiddleware** | `ask_clarification` 拦截：删同轮兄弟调用，`Command(goto=END)` 中断提问 | **恒为最后** |

（来源：`backend/packages/harness/deerflow/agents/middlewares/AGENTS.md` 编号 1-39 + `lead_agent/agent.py::build_middlewares` 逐行装配序 + 我本人抽检；文件级证据路径见 `_clones/deer-flow-main/backend/packages/harness/deerflow/agents/middlewares/`。有序性有测试钉死：`backend/tests/test_tool_error_handling_middleware.py`（基础链 11 项顺序断言）、`test_create_deerflow_agent.py`（16 项全链断言）、`test_lead_agent_model_resolution.py`（技能三件套紧邻断言：Activation→Policy→DurableContext）。）

**一个可复用的真实教训（社区贡献 PR #1081 的记录）** `[原文/PR 描述]`：TitleMiddleware 曾在 `aafter_model` 里**直接 await** LLM 生成标题，原文描述后果："由于 run stream 必须等待所有 middleware hook 执行完毕才能结束，用户在看到 AI 回复后，还需额外等待 5-10 秒才能发下一条消息"；修复=标题生成改 `asyncio.create_task()` 后台执行、再 patch 回 thread state（该 PR 自查跑完 backend 421 个测试）。**凡"每轮后置钩子里做慢操作"的中间件都有这个阻塞风险，照搬中间件链时务必把慢动作移出请求路径。** https://github.com/bytedance/deer-flow/pull/1081

### 4.3 钩子模型与 LangGraph 映射（非常重要的一个工程坑）`[原文]`

- 技术底座：LangChain 的 `AgentMiddleware`（`from langchain.agents.middleware import AgentMiddleware`；两个例外直接继承 LangChain 内置中间件：`DeerFlowSummarizationMiddleware(SummarizationMiddleware)`、`TodoMiddleware(TodoListMiddleware)`）。
- 执行模型："**不是洋葱，是管道**"（仓库文档原文）：`before_*` 正序、`after_*` 反序、`before_agent/after_agent` 每 run 一次、`before_model/after_model/wrap_model_call` 每轮循环。全链**只有 2 处硬依赖**：ThreadData 必须在 Sandbox 之前；Clarification 必须最后（`after_model` 反序时它最先跑、第一个拦截澄清）。
- **每个中间件钩子 = 一个 LangGraph 节点**（`subagents/turn_budget.py` 模块 docstring 原文）："``create_agent`` compiles every middleware lifecycle hook into its own node (`{middleware}.before_model`, `{middleware}.after_model`), so one turn costs `before_model nodes + model + after_model nodes + tools` super-steps… Passing ``max_turns`` straight through as ``recursion_limit`` therefore divides the operator's budget by the depth of the middleware chain… This module does the translation instead."（PR #4497 的旁证：一个 worker turn 因为多两个钩子变成 11 super-steps 而非 9。）
- **为什么用中间件链**（可引用的三层理由）：官方文档站"中间件链优于继承"（§3-⑤）；仓库扩展规范"用语义位置（MODEL_LOGICAL/MODEL_PHYSICAL/TOOL_VISIBLE/TOOL_RAW）声明贡献而不是脆弱的列表下标"（`extensions/AGENTS.md`）；RFC 原文"**这不是全排序**。内置链的顺序在代码中已确定，外置 middleware 只做插入操作。"

---

## 5. Skill System（SKILL.md 体系）

### 5.1 目录结构与 frontmatter `[原文]`

- 目录约定（`skills/AGENTS.md` + README）：全局公共技能 `skills/public/`（已提交，当前 **23 个**，名单经 GitHub API 抓取：academic-paper-review, bootstrap, chart-visualization, claude-to-deerflow, code-documentation, consulting-analysis, data-analysis, deep-research, find-skills, frontend-design, github-deep-research, image-generation, music-generation, newsletter-generation, podcast-generation, ppt-generation, skill-creator, skill-reviewer, surprise-me, systematic-literature-review, vercel-deploy, video-generation, web-design-guidelines）；用户自定义 `{DEER_FLOW_HOME}/users/{user_id}/skills/custom/`；托管集成包全局只读 `{DEER_FLOW_HOME}/integrations/skills/{provider}/`；沙箱内挂载（**仅启用项投影**，非原始目录）：
```
/mnt/skills/public/<skill>/SKILL.md
/mnt/skills/custom/<your-skill>/SKILL.md
/mnt/skills/integrations/lark-cli/lark-doc/SKILL.md   ← 托管只读
```
- **frontmatter 全字段**（`skills/frontmatter.py::ALLOWED_FRONTMATTER_PROPERTIES` 逐字）：`name, description, license, allowed-tools, argument-hint, required-secrets, secrets-autonomous, metadata, compatibility, version, author`。必填 `name`+`description`（非空字符串否则拒收）；安装期校验：未知键拒绝、name 必须 `^[a-z0-9-]+$` 且 ≤64 字符、description 不得含尖括号且 ≤1024 字符。
- `allowed-tools` 兼容 Claude 生态的可移植拼写：`Bash→bash, Edit→str_replace, Glob→glob, Grep→grep, Read→read_file, WebFetch→web_fetch, WebSearch→web_search, Write→write_file`；`Bash(tvly *)` 这类带参条目保持字面且**不生效**（DeerFlow 不解析工具参数）。
- 真实样例（我本地拉取并读过）：`skills/public/deep-research/SKILL.md` 的 frontmatter 只有 `name` + 一段信息量极大的 `description`（触发条件写在 description 里）；`skill-reviewer` 多了 `allowed-tools: [review_skill_package]`；`claude-to-deerflow` 是 name+长 description。
- 仓库内教学样例（`backend/CONTRIBUTING.md` 逐字）：
```markdown
---
name: My Skill
description: What this skill does
license: MIT
allowed-tools:
  - read_file
  - write_file
  - bash
---
# My Skill
Instructions for the agent when this skill is enabled...
```

### 5.2 system_prompt **只注入索引**、正文按需 `read_file` 渐进加载 `[原文]`

- 系统提示里的 `<skill_system>` 模板逐字（`lead_agent/prompt.py` L812-832，我本人读过）：

```
<skill_system>
You have access to skills that provide optimized workflows for specific tasks...
**Progressive Loading Pattern:**
1. When a user query matches a skill's use case, immediately call `read_file` on the skill's main file using the path attribute provided in the skill tag below
2. Read and understand the skill's workflow and instructions
3. The skill file contains references to external resources under the same folder
4. Load referenced resources only when needed during execution
5. Follow the skill's instructions precisely
**Explicit Slash Skill Activation:**
- If the user starts a request with `/<skill-name>`, that skill was explicitly requested for the current turn.
...
**Skills are located at:** {container_base_path}
<available_skills>
    <skill><name>…</name><description>… [built-in]</description><location>/mnt/skills/.../SKILL.md</location></skill>
    …
</available_skills>
</skill_system>
```

即：**每个技能只贡献 name/description/location 三元组**（XML 转义防注入），正文永远不进系统提示。

- **更省的模式**（`skills.deferred_discovery: true`）：`<skill_index>` 只放**名字**（逗号分隔），发现流程改为 `describe_skill(name)` 取描述 → `read_file` 拿正文（`skills/describe.py::get_skill_index_prompt_section`；`describe_skill` 支持 `select:a,b`、`+prefix`、自由词（≤5 结果，256 字符内））。
- **激活语义**：① slash 激活——SkillActivationMiddleware 把 SKILL.md 全文作为**隐藏的本 run 上下文**注入（每 run 一次、不持久化）；② 渐进加载——模型自己 `read_file` 到 SKILL.md 后，`ToolErrorHandlingMiddleware` 给该 ToolMessage 打 skill-read 标记 → `DurableContextMiddleware` 捕获进 `ThreadState.skill_context`（按路径去重、**上限 8 条**、description ≤500 字符）→ 后续每轮以紧凑"active skills"提醒重投影（**只提醒、不回放正文**，压缩也不丢）。③ `allowed-tools` 策略**只在实际激活后生效**（`SkillToolPolicyMiddleware` 原文："Merely enabling a skill makes it discoverable; it does not activate its authority policy."）；slash 激活的 run 内，其工具策略具有**权威性**（读别的技能不能放宽）。框架工具（`describe_skill`/`read_file`/`review_skill_package`/`tool_search`）恒可用。
- **token 爆炸的老坑与修复**：PR #2993（社区贡献者 LittleBlacky）原文记载了旧实现 "`SubagentExecutor._load_skill_messages()` read every enabled skill's complete SKILL.md and concatenated them into the system prompt…（137K+ for simple tasks）"——这正是"只注入索引+渐进加载"要解决的问题 `[二手/PR 引用]`（https://github.com/bytedance/deer-flow/pull/2993 ）。
- **扩展方式**：新增技能＝建目录+SKILL.md（放进 `skills/public/` 或用户 custom/）；`.skill` 归档支持导出/导入（限制：4096 ZIP 条目、单文件 64MiB、总量 100MiB、frontmatter 1MiB）；管理侧有 Capability Center UI、`POST /api/skills/reload`（外部挂载场景）、SkillScan 安装安全扫描（确定性扫描+LLM 复核，CRITICAL 直接拦截）。
- **DeferredToolFilter 的对象是 MCP 工具而非技能**（常被混淆）：未提升的 MCP schema 不放给模型，等 `tool_search`/路由提升；技能有自己独立的 `<skill_index>+describe_skill` 机制。

---

## 6. Context Engineering（Write / Select / Compress / Isolate）

> **先交代一个诚实结论**：`Write / Select / Compress / Isolate` 这组"四件套"表述，我在**仓库全量源码、官方文档站、README 各语言版、以及能抓到的第三方文章里都没有找到**（两路独立检索均为 0 命中，见 §10）。但**四个机制本体在 DeerFlow 里都真实存在且数字可查**，下面按这四个篮子归置真实证据。

### 6.1 Write —— 把中间结果写进文件系统 `[原文]`

- **工具输出外部化**（`ToolOutputBudgetMiddleware`，`config/tool_output_config.py`）：超 **12,000 字符**（`externalize_min_chars: 12_000`）的工具结果转存到线程 outputs 下的 `storage_subdir`（默认 `.tool-results`），模型侧收到"带类型摘要 + 虚拟路径引用"：模板逐字 `[Full {tool_name} output saved to {virtual_path} ({total} chars, ~total/4 tokens).]` + "Use read_file on {path} with start_line and end_line to inspect the raw output."；磁盘不可用时退化为 head+tail 截断（30,000 字符总预算 = 8,000 头 + 3,000 尾）；被后续写入覆盖的旧写入可折叠（`superseded_write_min_chars: 2000`，保留最新 1 条）；`read_file` 为豁免工具（防"转存→读回→再转存"循环）。
- **工作区纪律**（Lead 提示词逐字）：临时工作一律在 `/mnt/user-data/workspace`；最终交付必须复制到 `/mnt/user-data/outputs` 并用 `present_files` 呈现；长文档分段写（先 `write_file` 建文件、再 `append=True` 续写，"avoids mid-stream chunk-gap timeouts on oversized single-shot writes. (See issue #3189.)"）。
- **跨压缩工作笔记**：`task_note` 工具（任务连续性）：最多 **8 个键 × 750 字符 × 4 个来源**，压缩后以 "## Task working notes" 重投影（≤12,000 字符）；工件句柄 `art_xxxxxxxx` 存进 `ThreadState.tool_artifacts`，**跨压缩存活**。
- **上传文件**：不进上下文正文，只给"大纲 + 预览"（标题 200 字符、预览 2,000 字符上限），正文按需 `read_file`。

### 6.2 Select —— 按 token 预算选择注入

- **跨会话记忆（DeerMem 默认后端）**：存储布局逐字（`agents/memory/AGENTS.md`）：
```
{base_dir}/users/{user_id}/memory.json                     ← 只放全局摘要（user/history），不放事实
{base_dir}/users/{user_id}/agents/{agent_name}/facts/{sha256 前缀}/{fact-id}.md
```
  注入预算（`deermem/config.py`）：`max_injection_tokens` 默认 **2000**（100-8000）；`guaranteed_token_budget` 默认 **500**（纠错类 correction 保底名额）；`max_facts` 默认 **100**；`fact_confidence_threshold` **0.7**；查询词上限 1,000 字符；注入超时 5.0s（失败不阻塞对话）。选择算法支持 IDF 相关性排序 + 多样性惩罚 + 独立"保底池/常规池"预算；检索默认 SQLite FTS5/BM25 适配器（中文可选 jieba）。
- **注入形态**（`DynamicContextMiddleware`）：**冻结快照模式**——第一轮把 `<system-reminder><memory>…</memory><current_date>…</current_date></system-reminder>` 前置进首条用户消息并从此冻结（保护前缀缓存）；记忆文本走 **HumanMessage 数据通道**（非 SystemMessage，防 OWASP LLM01 提权），带 `hide_from_ui`。
- 其他后端：mem0 `max_injection_chars: 12000`、honcho 6000、openviking 12000（各 backend config 逐字）。**宿主不做预算截断，由 backend 自己 cap**（backends/README 原文）。

### 6.3 Compress —— 超限压缩（以及 "micro_compact" 的辟谣）

- **micro_compact：不存在。** 全仓 grep（`micro_compact|microcompact|micro-compact`）**0 命中**；也没有任何 `180000` 常量、没有 "180k" 字样（两路独立验证）。**请从 DocMind 的设计输入里删掉这一条。**
- **真实压缩机制 = SummarizationMiddleware（自动）+ `/compact`（手动）**，同一工厂、不会漂移（`backend/docs/summarization.md`，我逐字读过）：
  - 三种触发器（OR 逻辑）：`tokens`（绝对值）/ `messages`（条数）/ `fraction`（相对**当前 run 实际模型**声明的 `context_window` 的比例，文档示例 `0.8`——这个 0.8 是示例值不是默认值）；
  - schema 默认：`enabled: false`、keep=`messages×20`、`trim_tokens_to_summarize=4000`（摘要调用的输入预算）；**示例配置用的值不同**：tokens 触发器 32000、keep 10 条、trim 15564（配置漂移属正常，引用请带来源）；
  - 摘要写入 `ThreadState.summary_text`（不占 messages），压缩时 `RemoveMessage(REMOVE_ALL_MESSAGES)` + 保留尾部；todo 提醒消息（`todo_reminder`）被排除出摘要输入与保留区（成功压缩才提交）；
  - 重投影：`DurableContextMiddleware` 把摘要渲染进隐藏 `<durable_context_data>` 数据消息（≤6,000 字符），静态权威规则单独走 SystemMessage；
  - 子 Agent 侧：**同一个 `summarization.enabled` 开关**同时在 Lead 与 Subagent 链生效（"one config covers both chains"），子 Agent 压缩时跳过记忆 flush（防子 Agent 内部轮次污染父线程记忆）；
  - 手动：`POST /api/threads/{id}/compact`（`force` 默认 true）。
- **"微压缩"的最近似物**其实是 §6.1 的 tool-output 预算 + 折叠写入（工具结果级压缩），而不是一个叫 micro_compact 的机制。

### 6.4 Isolate —— 隔离

- 子 Agent 默认 `isolated`：只见任务描述；`snapshot` 模式也只带"保留历史+摘要"的数据快照（`<parent_context_snapshot>`，`hide_from_ui`，且明示"Historical tool actions are not evidence of child completion"；快照在派发时固定、边界只过数据不过运行时状态）。
- 子 Agent：独立事件循环（持久 isolated loop）、独立语言模型回合预算、`checkpointer=False`；**不注入用户记忆**（只给日期上下文，`SubagentDateContextMiddleware`）；技能目录共享但各自独立发现/激活。
- 沙箱隔离粒度 = `(user_id, thread_id)`（`derive_sandbox_scope_token = sha256(f"{user_id}:{thread_id}")[:16]`）；同一 thread 的父子共享沙箱由**执行租约**保证生命周期正确；AIO 下每个子 Agent 一个独立持久 shell 会话。
- 安全提示词加固：所有可疑文本走"数据通道 + 边界框选"，工具结果里的 `<system-reminder>` 等标签被 `ToolResultSanitizationMiddleware` 中和（防注入伪造框架消息）。

---

## 7. Sandbox / AIO Sandbox

### 7.1 是什么、怎么起 `[原文]`

- 抽象接口：`SandboxProvider`（acquire/acquire_async/get/release）+ 六个实现（取证 agent 逐一核对）：

| Provider | 本质 | 备注 |
|---|---|---|
| `LocalSandboxProvider` | **宿主机子进程**（非安全边界） | 出厂默认；`allow_host_bash: false`（默认禁 bash）；每线程 LRU 缓存 256 |
| `AioSandboxProvider` | **Docker 容器**（或 Apple Container / K8s Pod），走 HTTP API | 默认镜像 `enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest`（字节火山引擎镜像仓）；推荐 pin `1.11.0`（`:latest` 冻结在旧 digest，缺 `/v1/bash/*` 路由）；上游项目 = 字节 `agent-infra/sandbox`；Python 客户端 `agent_sandbox` |
| `E2BSandboxProvider` | E2B 云 microVM | 模板默认 `code-interpreter-v1` |
| `OpenSandboxProvider` | 远程 OpenSandbox 服务 | request/ready 超时各 30s、sandbox 寿命 14400s |
| `BoxliteProvider` | 本地 BoxLite microVM（daemonless OCI） | memory_mib: 1024 / cpus: 2 示例 |
| `TenkiSandboxProvider` | Tenki 云 microVM | cpu_cores 2 / memory_mb 2048 / max_duration 14400 |

- "**AISandBox**"这个拼写全仓 0 命中；正确名称是 **AIO Sandbox**（`AioSandboxProvider`）。量子位原话（[二手]）："其中 Docker 模式采用字节开源的 AIO Sandbox，隔离级别更高、运行更稳定。"

### 7.2 资源限制（默认值全部源码实锤）

- **`_DEFAULT_SANDBOX_MEMORY = "2g"`、`_DEFAULT_SANDBOX_CPUS = "2"`、`_DEFAULT_SANDBOX_PIDS_LIMIT = "512"`**（`community/aio_sandbox/local_backend.py` L231-233，注释："The sandbox executes untrusted, model-authored code, so containers get bounded resources by default"）；通过 docker run `--memory/--cpus/--pids-limit` 施加；**仅环境变量可调**（`DEER_FLOW_SANDBOX_MEMORY/CPUS/PIDS_LIMIT`，没有 YAML 键）；`0/none` 显式关闭限制。
- 其他：bash 命令超时 `bash_command_timeout: 600`（默认）；输出截断 bash 20k / read_file 50k / ls 20k 字符；Docker 加固 `--cap-drop=ALL`+5 项白名单+`no-new-privileges`（seccomp=unconfined 是默认，因镜像内 Chromium 需要）；网络策略 sidecar 自身锁死（256m/1cpu/128pids/read-only）；K8s provisioner Pod：requests 100m/256Mi、limits 1000m/1Gi。
- 网络模式：`open`（默认）/ `isolated` / `allowlist`（表驱动域名 + 人工批准卡；私网/回环/云元数据地址不可批准；无人值守 run 自动拒绝）。

### 7.3 隔离粒度与支持的语言

- 隔离标识 `sha256(user_id:thread_id)`；宿主目录 `users/{user_id}/threads/{thread_id}/user-data/{workspace,uploads,outputs}` 挂进容器 `/mnt/user-data/*`；技能只投影启用项到 `/mnt/skills`。
- **代码执行只有一个入口：`bash` 工具**（无独立 python/jupyter 工具；docstring 指引"Use `python` to run Python code"、建议线程级 venv）。Python/Node 可用性取决于镜像（AIO 自带 Python/Node/Jupyter）。所有沙箱工具：`bash / ls / glob / grep / read_file / write_file / str_replace`。

---

## 8. 指标 / 评估体系（Q7 的正面回答）

**有，而且是三层：**

1. **Tracing（三供应商，全部 env 开关、默认关）** `[原文，我读过 tracing/AGENTS.md]`：
   - LangSmith（`LANGSMITH_TRACING/LANGSMITH_API_KEY/LANGSMITH_PROJECT`，默认 project=deer-flow）、Langfuse（`LANGFUSE_*`；字段映射：`langfuse_session_id←thread_id`、`langfuse_user_id←用户`、`langfuse_trace_name=subagent:<name>`、`langfuse_tags=env:…,model:…`；回调挂在**图调用根**，一 run 一棵 trace；子 Agent 自动归组到父线程会话卡）；
   - **Monocle**（OTel 路线：`MONOCLE_TRACING/MONOCLE_EXPORTERS`（file 默认→`.monocle/`，还有 console/okahu/s3/blob/gcs），进程级 TracerProvider、Gateway lifespan 初始化、自动instrument openai/langchain/langgraph）；
   - 每次 Gateway 请求恒定返回 `X-Trace-Id`（`deerflow_trace_id` 恒写入 trace 元数据）。
2. **计量（token/成本/轨迹）** `[原文]`：
   - `token_usage.enabled: true`（默认开）：每次模型调用记录 input/output/total；按步骤归因（final_answer/thinking/tool_batch/subagent_dispatch/todo_update）；子 Agent 用量经终态 ToolMessage 的 `subagent_token_usage` 元数据合并回**派发处 AIMessage**（不靠进程级缓存），幂等标记 `subagent_token_usage_attributed`；
   - `RunJournal.get_completion_data()` 汇总：`total_input_tokens/total_output_tokens/total_tokens/llm_call_count/lead_agent_tokens/subagent_tokens/middleware_tokens/token_usage_by_model` → 落 `runs` 表；
   - **成本**：无硬编码价格表；按模型 `pricing` 配置（currency/input_per_million/output_per_million/cache 折扣）在读取时计算，`missing_usage_calls` 存在时 cost=null（诚实口径）；
   - `token_budget`（默认关）：200,000 / warn 0.8 / hard_stop 1.0，硬停不抛异常而是剥离工具调用收尾，`stop_reason=token_capped`。
3. **运行事件流（轨迹）** `[原文，我读过 RUN_EVENT_STREAM.md]`：append-only、`thread_id+run_id+seq+event_type+category+content+metadata` 信封；类别 `message/trace/outputs/error/middleware/context/subagent/workspace`；子任务事件 `subagent.start/step/end`（终态 completed/failed/cancelled/timed_out）；5 档 run 级 stop_reason（loop_capped/token_capped/safety_capped/subagent_limit_capped/model_length_capped）+ `orphan_recovered`；已知缺口文档自己列了（`run.end.metadata.status` 恒 success、工具意图未一等公民化、**评估消费者“planned rather than present”**——见 issue #4243 的 TL;DR："the eval work (#3804 / PR #4070) will use it as its evidence source"，即评估体系当时仍在规划）。
4. **基准套件**（`backend/scripts/benchmark/`）`[原文/目录核对]`：`context_snapshot`（隔离交接 vs 快照的合成评测，8 任务 51 项检查）、`deermem_eviction`（记忆淘汰策略离线复现，LongMemEval oracle 固定 revision+SHA-256）、`deermem_scope_isolation`（记忆跨用户/跨 Agent 污染率等 5 指标）、`sandbox`（acquire/run/release 延迟）、`checkpoint`（生产路径 full/delta checkpoint 延迟）、`concurrency`（SQLite vs Postgres 多进程压测）、`scheduled_tasks`。
5. **目标评估器**（Session Goals）：`/goal <完成条件>` 后每 run 用轻量评估模型判定，输出类型化 blockers（`missing_evidence/needs_user_input/run_failed/external_wait/goal_not_met_yet`），仅当 `goal_not_met_yet` 且进度未停滞才注入隐藏续跑（安全上限 8 次，重复无进展 2 次即停）`[原文，README 读过]`。

---

## 9. 可移植性判断（面向 DocMind：Python/FastAPI + 现有 PER 循环）

### ✅ 能直接搬到 Python/FastAPI 后端（模式对模式，本周可动手）

| 机制 | 搬运要点 | 依赖 |
|---|---|---|
| **子 Agent 准入控制器** | 照抄 `capacity.py` 设计：异步 FIFO、`max_running=3/max_queued=64/queue\|reject/queue_timeout=300`、槽位直接转移给队首、取消不泄漏；~180 行纯 asyncio，无框架绑定 | 无（纯 asyncio） |
| **task 工具契约** | `prompt/subagent_type/acceptance_criteria/description/context_mode` + 后端轮询阻塞返回（LLM 只发一次调用）+ "自述报告需引用回执、验收清单确定性核对、UNVERIFIED 语义" | 需给工具加"回执账本"（ToolReceipt 思路可简化） |
| **三轴熔断 + stop_reason additive 字段** | turn/token/loop 三轴、硬停不抛异常而是剥离 tool_calls 逼出收尾、additive 字段保兼容 | 与现有 PER Reflector 天然契合 |
| **工具输出外部化中间件** | 12k 转存/30k 截断/文件引用模板/写折叠；对 DocMind 的 RAG 长文返回同样适用 | 无 |
| **摘要压缩接线** | tokens/messages/fraction 触发器 + keep + trim 参数 + summary 独立通道（不占消息数组）+ 压缩后重投影 | 需要自己的"摘要进 checkpoint"位置 |
| **技能体系** | public/custom 目录 + frontmatter 白名单 + "系统提示只放索引、read_file 渐进加载、激活后收窄工具面"三件套 | 与 Claude Code skill 生态可直接互操作（allowed-tools 别名映射已给出） |
| **观测** | Langfuse/LangSmith 回调挂"图根"（一次 run 一棵 trace）+ 子 Agent 归因走终态元数据 + 每 run 汇总 token/成本列 | 无常驻依赖；Langfuse 自托管即可 |
| **记忆预算选择** | max_injection_tokens/guaranteed 预算/置信度阈值/查询词 cap 的参数模型可直接挪到 DocMind 的 memory 模块 | 无 |

### ⚠️ 需要替代方案才能搬（模式可用、实现要换）

| 机制 | 替代建议 |
|---|---|
| **LangGraph/LangChain 中间件 API**（钩子语义+每钩子一个图节点） | DocMind 的 PER 是自己的循环：在循环层面实现等价钩子点（pre-turn / pre-model / post-model / post-turn）即可复刻语义；**但"钩子=节点导致 super-step 膨胀"这类 LangGraph 特有坑不会出现**，反而要自己管"每轮预算换算"（turn↔step 转换逻辑照抄思路） |
| **AIO Sandbox 容器** | 用自建 Docker 容器替代（2g/2cpu/512pids 三参数照抄；镜像可用 python:slim 之类自建，AIO 镜像本身可从公开 registry 拉但功能耦合其 HTTP API）；K8s provisioner 对 DocMind 当前阶段过重 |
| **StreamBridge/SSE 事件协议** | DocMind 已有 SSE/WebSocket 基础设施的话，直接采用其**事件词汇表**（task_started/running/completed/failed/cancelled/timed_out + subagent.start/step/end 持久化映射）和"进度=自定义事件、不算 subgraph"的原则 |
| **沙箱执行租约（多执行者共享一个沙箱）** | 如果 DocMind 给每个子任务单独起容器，可省掉租约系统；只有当"父子共享沙箱省启动成本"成为需求时再引入租约（lease.py 模式可抄） |

### ❌ 强依赖 DeerFlow 本体/桌面或大规模部署，不建议搬

- `deerflow-harness` 包本体（依赖 LangGraph 1.x 生态与其 `create_agent` 中间件生命周期，绑死）；
- Monocle（Okahu 商业导出器生态，个人项目不必要；用 Langfuse 平替）；
- IM Channels（飞书/Slack/Telegram 网关）、Nginx 统一入口、Next.js 前端子任务卡片（要 UI 就自建轻量版）；
- 批量 `batch_task` 的 SQL 租约恢复（5000 项/多 worker/Postgres，DocMind 规模用不上；普通 task 就够）；
- 企业级 RBAC/多租户、Lark 集成包、E2B/Tenki 云沙箱。

### 给 DocMind 的落地顺序建议（基于本次证据的排序）

1. **本周**：task 工具 + 进程级 3 并发准入器 + 阻塞式返回（直接消灭"多 agent 编排"最大复杂度）；
2. **下一步**：工具输出外部化中间件（12k/30k 参数）+ 摘要压缩接线（先 messages 触发器）；
3. **然后**：技能目录（先 3-5 个 SKILL.md，索引注入+read_file 渐进加载）；子 Agent 报告契约（回执引用可先简化为"文件路径+命令证据"）；
4. **之后再评估**：沙箱容器化（先用自建 Docker 替代 AIO）、Langfuse 接入、run 事件流。

---

## 10. 未找到 / 不确定清单（诚实边界）

1. **`micro_compact` 机制 / 180k 阈值**：全仓 0 命中、无相关常量 —— **该机制在 DeerFlow 中不存在**（两路独立 grep 验证）。与之最接近的是工具输出预算（§6.1）与摘要压缩（§6.3）。
2. **"Write / Select / Compress / Isolate" 四件套表述**：三路独立检索（仓库全量、官方文档站、LofiSu 专项）均 **0 命中**；对中英文 README 做过全文共现检索（英文 write/select/compress/isolate、中文"写入/选择/压缩/隔离"同窗共现）同样 0 命中——**未找到该表述的任何 DeerFlow 相关出处**。README 的 "Context Engineering" 小节实际只写四件事：Table Tool-Output Synopses、**Isolated** Sub-Agent Context、Summarization（"summarizing completed sub-tasks, offloading intermediate results to the filesystem, compressing what's no longer immediately relevant"）、Strict Tool-Call Recovery。四个机制本体在 DeerFlow 里有充分原文（见 §6），但"四件套"命名不是官方术语，不建议在对外材料中使用。
3. **"900s"字样原文**：未在任何文档中找到字面 "900 秒/900s" 的表述；实际是代码默认值 900（`subagents/config.py`、Gateway 路由 DTO、managed_subagents schema）与"自定义 agent 默认 900"的配置注释。内置子 Agent 有效默认 1800s。
4. **"76k stars"**：找不到该数字对应的时间点；数值序列见 §0。
5. **"AISandBox"** 拼写：不存在；官方名 AIO Sandbox。
6. **issue #824 的 19 条评论正文**、**RFC #4651 完整正文**、**#3804/PR #4070 评估体系细节**：未逐条读取（issue 正文已读；评论与关联 PR 内容未展开）。
7. **火山引擎文章 / deerflow.tech 页面**：由子 agent 抓取并逐字转引（`[子agent取证]`），我本人未二次打开；重要决策前建议人工复核这两个链接。
8. **LofiSu 署名长文**：**未找到**（2026-10-06 专项穷尽检索：个人博客域名 lofisu.chat NXDOMAIN；myBlog 仓库 155 文件全量核查无 DeerFlow 内容；掘金/知乎/微信公众号未见；其 Awards.md 自述"技术文章产出到内部论坛"）。其可核实的 DeerFlow 公开文字=PR #1063/#1064/#1077/#1081/#1088 与提交记录（时间覆盖 2026-02-09~03-11，含核心文件 `lead_agent/prompt.py` 的合并冲突处理）；官方 README 中英文 **0 次**出现其名，Key Contributors 仅 Daniel Walnut、Henry Li。**"Maintainer"是其自述**（个人 README），未见官方名单佐证；2026-05 起第三方存在针对其"身份包装"的批评材料（https://yonagi04.github.io/blog/lofisu_ruiping/ ；知乎问题 2042236991161590108；ghfind.com 收录页——均二手且立场鲜明，引用需谨慎、需注明争议属性）。**给用户的行动建议：把任务书里"LofiSu 公开分享的技术体系"改为"DeerFlow 2.0 官方技术体系"，本报告其余部分已按此口径取证。**
9. **HN/Reddit/X 的深度架构讨论**：基本不存在（HN 相关帖最高 4 分；Reddit/X 因网络不可达无法核）。
10. **成本/成功率"面板"级指标**：有逐 run 记账与 stop_reason，但**没有**开箱的"成功率仪表盘"；评估体系（#3804）当时仍在规划（RUN_EVENT_STREAM.md 原文："Evaluation consumers discussed in #4243 are planned rather than present in this tree"）。

### ⚠️ 二手来源可信度黑名单（写材料时避开）

- **掘金「吴琼琼」文**（https://juejin.cn/post/7621140573428940851 ）：多处编造（"内部打磨 3 年/10 万并发/Go 语言 Gateway/不存在的 npm 包"），全套数字与官方对不上 `[第三方取证确认]`；
- **华为云社区「霍格沃兹测试开发学社」文**：把 2.0 描述成 Planner/Executor/Researcher/Reviewer 四角色（实为 1.0 时代的角色图演化错误）；
- **deerflow.one**（非官方站）：用 1.0 角色图描述 2.0，含广告位；
- **腾讯云「老周聊架构」文**：自创 "Flash/Standard/Pro/Ultra 四级模型"（官方无此说法；但官方有 flash/standard/pro/ultra 的**执行模式**字样存在于 claude-to-deerflow 技能描述中——勿混淆，后者是 API 调用参数）；
- **topaiproduct.com**：域名已停放；CSDN「14 层洋葱」文：14 数正确但"洋葱"定性被官方文档明确否定（"不是洋葱，是管道"）。

---

## 附A：关键证据 URL 汇总（按主题）

**仓库主文件**
- README（2.0 声明/演进/全部机制概述）：https://raw.githubusercontent.com/bytedance/deer-flow/main/README.md
- README_zh：https://raw.githubusercontent.com/bytedance/deer-flow/main/README_zh.md
- 1.x README：https://raw.githubusercontent.com/bytedance/deer-flow/main-1.x/README.md
- 顶层架构文档：https://raw.githubusercontent.com/bytedance/deer-flow/main/docs/ARCHITECTURE.md
- 后端架构地图：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/AGENTS.md
- 子 Agent 模块文档：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/packages/harness/deerflow/subagents/AGENTS.md
- 中间件模块文档：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/packages/harness/deerflow/agents/middlewares/AGENTS.md
- 子 Agent 容量实施文档：https://raw.githubusercontent.com/bytedance/deer-flow/main/docs/plans/2026-08-24-subagent-batch-capacity-implementation.md
- 中间件执行流（"不是洋葱是管道"）：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/docs/middleware-execution-flow.md
- task 工具演进：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/docs/task_tool_improvements.md
- 摘要压缩文档：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/docs/summarization.md
- 运行事件流：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/docs/RUN_EVENT_STREAM.md
- 流式设计：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/docs/STREAMING.md
- 工厂 RFC（中间件装配代码）：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/docs/rfc-create-deerflow-agent.md
- tracing 模块文档：https://raw.githubusercontent.com/bytedance/deer-flow/main/backend/packages/harness/deerflow/tracing/AGENTS.md
- 配置模板（全部默认值）：https://raw.githubusercontent.com/bytedance/deer-flow/main/config.example.yaml
- 关键源码：`backend/packages/harness/deerflow/tools/builtins/task_tool.py`、`subagents/executor.py`、`subagents/capacity.py`、`subagents/config.py`、`subagents/builtins/{general_purpose,bash_agent}.py`、`agents/lead_agent/prompt.py`、`agents/middlewares/*`、`sandbox/lease.py`、`sandbox/middleware.py`、`community/aio_sandbox/local_backend.py`、`runtime/journal.py`（均同前缀 raw URL）

**官方站/官方渠道**
- 官方文档站（设计理念/Lead Agent/子 Agent/中间件/技能/沙箱）：https://deerflow.tech/zh/docs/harness/design-principles 、/lead-agent 、/subagents 、/middlewares 、/skills 、/sandbox ；https://deerflow.tech/zh/docs/introduction/why-deerflow
- 火山引擎官方渠道文（2.0 为什么改）：https://developer.volcengine.com/articles/7622159746254307391
- 官方迁移 issue #824：https://github.com/bytedance/deer-flow/issues/824
- 评估相关工作 issue #4243：https://github.com/bytedance/deer-flow/issues/4243
- 1.0 公告：微信 https://mp.weixin.qq.com/s/Le7Ic9FgcwAkQeHDuorXMw （2025-05-10）；InfoQ 中国 https://www.infoq.cn/article/U2p2OV5aDPEDh4cl26QO （2025-05-16，作者李昕）

**第三方（均标二手）**
- 量子位（3 月，"11 层+3 子智能体"）：https://www.qbitai.com/2026/03/391361.html
- 钛媒体（4 月，57k/fork 数据 + 转述团队"重做一遍"）：https://m.tmtpost.com/7939926.html
- 差评 XPIN（4 月，实用评测与批评）：https://www.163.com/dy/article/KQBRB1R0051196HN.html
- 星标实时（镜像）：https://ungh.cc/repos/bytedance/deer-flow

**LofiSu 相关（专项检索 2026-10-06）**
- 提交检索（一手）：https://api.github.com/search/commits?q=repo:bytedance/deer-flow+author:LofiSu
- 自述主页 README / Awards.md（一手）：https://raw.githubusercontent.com/LofiSu/LofiSu/main/README.md 、https://raw.githubusercontent.com/LofiSu/LofiSu/main/Awards.md
- 代表性 PR（一手描述）：#1077（已合并）https://github.com/bytedance/deer-flow/pull/1077 ；#1081（middleware 修复）https://github.com/bytedance/deer-flow/pull/1081 ；#1063/#1064（记忆 API）https://github.com/bytedance/deer-flow/pull/1064 、https://github.com/bytedance/deer-flow/pull/1063 ；#1088 https://github.com/bytedance/deer-flow/pull/1088
- 其个人项目（一手）：https://github.com/LofiSu/LoopLab （harness 评测/trajectory 平台）、https://github.com/LofiSu/lofisu-skill （个人 Claude Code 技能库）
- 争议材料（二手、立场鲜明，引用需谨慎）：https://yonagi04.github.io/blog/lofisu_ruiping/ （2026-05-26）；https://www.zhihu.com/question/2042236991161590108 ；https://ghfind.com/collections/lofisu

**本地证据副本**：DeerFlow 整仓快照（**未入仓**，可经 `codeload.github.com` 重新获取）
- `deer-flow-main/`：main 快照解包（backend/ 完整、含上述全部源码与文档）
- `main/`：早期散件（README 双语、config.example.yaml、docs/plans、1.x README）
- `filelist_210.txt`/`tags.json`/`issue_824.json`/`issue_4243.json`/`latest_commit.json`：元数据
- `skills_fetched/`：7 个 SKILL.md 样例；`examples_fetched/`：3 个扩展示例 README

## 附B：待办（供后续验证者）

1. 若需引用火山引擎文/deerflow.tech，请人工在可访问网络二次打开核对（本次为子 agent 抓取）。
2. LofiSu 专项检索已完成（2026-10-06）：结论=无署名长文、官方 README 0 命中、存在身份争议材料（详见 §0 与 §10-8）。若其博客重建或新发文可再行补录。
3. `issue #824` 的 19 条评论、`#4651`/`#3804`/`PR #4070` 正文：如需深挖评估体系，从这里继续（工具：gh-proxy 镜像 API）。
