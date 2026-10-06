# DocMind: 面试就绪路线图

> 目标：一个可部署、可观测、可验证的 PER Agent 项目。
>
> 面试官能访问、能截图、能讲出 trade-off。

> **⚠️ 本文件的时效声明**
>
> 初稿 **2026-06-11**。**2026-10-06 按代码与 CI 实测逐项复核过一遍**——
> 初稿里有多处把「已完成」标成了「未做」，以及一段与现状相反的面试答词，本次一并更正。
>
> 图例：**✅ 已完成并核实** · **⚠️ 部分完成** · **❌ 未开始** · **❓ 未能核实**
> 「核实」= 我在当前 `develop` 上找到代码证据或 CI 证据，不是凭印象。

---

## 路线图概览

| | 阶段 | 初稿状态 | **2026-10-06 复核** |
|---|---|---|---|
| S1 | 部署上线 | 未开始 | **❌ 未开始**（仓库里找不到线上地址） |
| S2 | Langfuse Tracing | 概览标完成 / TODO 未勾 | **⚠️ 代码已接，开关未开**（配置项在 `.env.example` 里是注释状态） |
| S3 | Benchmark | 概览标完成 / TODO 未勾 | **⚠️ 题目与运行器在，基线数据未落档** |
| S4 | ExecutionContext | 概览标完成 / TODO 未勾 | **✅ 已完成** — `app/agent/exec_context.py:58` |
| S5 | Skill Layer | 概览标完成 / TODO 未勾 | **✅ 已完成** — `app/agent/tools/skills.py:20/85/196` |
| S6 | 扩 Benchmark | 概览标完成 / TODO 未勾 | **⚠️ 题集已到 30 题**（`benchmark/questions/v2.json` / `v3.json` 各 30），对比数据未落档 |
| S7 | MCP Bridge | 概览标完成 / TODO 未勾 | **✅ 工具已完成** — `app/agent/tools/mcp_bridge.py:156`；「接入真实 MCP Server 验证」**❓ 未核实** |

其余可核实的规模数字（2026-10-06，来自 `e5b2eb18` 上一次成功的 CI）：

| 项 | 实测值 | 证据 |
|---|---|---|
| 后端测试 | **911 passed**（`pytest tests/`，含 MySQL + Redis） | CI `Integration Tests` job |
| 后端测试（无服务子集） | **877 passed, 1 skipped** | CI `Backend Tests (3.12)` 与 `(3.11)` 双侧一致 |
| 前端测试 | **111 passed** | CI `Frontend Tests` job |
| 工具数 | **30** | 30 个真实装饰器（AST 计数，30 个唯一工具名）；`@register_tool` 在 `backend/app` 内出现 39 处，其中 9 处是注释/文档字符串 |
| 语言 | 4（zh / en / ja / fr） | `frontend/src/locales/` |

---

## S1 · 部署上线 ❌

**目标**：`https://docmind.xxx.com` 可访问，Agent 对话可用。

**复核结论**：**未开始。** 仓库里（README、`deploy/`）找不到任何指向线上实例的地址。
Docker Compose 与 K8s 清单都在，但**没有部署记录**。

**待办**：
- [ ] 注册云服务器（阿里云/腾讯云学生机）
- [ ] 域名 + ICP 备案（或境外服务器免备案）
- [ ] Docker Compose 一键启动
- [ ] 前端配置 API 地址指向后端
- [ ] HTTPS（Let's Encrypt / Cloudflare）
- [ ] README 添加线上链接

> **这是整份路线图里唯一还完全没动的阶段，也是面试价值的最大瓶颈** ——
> 面试官点不开的东西，就只能靠讲的。

---

## S2 · Langfuse Tracing ⚠️

**目标**：每轮 Agent 请求在 Langfuse Dashboard 上呈现完整 trace。

**复核结论**：**代码已接，开关未开。**

- `backend/app/agent/observability.py` 里 langfuse 相关行 24 行 / 出现 29 次，span 埋点分布在
  `planner.py:195`（规划）、`executor.py:539/587/639`（执行步 + LLM generation）、
  `reflector.py:123`（反思）、`registry.py:464`（工具）、`memory_bridge.py:110`（记忆召回）
- **但** `backend/.env.example` 里 `LANGFUSE_PUBLIC_KEY` / `SECRET_KEY` / `HOST`
  **三行全是注释状态**；`observability.py:33-42` 的行为是「未配置 key 即整体禁用」

> 与 Prometheus 是同一个形态：**能力写全了，开关没打开**。
> 这一条对面试反而是个好素材——"我怎么发现自己有一套功能其实从来没生效过"。

**待办**：
- [x] `pip install langfuse` + 初始化
- [x] 5 个埋点（registry / memory / planner / executor / reflector）
- [ ] **在 `.env` 里配上真实 key，确认 trace 真的产生** ← 当前卡在这一步
- [ ] 验证 trace 树完整
- [ ] 截图放进 README

---

## S3 · Benchmark（10 题基线）⚠️

**目标**：先跑通评测流水线，得到完成率 + 关键词覆盖率。

**复核结论**：**题目与运行器在，基线数据没有落档。**

- 题集：`backend/benchmark/questions/v1.json`（10 题）、`v2.json`（30 题）、`v3.json`（30 题）
- `v3.json` 声明了三层评测结构（L1 能力 20 题 / L2 系统 10 题），指标为
  `keyword_coverage` / `completion` / `step_efficiency`
- 仓库里有一份 `backend/eval_first_report.json`（2026-06-15），
  内容是 **`total_scenarios: 10, passed: 0, failed: 10`**

⚠️ **那份报告是满盘皆输，并且挂了四个月没人看** —— 它没有被任何 CI 引用。
这本身就是一条可讲的工程教训：*评测跑出来了但没人接门禁，等于没跑*。

**指标定义**：
- 完成率 = count(非空回答) / total
- 关键词覆盖率 = count(匹配关键词) / count(总关键词)
- 平均步骤数 · 工具失败率

> **注意 `keyword_coverage` 是一个弱代理指标** —— 它衡量的是字符重合，
> 不是"答案对不对"。用它当唯一门禁会给出错误信号（这一点值得在面试里主动讲）。

**待办**：
- [x] 设计测试问题（`benchmark/questions/v1.json`）
- [x] 实现评测运行器 + 评分器
- [ ] **跑出并落档一份可复现的基线**，且接进 CI
- [ ] 写入 README

---

## S4 · ExecutionContext ✅

**目标**：把散落在各模块的状态收拢到一个对象。

**复核结论**：**已完成。** `app/agent/exec_context.py:58` 的 `@dataclass ExecutionContext`
含 `completed_steps` / `findings` / `failures` / `decisions` / `progress` / `duration_ms` / `total_tokens`，
并且已经在生产路径上流转（`loop.py:240` 创建，向 planner / executor / reflector 传递）。

**待办**：
- [x] 实现 `ExecutionContext` dataclass
- [x] `loop.py` 创建 ctx 并向下传递
- [x] `executor._execute_step_once()` 写入 findings / failures / decisions
- [x] `reflector.reflect()` 写入 decisions
- [ ] Langfuse 观察 ctx 数据 ← **依赖 S2 的开关**

> ⚠️ **一个已知缺口**：`total_tokens` 字段存在但**全仓零赋值** ——
> 也就是说"这次任务花了多少 token"目前答不出来。已登记为 issue。

---

## S5 · Skill Layer ✅

**目标**：Tool 之上加一层 Skill 编排。

**复核结论**：**已完成。** 三个 Skill 全部作为 Tool 注册：

| Skill | 位置 | 编排链路 |
|---|---|---|
| `deep_research` | `app/agent/tools/skills.py:20` | web_search → search_knowledge_base → 一段固定的「综合摘要」提示文本（**不调用 `extract_insights`**） |
| `document_comparison` | `app/agent/tools/skills.py:85` | get_document_info → extract_insights → cross_document_analysis（**不经过检索链**） |
| `knowledge_analysis` | `app/agent/tools/skills.py:196` | search_kb → extract_insights(framework) → generate_report |

**待办**：
- [x] 实现 `deep_research` 工具
- [x] 实现 `document_comparison` 工具
- [x] 实现 `knowledge_analysis` 工具
- [ ] Benchmark 对比：有无 Skill 的完成率/准确率变化 ← **依赖 S3 的基线**

---

## S6 · 扩 Benchmark ⚠️

**目标**：10 → 30 题。

**复核结论**：**题集已到 30 题**（`v2.json` 与 `v3.json` 各 30），
但**Skill Layer 前后的对比数据未落档**。

**待办**：
- [x] 扩到 30 题
- [ ] 跑 Skill Layer 前后的对比数据
- [ ] 写入 README

---

## S7 · MCP Bridge ✅ / ❓

**目标**：简历能写"兼容 MCP 协议"，面试能回答 MCP 相关问题。

**复核结论**：**桥接工具已完成**（`app/agent/tools/mcp_bridge.py:156` 的 `mcp_call`），
README 的工具表里也列了它。**但「接入 GitHub MCP Server 验证过」这一步 ❓ 未核实** ——
仓库的 `.env.example` 里找不到已配置的 MCP server。

**待办**：
- [x] 实现 `mcp_call` 工具（用官方 `mcp` Python SDK，未手写 JSON-RPC）
- [ ] 配置一个真实 MCP Server 并跑通一次调用
- [ ] 写入 README

> ⚠️ **安全说明**：`app/agent/config.py:21` 在 `DEFAULT_DISABLED_TOOLS` 里把 `mcp_call` 标为高危
> （宿主机文件读写 + 子进程环境变量泄露），它因此默认禁用，
> 且 `api/v1/endpoints/agent.py:42-48` 在**服务端强制合并**禁用列表，客户端无法解除。
> 这一点值得主动讲——它体现的是"知道自己在做什么"。

---

## 面试叙事

### MCP 是怎么接的？

> 用官方 `mcp` Python SDK 的 stdio client，包成一个 `mcp_call` 工具注册进 Tool Registry。
> 这样 Planner 完全不需要改——LLM 看到工具列表里有它，该用就会用。
>
> 范围上我**刻意只做了桥接**：不做 MCP Server 注册中心（硬编码 2-3 个够用）、
> 不做热加载（配置在 `.env` 里）、不做流式传输。**在工具生态只有两三个的时候，
> 为"未来可能的扩展"提前建基础设施，是典型的过度设计。**

> 原始版本这一节标题是「为什么不做 MCP？」——那是 2026-06 写的，7 月就实现了。
> 本次更正。

### 为什么不做长期 Runtime？

> 我的场景是知识问答，任务生命周期通常在 30 秒以内，请求-响应模型已经足够。
> Claude Code 那种持续运行的 Runtime 更适合代码执行场景——两者的问题域不同，
> 不是代际差距，是 trade-off。

> ⚠️ **这段立场正在被自己动摇**：目前 `AgentTask` 表与 `TaskStatus` 状态机已经建好，
> 但运行时零读写——中断一次 run，全部中间状态作废。如果要做长任务，
> checkpoint / resume 是必须补的。**面试时更诚实的说法是**：
> "我最初判断不需要，后来发现长任务场景下中断代价太高，所以正在补 resume"。

### 如何证明 Agent 比 RAG 好？

> ⚠️ **这段目前没有数据支撑，不要照读。** 原稿写的是
> "PER Agent 相比纯 RAG 的完成率提升 X%，关键词覆盖率提升 X%"——**两个 X 是占位符，一直没填。**
>
> 诚实的现状：题集有 30 题（`benchmark/questions/v3.json`），运行器也在，
> 但**完整基线还没跑出来**；最近一份落档的报告是 2026-06-15 的 `0/10`。
>
> **能讲的替代版本**：
> "我建了 30 题的评测集，但坦白说基线还没跑通 —— 最近一次落档是 10 题全挂，
> 排查下来主要原因之一是检索链路没有数据（索引是空的）—— 10 个场景里还有一类走飞书表查询、
> 不经过检索链，所以不能把 0/10 全归到这一个原因上。这件事教给我的是：
> **评测跑出来不接门禁等于没跑**，所以我下一步是把评测接进 CI。"

---

## 修改历史

| 日期 | 版本 | 修改 |
|---|---|---|
| 2026-06-11 | v1 | 初稿 |
| **2026-10-06** | **v2** | **按代码与 CI 实测逐项复核**：更正 S4/S5/S7 的状态（初稿标未做、实际已完成）；补上可核实的规模数字（911 后端 / 111 前端 / 30 工具）；修正「为什么不做 MCP」这段与现状相反的答词；标注「如何证明 Agent 比 RAG 好」缺数据支撑 |
