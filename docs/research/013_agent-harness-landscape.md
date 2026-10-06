# AI Agent "Harness" 版图调研 —— 桌面/CLI vs 前后端 Web，到底差在哪？

- **调研日期**：2026-10-06（8 路并行检索 + 本机一手文件取证）
- **回应的问题**：*"人家这些都是桌面版本的东西，但是我的这个 agent 为什么是前后端架构的，这两种一样吗？真正的效果上来说。"*
- **证据分级**：
  - 【一手】= 本机文件直读、或 `curl` 直取原文成功（本报告仅 Hermes/Pi/OpenClaw 相关条目达到该级）；
  - 【确证】= 官方文档/官方仓库/多源独立一致（多数经检索摘要转述）；
  - 【二手】= 单一第三方转述；
  - 【推测】= 有依据的推断；【未找到】= 检索后无证据。
- **方法学限制（重要）**：本机网络策略**全域拦截 WebFetch**（github.com 亦不可直开），大部分网页无法逐字核验；网络证据以 WebSearch 返回的搜索摘要为主（各 agent 检索预算 200 次/会话，多数已用尽）。本文凡标【确证】者均为**官方来源或多源一致**，但"确证"指来源等级，不等于本机逐字复核。**价格/星数等易变数字请落地时以官方页面为准。**

---

# 0. 一页结论（先回答你）

## 0.1 三句话

1. **不一样，但不一样的地方不是"桌面 vs 前后端"。** 桌面、CLI、Web、IDE 都只是**外壳**；真正决定"效果"的是两样东西：**(a) 行动面**——agent 的"手"和"钥匙"能伸到哪（你的文件系统/shell/内网/登录态？还是云沙箱里一个空 Ubuntu？还是只能调 API？）；**(b) harness 内核成熟度**——循环、上下文管理、工具质量、状态恢复、权限模型、评估闭环。
2. **你看到的"人家"其实也不全是桌面。** DeerFlow 2.0 是自托管 Web 应用（官方 README 自述 "open-source **super agent harness**"）；OpenHands 是 web-first 多前端；**你自己截图里那台 Supermew（icey1287/SuperMew）本身就是前后端架构**——LangGraph + checkpoint + eval 门禁全做在后端（见 `012_reference-rag-architecture.md`）。而 Pi/Hermes 的"桌面性"实质是"**本地运行时 + 多入口**"（TUI + 桌面端 + IM 网关 + IDE），并不是一个 GUI 独苗。
3. **效果层面：同一模型、同一循环、同一工具下，换壳不改成绩。** mini-SWE-agent 用约 100 行 Python、纯 bash、无 tool-calling，SWE-bench Verified 也拿到 >74%【确证·多源】；而**同一模型换 harness，公开榜单能差出 8–12 个百分点**（第三方统计口径）【二手】。所以你的问题不是"为什么是前后端"，而是"**内核还差哪几件**"——而这恰好是低成本可补的。

## 0.2 本质差异 vs 外壳差异（本文最重要的表）

| 维度 | 判定 | 判据 |
|---|---|---|
| **数据/凭据/内网/本地应用的可达面**（"手"与"钥匙"） | **本质**——决定能做什么任务 | 云 VM 只能看见你推上去/克隆下来的东西；"带你自己登录态的浏览器""本地未提交文件""内网服务"只在本地可达。2026 年所有大厂都在造桥（Remote Control / self-hosted runner / 本地 worker）——这本身就是"可达性是本质"的产业级证据 |
| **信任与合规边界**（数据出域、审计、谁负责隔离） | **本质**——决定谁允许你做什么 | ZDR/HIPAA 配置直接禁用云会话与远程控制；组织 IP 白名单会让厂托管云会话全部失败；反过来云形态把隔离责任转移给厂商 |
| **生命周期**（"谁的机器必须活着"） | **本质**——决定怎么用 | 本地=你的进程/电脑（远程控制要求"本地进程一直运行"）；云=厂商 VM，闲置回收，**回收时正在跑的后台工作会丢**；换来的是"关笔记本继续跑+并行 N 会话" |
| **隔离层级与可授予的自主度** | **本质** | 官方立场：`--dangerously-skip-permissions` 这类最高自主模式**只能在容器/VM/sandbox runtime 里用**；隔离等级直接决定你敢把多少审批关掉 |
| 会话存储介质（JSONL vs 数据库） | 外壳 | 两端都在对等实现（本地 resume / 云端会话列表 / 互相搬运都已有产品级方案） |
| 多会话视图、diff、分享、通知、流式延迟 | 外壳 | 双形态产品已证明可搬运；同一模型同一上下文管理下 token 成本≈相同 |
| 分发方式 | 外壳（双向） | CLI 一条命令装，但要有终端习惯；Web 免安装，但要账号+组织策略+连接仓库 |
| 表格、卡片、看板这类"状态可视化" | 外壳（Web 略优） | 交互/可视化是 Web 传统强项，但不改变 agent 能做什么 |

## 0.3 对你的直接映射（一句话版）

- **DocMind**：它已经是"**Web 形态的 harness**"——PER 循环、25 工具、上下文压缩（本仓库 `context.py` 注释自述 *"Inspired by hermes-agent's ContextEngine / ContextCompressor pattern"*）、四层记忆、SSE、replay、评估都在。缺的**不是形态**，而是：**"手"**（一个能触及用户真实环境的执行体——本地桥接，或云沙箱）和**几件内核**（任务级状态持久化/续跑、评估闭环落地；详见 A3/A6 及本文 §6）。
- **新项目（个人 AI 工作台）**：决策文档 v4 的"**入口无关、CLI 第一**"与 2026 年行业答案完全一致——**一个内核、多种入口**（Codex / Claude Code / Goose / Hermes / Pi / OpenClaw 全部如此）。
- **结论**：不需要"转做桌面"，也不需要为"是前后端"焦虑；要做的是**内核与形态解耦**，然后把"手和钥匙"按需补上。

---

# 1. "Harness" 是什么：定义、词源、2026 共识

## 1.1 两条词源谱系

| 谱系 | 含义 | 代表 |
|---|---|---|
| 旧义：评测脚手架 | test harness → 模型评测设施 | EleutherAI 的 `lm-evaluation-harness`（README 标题即 "Language Model Evaluation Harness"）https://github.com/EleutherAI/lm-evaluation-harness ；SWE-bench 通篇称评测设施为 "evaluation harness" https://github.com/swe-bench/SWE-bench |
| 新义：把模型变成 agent 的完整运行时 | 模型之外的一切：循环、工具、上下文、权限、持久化、观测 | Claude Code、Codex CLI、Hermes Agent、Pi…… |

- 词源重建有一篇专门论文：arXiv 2606.10106《What makes a harness a harness》（2026-06-08），原文即"*from the horse's tack to the classic test harness, to the machine-learning evaluation harness, and finally to the agent harness*"，并用 6 个真实 harness（含 Claude Code、Codex CLI、Aider、Cline、OpenHands、SWE-agent）验证其充要条件定义。https://arxiv.org/abs/2606.10106 【确证】
- **最早的高影响力使用**：Anthropic《Building agents with the Claude Agent SDK》（2025-09-29）："the agent harness that powers Claude Code" https://claude.com/blog/building-agents-with-the-claude-agent-sdk 【确证】。随后 Anthropic 以 harness 为题连发：《Effective harnesses for long-running agents》（2025-11-26，定义句 "The Claude Agent SDK is a powerful, general-purpose **agent harness**…"）https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- **负证据**：Anthropic 2024-12 的《Building effective agents》全文 0 次 "harness"、0 次 "scaffold"——该词不是从这篇来的。【确证】
- **谁第一个用**：【未找到】。Harrison Chase 2025-10-25 明说："'Agent Harness' is a term I'm just starting to see be used more (**I didn't come up with it**)" https://www.langchain.com/blog/agent-frameworks-runtimes-and-harnesses-oh-my 。METR 2025-03 的长任务评测里这一层还叫 **scaffold**（modular-public / flock-public）https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/
- **2026 年 2 月为什么突然爆**：Mitchell Hashimoto 的博文（2026-02-05，常被引为 "harness engineering" 出处，**此归属仅二手证据**）＋ OpenAI 的 Ryan Lopopolo《Harness Engineering: Leveraging Codex in an Agent-First World》（2026-02-11，提出最流行公式 **Agent = Model + Harness**，"Humans steer. Agents execute."）https://openai.com/index/harness-engineering/ ＋ Martin Fowler 站点（Birgitta Böckeler，2 月首谈、4 月成熟版：guides vs sensors、computational vs inferential）https://martinfowler.com/fragments/2026-07-13.html 【确证/报道口径】

## 1.2 为什么不说 framework 了

Harrison Chase 的三层分工（业内引用最多）【确证】：
> "**LangChain is an agent framework, LangGraph is an agent runtime, DeepAgents is an agent harness**" —— framework 给你积木；runtime 管理执行与状态；harness 是"有主张、开箱即用、真把事做完"的那一层。

Builder.io 的类比（2026-04）："**Node is the runtime, Express is the framework, Next.js is the harness**" https://www.builder.io/blog/agent-harness
"**The harness is the product**"这一说法确实存在（Pere Villega 2026-06-04 等一批同题文章；最早的出处【未找到】）https://perevillega.com/posts/2026-06-04-the-agent-harness-is-the-product/

## 1.3 2026 年收敛出的标准定义

| 来源 | 原句 |
|---|---|
| LangChain《The Anatomy of an Agent Harness》（Vivek Trivedy，2026-03-10） | "**Agent = Model + Harness**……If you're not the model, you're the harness. A harness is every piece of code, configuration, and execution logic that isn't the model itself. A raw model is not an agent. But it becomes one when a harness gives it things like state, tool execution, feedback loops, and enforceable constraints." https://www.langchain.com/blog/the-anatomy-of-an-agent-harness |
| 微软官方（Microsoft Learn / Build 2026，`AsHarnessAgent()` 进 SDK） | "An agent harness, sometimes called an AI harness, is the **runtime scaffolding that turns a language model into an agent that can perform work**."；Build 更口语的定义："the layer where model reasoning meets real execution: shell and filesystem access, human-in-the-loop approval flows, and context management across long-running sessions" https://learn.microsoft.com/en-us/agent-framework/agents/harness ；https://devblogs.microsoft.com/agent-framework/microsoft-agent-framework-at-build-2026-announce/ |
| Simon Willison《How coding agents work》（2026-03） | "A coding agent is a piece of software that acts as a **harness for an LLM**, extending that LLM with additional capabilities that are powered by invisible prompts and implemented as callable tools." https://simonwillison.net/guides/agentic-engineering-patterns/how-coding-agents-work/ |
| 学术综述（CMU/耶鲁等，2026-06，171 个条目，ETCLOVG 七层分类） | 三阶段论：**提示工程（2022–24）→ 上下文工程（2025）→ harness 工程（2026）**；结论："不改模型权重、只调 harness 层，也能显著改变 coding/terminal benchmark 表现" https://picrew.github.io/LLM-Harness/ ；中文解读 https://hub.baai.ac.cn/view/55145 |
| arXiv 2605.23950《Stop Comparing LLM Agents Without Disclosing the Harness》（2026-05-07） | harness 配置常比模型更能决定长任务成绩（Binding Constraint Thesis）https://arxiv.org/abs/2605.23950 |

## 1.4 共同骨架（写任何 harness 都逃不掉的八件）

| 组件 | 说明 | 权威来源 |
|---|---|---|
| 模型 + agent 循环 | LLM + system prompt + tools in a loop（ReAct 或 plan-execute 变体） | Willison；LangChain 解剖文 |
| 工具面 | 内置工具（read/write/bash/search…）+ MCP/扩展；Anthropic 自述做 SWE-bench 时"**花在工具上的时间比写 prompt 还多**" | Claude Agent SDK 博文；https://www.anthropic.com/engineering/building-effective-agents |
| 上下文管理 | compaction = "对话逼近窗口上限时摘要并重启新窗口"；context rot | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents |
| 记忆 | AGENTS.md/CLAUDE.md 文件式注入 + 检索；OpenAI 把 AGENTS.md 降为 100 行目录、docs/ 作 system of record | LangChain 解剖文；openai.com/index/harness-engineering |
| 权限/沙箱 | 审批策略 + allow-list + 网络隔离；"隔离层级决定你能给出多少自主权" | 微软文档；Claude Code/Codex 官方文档 |
| 状态持久化/恢复 | 会话存储 + resume + checkpoint；Codex Agents API 托管 "session orchestration, context compaction and recovery" | 各官方文档（详见 §4.2） |
| 可观测性 | 微软 harness 默认开 OpenTelemetry；OpenAI 每个 worktree 起临时 LogQL/PromQL | 微软文档；openai.com/index/harness-engineering |
| 子 agent/编排 | orchestrator-worker；"动态生成子 agent"（agent()/parallel()/pipeline()） | https://www.anthropic.com/engineering/multi-agent-research-system ；https://claude.com/blog/a-harness-for-every-task-dynamic-workflows-in-claude-code |

**→ 这八件全部与前端形态无关。** 它是本文回答"两种形态一样吗"的判据来源。

---

# 2. 版图对照表（2025–2026）

## 2.1 本地系（CLI / 桌面 / 自托管）代表

| 名称 | 形态（2026-10） | 循环 | 工具面 | 上下文/记忆 | 沙箱与权限 | 状态恢复 | 观测/指标 | 出处 |
|---|---|---|---|---|---|---|---|---|
| **Claude Code**（Anthropic） | CLI + macOS/Win 桌面 app（Chat/Cowork/Code 三 tab；Code 支持 Local/Cloud/SSH/WSL）+ Web + 手机 Remote Control + VS Code 扩展；Linux beta | 回合→模型请求→工具调用→hook | Bash/Read/Edit/Write/WebFetch/MCP/computer use（预览）/子 agent；iOS 模拟器面板 | CLAUDE.md；auto session summaries；auto-compact（文档未尽） | OS 沙箱：macOS Seatbelt / Linux bubblewrap（**原生 Windows 不沙箱**）；权限模式 default=Manual/acceptEdits/plan/auto/dontAsk/bypass（deny 规则全模式生效）；cloud 会话每会话隔离 VM、凭据代理代签、网络默认白名单 | `-c`/`--resume`、`/rewind` 文件 checkpoint（100 快照，不追踪 bash 文件改动）、`--teleport` 云→本地 | 内置 OTel（metrics 正式/traces beta）；hooks span | code.claude.com/docs（permissions / sandboxing / sessions / checkpointing / remote-control / platforms）；Terminal-Bench 2.1：Fable 5(xhigh)+Claude Code 83.8%、Opus 4.8+Claude Code 78.9%【二手榜】 |
| **OpenAI Codex** | CLI（Rust，Apache-2.0）+ IDE 扩展 + 桌面 app（2026-02 macOS、03-04 Win、07-09 并入 ChatGPT 桌面）+ Cloud/Web + 远程 TUI | **官方明示一内核多前端**："Codex core" 在 CLI 仓库，App Server 以 JSON-RPC(stdio) 暴露给全部前端；云版在容器内跑同一 harness | shell、文件编辑、diff、审批流、线程持久化；AGENTS.md/Skills 全形态复用 | App Server Thread/Turn/Item；线程 create/resume/fork/archive | 默认全命令沙箱：macOS Seatbelt / Linux Landlock+seccomp / Windows 实验沙箱；三档 read-only / workspace-write / danger-full-access；审批 untrusted/on-request/on-failure/never + **auto_review**（审批先给 reviewer agent，fail-closed） | `codex resume`（按 CWD 过滤、`--last`/ID/`--all`）；`codex exec resume`；转录/plan/审批记录全保留 | 未核实 | https://openai.com/zh-Hans-CN/index/unlocking-the-codex-harness/ ；https://github.com/openai/codex |
| **Pi**（pi.dev / earendil-works） | 终端 TUI 为主 + 4 种运行模式 + **大量社区桌面客户端**（pi-agent-desktop 等）；官方无 GUI | 极简 harness：默认 4 工具（read/write/edit/bash）、系统提示 <1000 tokens；其余全靠扩展 | 4 内置 + MCP（1.0 新增）+ Codemode（harness 侧 JS 沙箱）；刻意为极简 | 默认不带 sub-agent/plan mode/权限弹窗（靠扩展） | 靠宿主 shell；1.0 加延迟工具加载/缓存预热 | 未详 | 【确证】pi.dev；https://github.com/earendil-works/pi ；Pi 1.0 官方文 https://earendil.com/posts/pi-1-0/ ；HN 榜首（2026-10-01/02） |
| **Hermes Agent**（Nous Research） | **TUI + 消息网关（单进程 20+ 平台）+ 官方桌面端 Hermes Desktop（macOS/Win/Linux，2026-06-03 公测）+ Web Dashboard + IDE ACP server** | "自进化"循环：会话搜索 + 记忆 + 自动生成/改进 skills + cron + 子 agent 委派 | 工具以 toolset 组织（terminal/file/browser/code_execution/memory/delegation/kanban/cron…40+ 项）；MCP client/server 双向 | FTS5 会话搜索 + MEMORY.md/USER.md + Honcho 用户建模 + skills（agentskills.io 标准） | 七种 terminal 后端：local/Docker/SSH/Singularity/Modal/Daytona/Vercel Sandbox；审批 manual/smart/off；外部安全工具 tirith | sessions JSONL + `--resume/--continue`；`--checkpoints` + `/rollback` 文件快照；`/compress` 手动压缩 | `hermes insights`； | **本机一手**：`hermes-wechat-experiment/`（SOUL.md、SKILL.md v2.1.0）；https://github.com/NousResearch/hermes-agent ；https://hermes-agent.nousresearch.com/docs/ |
| **OpenClaw**（社区；原 Clawdbot→Moltbot，2026-01-30 定名） | 本地个人 assistant（消息平台为主入口）；创始人 Peter Steinberger 2026-02 加入 OpenAI，项目转入 OpenClaw Foundation；Windows 生态接入微软 MXC（内核级 OS 沙箱） | 未详 | 以消息平台为入口的全能助手 | 未详 | MXC 四档隔离（进程/会话/VM/WSL/Windows 365） | 未详 | 2026 年最大开源现象：72h 6 万星、6 月约 29.5 万星【报道口径】；中国"**养龙虾**"生态（腾讯 QClaw、智谱 GLM-5-Turbo 专项、字节"豆包工作"） | https://steipete.me/posts/2026/openclaw/ ；新智元 Build 2026 报道（hub.baai.ac.cn/view/55209） |
| **Goose**（Block） | CLI + 桌面（Electron）+ goosed server + Web + MCP/ACP server；Apache-2.0；2025-12 捐入 AAIF（Linux Foundation） | turn-based（上限 1000 turns）+ subagents + recipes | MCP-native（最早支持 MCP 的开源 agent）；Developer 扩展（shell/write/edit/tree…） | SQLite 会话持久；上下文 80% 自动压缩 | **无内置沙箱**（以用户全权限运行）；多层 inspection 管线；secrets 走 OS keyring | SQLite + `goose session` 子命令 | 未核实 | https://github.com/block/goose ；第三方代码级评审（agent-reviews） |
| **OpenCode**（sst） | TUI + server + Web UI + IDE 插件 + 桌面（beta） | 教科书"一内核多前端"：裸跑即 server+TUI；**同一 OpenAPI 3.1 API 服务所有前端**（HTTP+SSE /event） | 路由含 session/permission/mcp/pty/file；Tool.execute() | SQLite WAL | 服务端统一 permission allow/ask/deny；`--yolo` | 服务端会话存储 | 未核实 | https://docs.opencode.ai/docs/server/ |
| **Crush**（Charm） | TUI（+`crush run` 非交互；client/server 模式） | 循环外包给 charm.land/fantasy SDK（回调式） | bash/view/write/edit/LSP 系列/MCP/keyless…；实现 SKILL.md 标准（兼容 .claude/skills） | SQLite 持久 | permission.Service 拦截每工具调用；`--yolo` | SQLite | 未核实 | DeepWiki 拆解；社区基准笔记 |
| 补充（2026 新） | **Warp**（客户端开源 AGPLv3，"同一 agent 三形态"：app/Agent CLI/云）；**Amp**（Sourcegraph 拆分，CLI+web+macOS/iOS，Orbs 云沙箱）；**Factory Droid**（CLI+桌面，风险分级自主度）；**Zed**（反向模式：编辑器作宿主，挂载 Claude Code/Codex/Gemini CLI/OpenCode 等多个外部内核，ACP） | | | | | | | docs.warp.dev/agents ；innfactory.ai；docs.factory.ai；zed.dev/docs/ai/agents |

**指标参考（口径混乱，仅作量级；均为【二手】第三方榜单/转述）**：Terminal-Bench 2.1 头部为 GPT-6 Astra+Codex 87.4% / Fable 5+Claude Code 83.8%；Terminal-Bench 2.0 第三方盘点 Codex CLI 82.7%（2026-06）；SWE-bench Verified 是"模型+scaffold"数字，同一模型不同 harness 可差 8–12pp，勿当模型能力对比。**mini-SWE-agent（~100 行、纯 bash、无 tool-calling）>74% SWE-bench Verified** 是"harness 复杂度与效果非线性"的最佳注脚。https://www.tbench.ai/ ；https://mini-swe-agent.com/latest/

## 2.2 云 / Web 原生系代表（反"桌面才强"叙事的证据组）

| 名称 | 行动面 | 沙箱实现 | 持久化/恢复 | 备注 |
|---|---|---|---|---|
| **DeerFlow 2.0**（字节） | 自托管（本机/服务器/K8s）跑 Web 应用；LangGraph Server(:2024)+FastAPI(:8001)+Next.js(:3000) | 可选 Local / Docker(AIO Sandbox) / K8s，给完整文件系统+bash | LangGraph 线程 checkpoint（thread 状态） | README 自述 **"open-source super agent harness"**；2026-02-28 重写并登 GitHub Trending #1；~5.7 万星（4 月）https://github.com/bytedance/deer-flow |
| **OpenHands**（All Hands AI） | 多前端：CLI / 本地 GUI / Cloud / K8s；**agent 与 runtime 容器分离**，Local/Docker/Remote/K8s/Modal runtime 可插拔 | Docker 为主（cap-drop ALL + no-new-privileges，每会话一容器） | **事件溯源：先同步落盘再分发，崩溃不丢事件**；ReplayManager 重放；Condenser 压缩 | SWE-bench Verified 60.6% 单发 / 66.4% 扩展（2025-04）；2026-03 arXiv 多 agent 配置 69.4%；90.1K 星；2026-05 发 Agent Control Plane；SDK 的 LocalConversation/RemoteConversation **同一 API 面** |
| **Manus**（Butterfly Effect） | Web + **Manus Desktop "My Computer" 连接用户本地实体机**（云↔本地可切换） | **每任务一台隔离云 VM**：E2B + Firecracker microVM（自托管 E2B），~150ms 起；曾试 Docker（10–20s）后弃用 | sandbox 空闲休眠保文件、按需唤醒；闲置回收（免费 7 天/Pro 21 天）；另有常驻 Cloud Computer | "云 agent 反向补本地行动面"的代表；定价 credit 制 https://manus.im/blog/manus-sandbox |
| **Devin**（Cognition） | 云 VM（自带 shell/编辑器/浏览器）+ Devin Desktop（原 Windsurf）+ Local + CLI | 每会话独立沙箱 VM；企业版 VPC 隔离 | 云会话持久；2026-02 起多并行会话 | Devin 2.0 起 $20/月（从 $500 大幅下调）；"云+桌面+本地+CLI"全家桶 https://devin.ai/pricing |
| **ChatGPT Agent mode / Dots**（OpenAI） | 云托管"虚拟电脑"（视觉浏览器+文本浏览器+终端+API）；2026-09-30 DevDay 发 **Dots**（永远在线 agent：自有云端电脑/身份/权限/浏览器，24h 执行） | 远程虚拟电脑 + 沙箱浏览器；**不能直接接触密码/银行凭据**；登录环节暂停交还用户接管 | 按任务弃置，无持久 VM 承诺 | https://openai.com/index/introducing-chatgpt-agent/ ；netmag.tw 10/4 报道（Dots） |
| **Jules**（Google） | Web + Jules Tools CLI + API + GitHub 触发 | 一次性 Google Cloud VM（克隆→计划→执行→PR，完成后销毁） | 任务制 | **与 Gemini CLI 非同内核**（反例）；Gemini CLI 本体 2026-05 起被并入 Antigravity CLI（桌面+CLI 统一 harness，非开源）【确证·多源】 |
| **Supermew**（用户截图产品，icey1287/SuperMew） | **前后端架构**：backend + frontend + evals + ADR（LangChain/LangGraph + Milvus） | `guardrails-and-isolated-sandbox` ADR（后端内建） | **checkpointing.py / checkpoint_runner.py**；canonical-thread-lifecycle ADR | 884 星、2025-12 建、2026-09 仍活跃；**fe/BE + harness 全套（中间件链、checkpoint、skill、评估门禁）**——见 A8 拆解 |
| （对照）**你的 DocMind** | FastAPI + Vue；JWT+RBAC+多租户；SSE 12 事件；PER+25 工具+四层记忆+replay+评估 | 代码执行默认禁用+受限执行；另有 docker_sandbox（工作流代码节点走 Docker 沙箱） | 会话/记忆 DB + Redis；**任务级续跑与结果落库是 A3/A6 指出的缺口** | 本仓库（v1.22.0-rc，2026-10-05） |

## 2.3 "一个内核、多前端"专表（讨论"形态"问题的核心证据）

| 产品 | 内核边界在哪 | 执行位置 | 证据等级 |
|---|---|---|---|
| **OpenAI Codex** | "Codex core" + App Server（JSON-RPC/stdio）；**官方白纸黑字** | 本地沙箱 / 云容器可换 | 【确证·官方】 |
| **Claude Code** | "**runs the same underlying engine everywhere**"；会话可 `/desktop` 搬运、`--teleport` 云→本地；CLI-only 是 scripting/SDK | 本地（Remote Control 时执行仍在本地）/ 云 VM | 【确证·官方】 |
| **OpenCode** | 同一 OpenAPI 3.1 server 服务 TUI/Web/IDE/桌面 | 本地或远端 server | 【确证】 |
| **Goose** | 单 Rust 核心 + goosed（HTTP/WS）；桌面与 CLI 都是客户端 | 本机 | 【确证※代码级】 |
| **Hermes Agent** | 同一 agent 内核：TUI / Desktop / gateway 平台 / IDE 共享配置/会话/技能/记忆；**七种 terminal 后端可插拔** | 本地/容器/SSH/Singularity/Modal/Daytona/Vercel | 【一手+确证】 |
| **OpenClaw** | 内核 = **Pi**（极简 agent）；外壳为各消息平台/桌面客户端生态 | 本机 | 【确证·多源】 |
| **Warp / Antigravity（Google）** | 官方口径"同一 agent 三形态"/"与桌面共享统一后端 harness" | 本机/云 | 【确证·官方博客】 |
| **反例：Gemini CLI vs Jules** | 只共享模型，不共享 harness | 本地 / 云 | 【确证·多源】 |

**最干净的结论（来自本轮差异调研）**：真正实现了"一个内核多前端"的产品，都是**先有独立于 UI 的内核边界**（App Server / 事件日志 / server 化），再把"**执行位置**"做成可插拔（local / worker / cloud VM）。**这与前端是不是 CLI 无关。**

## 2.4 2026 年 1–10 月大事（浓缩时间线）

- **1–2 月**：Claude Code 桌面预览（1/7）；Codex 桌面 app（2 月初 macOS）；OpenClaw 现象级爆发（72h 6 万星）；Pi 被 Ronacher 长文背书（1/31）。
- **2 月**：OpenAI《Harness Engineering》提出 Agent=Model+Harness；微软 Agent Framework 1.0 GA（4/2）；DeepSeek Harness 开源（"Everything is a Plugin"，子 agent 经 GitHub API 实测 **24.4 万星**，截至 2026-10-06——数字未复核）；DeerFlow 2.0（2/28，"super agent harness"）。
- **3–4 月**：LangChain《Anatomy of an Agent Harness》（3/10）；Anthropic《Harness design for long-running apps》（3/24，演示 solo 20 分钟 $9 vs 全 harness 6 小时 $200）；Earendil 收购 Pi（4/8）；Codex Desktop 加 computer use（4/16）；**Gemini CLI→Antigravity CLI**（5/19 I/O，6/18 起个人用户停服，社区批评"不再开源"）。
- **5–6 月**：Google I/O（Antigravity 2.0）；Codex "Record & Replay"（6/18）；**Hermes Desktop 公测（6/3）**；OpenClaw 登 Build 2026 + 微软 MXC + 基金会；Roo Code **关闭 IDE 扩展**转云端 Roomote（5/15，"IDE 时代结束"之争）。
- **6 月**：**SpaceX 以 600 亿美元收购 Cursor**【报道口径·多源】。
- **7–9 月**：Kimi K2.6/K3（最多 300 子 agent）；Qwen Code 桌面端（8/6）；字节 TRAE+扣子并入豆包；智谱 ZCode（自称"国产 Coding Harness"，用户破百万）；腾讯 QClaw"微信直连"；**Meta Muse** 个人 agent（9/8，22 天 500 万下载）；Manus 2.0（9/28，中资审查叫停 Meta 收购后恢复独立）；Devin Desktop。
- **10 月**：**Pi 1.0**（10/1，加 MCP，登 HN 榜首，The Register 称"180° 转弯"）；OpenAI DevDay：**Dots**（永远在线 agent）+ Codex Cloud（9/30）。
- **中国叙事的总结词**：OpenClaw 生态被称"**养龙虾**"（腾讯 QClaw/WorkBuddy、智谱 GLM-5-Turbo 专项与 ZClawBench、字节"豆包工作"）——**微信通道之争已是大厂主场**（这直接支持你决策文档"微信只做 P3 可选通道"的结论）。

---

# 3. "Pi" 与 "Hermes" 核实

> 两个名字都已确证指向 2025–2026 年的真实产品；且两者恰是"极简内核 + 多形态外壳"的两种范式。

## 3.1 "Pi" = pi.dev 的 Pi Coding Agent【最可能】

| 项 | 内容 | 证据 |
|---|---|---|
| 是什么 | 开源（MIT）极简 coding agent **harness**：默认仅 4 个工具（read/write/edit/bash）、系统提示 <1000 tokens，刻意不内置 sub-agent/plan mode/权限弹窗（靠扩展补） | https://pi.dev/ ；https://github.com/earendil-works/pi ；官方 1.0 文自述 "a hardened, minimal, extensible **agent harness** that you can make your own" https://earendil.com/posts/pi-1-0/ |
| 作者/归属 | Mario Zechner（libGDX 作者）创建；2026-04-08 被 **Earendil**（Armin Ronacher + Colin Hanna，维也纳）收购接手，Zechner 留任；核心保持 MIT | devbytes.co.in/news/earendil-acquires-pi…；多源 |
| 时间线 | 2026-01-31 Ronacher 背书 → 02 Ollama 集成（`ollama launch pi`）→ 05-25 Product Hunt 正式 launch → **2026-10-01 Pi 1.0**（新增 MCP、Codemode、虚拟模型、延迟工具加载；实验包 Pi Durable），HN 榜首；热度：5 月 ~4.5 万星 → 9 月报道 **10 万+ star、npm 周下载 120 万+**【多源，口径不一】 | theregister.com 10/2；gigazine 10/2；aiwiki.ai/wiki/pi_agent |
| 为什么它是"桌面语境"的高概率指代 | ① 它是 **OpenClaw 的内核**（"OpenClaw 里的那个极简 agent"，Cua 将 Hermes/OpenClaw/Pi 并列为 Computer-Use 2.0 通用 agent）；② 社区**桌面客户端生态庞大**：Chasen-Liao/pi-agent-desktop（Electron）、DLYYZT/pi-desktop、justhil/pi-app、hhyy668/pi-desktop-ui（`pi --desktop`）等，中文圈以"基于 PI 内核的桌面编码 Agent"推广；③ 与 Hermes 同处"极简 harness 阵营"，常被并列讨论 | github.com/Chasen-Liao/pi-agent-desktop 等；cua.ai/blog/computer-use-2-ai-engineer-worlds-fair |
| 何时是它 | 若用户看到的是"桌面 agent 产品/极简 harness/Pi 1.0"相关讨论 → 即此物 | 判据如上 |

**次可能：PaiAgent（itwanger/PaiAgent）** —— 但它是**Web 工作流编排平台**（Spring AI + LangGraph4J + ReactFlow，~675 星，作者"沉默王二"），不是桌面产品；它在用户语境里成立的方式是"**DocMind 里 skills/ 的来源**"（本地 `AGENTS.md`/`CLAUDE.md` 记载"absorbed from PaiAgent"；其中文社区另有 PaiCLI（终端 agent CLI）系列）。https://github.com/itwanger/PaiAgent

**已排除项（同名陷阱）**：Inflection AI 的 Pi（情绪陪伴聊天机器人，至今未转桌面 agent【未找到】）；Physical Intelligence 的 π0/π0.5/π*0.6/π0.7（机器人 VLA 模型 + openpi 机器人软件，无桌面 agent 软件【未找到】）；Pi Labs / withpi.ai（LLM evals 初创）；阿里云 PAI（云平台）。【均确证排除】

## 3.2 "Hermes" = Nous Research 的 Hermes Agent【确证（含本机一手证据）】

**这是本轮最确定的一项——你本机的 `AI-projects/hermes-wechat-experiment/`（2026-05-11 运行）就是它：**

| 项 | 内容 | 证据 |
|---|---|---|
| 它是什么 | **开源（MIT）"自进化" agent harness**：官方 README 原句 "*The self-improving AI agent built by Nous Research.*"，自述"与 Claude Code / Codex / OpenClaw 同类" | 本机 `SOUL.md`（产品默认人设："You are Hermes Agent, an intelligent AI assistant created by **Nous Research**"）；官方 README https://github.com/NousResearch/hermes-agent ；SKILL.md（本机副本 v2.1.0） |
| 形态（关键） | **TUI + 消息网关（单进程接 20+ 平台）+ 官方桌面端 Hermes Desktop（macOS/Windows/Linux，2026-06-03 公测）+ Web Dashboard + IDE 的 ACP server**；"Same agent, same skills, same memory as the CLI and gateway" | 本机 `gateway_state.json`（`hermes gateway run` 进程）；`apps/desktop/README.md`；Product Hunt 2026-02-26（agent）/06-03（desktop）；gigazine 2026-06-04 |
| 与微信的关系（解释你决策文档里那句话） | 原生 **Weixin 平台适配器，走腾讯官方 iLink Bot API**（扫码登录、长轮询、无需公网 Webhook）——"官方插件 + Hermes 都已解决接入层"成立 | 本机 `channel_directory.json`（23 平台含 weixin/yuanbao/qqbot/wecom/feishu）；官方 weixin.md（hermes agent 直取 raw.githubusercontent.com HTTP 200） |
| 内核能力（本机文件可见） | 七种 terminal 后端（local/Docker/SSH/Singularity/Modal/Daytona/Vercel Sandbox）；skills 体系（SKILL.md、curator 生命周期）；kanban.db 多代理工作队列；cron；会话 JSONL + `--resume/--continue` + `--checkpoints`/`/rollback` + `/compress`；审批 manual/smart/off；第三方安全工具 **tirith**（sheeki03/tirith，AGPL，你当时因境内网络下载失败——本机 `.tirith-install-failed`） | 本机 logs、skills/、sessions/、sandbox 目录；SKILL.md 全文；https://github.com/sheeki03/tirith |
| 时间线 | PH 上线 2026-02-26；桌面端 6/3；v0.16.0=2026-06-05 加 zh-Hans；v0.21.5 构建 2026-09-24；中国社区昵称"**养马**"；被 Cua 列为 Computer-Use 2.0 代表 agent | 多源（PH、gigazine、c114、GitHub releases） |
| 注意 | ① star 数第三方口径矛盾（27k/47k/142k），**勿引用**；② 有 EvoMap 抄袭争议报道（争议存在【确证】，指控内容【推测】）；③ "部署重/中文细节不足"与你实测一致但"部分成立、正在补齐"（官方已有 README.zh-CN.md） | HN id=48187581 等 |
| 同名排除 | **Nous 的 Hermes 2/3/4 是模型**（Hermes 4：2025-08-26，14B/70B/405B，arXiv 2508.18255），不是这个 agent；Meta 的 Hermes 是 React Native 的 JS 引擎；爱马仕无关 | HuggingFace / arXiv / facebook/hermes |

## 3.3 核实结论表

| 名 | 判定 | 一句话 |
|---|---|---|
| **Pi** | **【确证存在】最可能 = pi.dev 的 Pi Coding Agent**（极简终端 harness，OpenClaw 内核，社区桌面客户端生态大，2026-10-01 发 1.0） | 【推测】为用户所指（无用户原话，无法 100% 确证）；次可能 = PaiAgent（仅当语境是"skills 来源"） |
| **Hermes** | **【确证】= Nous Research Hermes Agent（含官方 Hermes Desktop）**，且你本机跑过、决策文档评过、DocMind 架构文档参考过（"hermes-agent (NousResearch) → 装饰器工具注册 + subagent.py"） | 无歧义 |

---

# 4. 桌面/CLI vs 前后端 Web：分层差异

## 4.1 能力面：行动面在哪里

**本地 harness 独有能力**（均有官方文档【确证】，其中两条直取原文）：改本地文件（"runs on your machine with direct access to your files"）；跑 shell（OS 沙箱边界内）；进程/后台任务（Claude Code 后台会话；**Remote Control 明确要求"本地进程一直运行"，关终端即离线**）；**访问本地凭据/登录态**（MCP stdio server "Auth is inherited from the OS user"；Claude Code Chrome 集成官方描述 "*Controls your browser with your logged-in sessions*"）；操作本地应用（computer use）；OS 级隔离执行。

**Web/云 Agent 的弥补路径**（先例 + 限制）：

| 路径 | 先例 | 限制 |
|---|---|---|
| 云沙箱（"手"在云 VM） | Claude Code cloud（每会话隔离 VM、凭据留在沙箱外由代理代签、push 限当前分支）；Codex cloud（setup 阶段才有 secrets，agent 阶段默认离线）；Devin Devbox；Cursor Cloud Agents（关本机继续跑） | 只能看见推上去/克隆下来的东西；内网要 IP 白名单；闲置回收会丢后台工作 |
| **自托管执行体**（"手"在你自己的基础设施） | Claude self-hosted environments（企业 runner）；**OpenAI Agents API 的 `self_hosted` environment：`codex exec-server` 在你的容器里出站 WebSocket 接入托管 harness**；Cursor self-hosted machines（**loop 在云、执行在你机器**）；OpenHands 后端可切 local/Docker/VM/cloud | 运维责任转移给你；断连自愈要自己做 |
| 浏览器沙箱 | Anthropic computer-use 参考实现（官方只给 Docker 镜像，不代执行）；Operator/ChatGPT Agent 云浏览器 | 没有你的登录态；或要你交出登录态；注入攻击缓解后成功率仍约 11–24%【二手引述】 |
| **本地桥接**（本地装执行体、web 遥控） | **Claude Code Remote Control**：会话**在本地执行**，浏览器/手机只是"一扇窗"；仅出站 HTTPS、永不开入站端口；Trusted Devices（设备登记+生物识别）；**Dispatch**（手机发任务到你的电脑）；Channels；Codex 远程 TUI（`codex app-server --listen ws://` + capability-token 认证）；MCP stdio | 电脑必须开着；多数组织策略默认关闭；ZDR/HIPAA 配置**禁止**启用 |

**关键不对称（官方文档确证）**：Claude Code 只能 `--teleport` 把**云会话拉回本地**，**不能把本地会话推到云**（唯一例外：桌面 app 的 "Open in Cloud" 以摘要接力）；Codex IDE 则可双向。→ "本地是更完整的那一侧"是 2026 年的产品事实。

**"agent 的价值取决于能否接触你的环境"有没有权威说法**：没有单篇纲领文献【未找到】；但组合证据成立——The Verge（"that level of context — read and write access to your email, your bank account…is required if AI is going to…actually go off and do things for you"）；Anthropic ACI 实证（做 SWE-bench 时"花在工具上的时间比 prompt 还多"）；Simon Willison"致命三要素"（私有数据+不可信内容+对外通信，三者同存才危险，也恰好是有用的原因）。

## 4.2 工程面速览（详见本轮子报告，此处只留结论）

| 项 | CLI/桌面做法 | Web/云做法 | 判读 |
|---|---|---|---|
| 会话持久化 | 本地 JSONL/rollout 文件，`--continue/--resume`（Claude Code/Codex/Hermes）；恢复范围含转录/plan/审批 | DB 持久化 + 云端会话列表 + 排队消息 + 分享（claude.ai/code）；会话可在关闭浏览器后继续 | **外壳级差异，双方已对等**；真正的差异在"恢复什么"：云 VM 回收后重开是**新 VM**，对话能恢复、**后台工作不恢复** |
| crash 恢复/checkpoint | Claude Code `/rewind` 文件快照（100 个，不追 bash 改动）；OpenHands **事件溯源"先落盘再分发"**；LangGraph 每 super-step checkpoint + pending writes（durability 三档、重放语义、HITL 可挂数小时）；OpenAI 沙箱基建 microVM+块级快照 | 同左（LangGraph/OpenHands 本就是 web 系）；Codex 云容器缓存 12h | **能力同源；差别在工程成熟度** |
| 权限模型 | 交互式审批为主 + 长出策略层（Codex auto_review 用 reviewer agent 先审、fail-closed；Claude managed settings 组织级强制） | RBAC/审批流为主 + 长出"设备信任"（Trusted Devices；self-hosted runner） | 各有强项：交互审批质量本地最好；组织治理/审计云端最强 |
| 沙箱谱系 | OS 级（Seatbelt/bubblewrap/Landlock+seccomp）→ 容器 → microVM → 云 VM | 容器（OpenHands/DeerFlow）→ microVM（Manus E2B+Firecracker）→ 云 VM（Devin/Jules/ChatGPT Agent） | 官方立场：**隔离层级决定你敢给出多少自主权** |

## 4.3 产品面：为什么 2026 的强者偏 CLI/桌面？——一半事实，一半叙事

**事实分布（确证）**：面向开发者/专业工作的头部产品以 CLI 为核心、桌面为扩展载体（Claude Code CLI 起家、桌面获同等待遇；Codex 首页定位 "runs locally on your computer"；Google 的终端产品并入 Antigravity 桌面统一后端；Cursor/GitHub 都补了 CLI；开源侧 Goose/OpenCode/Hermes/Pi/OpenClaw 均本机为主）。中文最系统的分析是少数派/36氪长文（2026-06-17）：GUI 对 AI 四大不友好（理解成本高、状态隐式、不可组合、难测试），CLI 三大优势（**可组合、可预测、可审计**），核心论点"**function call 从语义上就是 CLI**"；并提醒作者是本地工具厂商、有利益相关。https://www.36kr.com/p/3856750377030918（原文 https://sspai.com/post/107173）

**反证（同样确证）**：消费级战场仍是 **Web/App**——ChatGPT/Dots、Meta Muse（22 天 500 万下载）、Manus 主入口都是 web/App；Cursor 仍 ~40 亿美元 ARR；Roo Code 关闭 IDE 扩展≠转向 CLI，而是转向**云端容器 agent（Roomote）**——"形态在洗牌，不是 CLI 单向赢"。**没有找到**"最强 agent 大多是 CLI/桌面"的原始权威文句【未找到】；准确表述是：**"面向开发者的头部 agent = CLI 为核心 + 桌面为扩散载体；桌面成为 2026 竞争主战场；消费级仍 web/App。"**

## 4.4 诚实结论

把 §0.2 的表再压缩成一条判据线：
- **任务对象是"你的机器/你的数据/你的账号"**（改本地代码、跑本地命令、用登录态、连内网）→ 桌面/CLI（或"本地执行体+web 遥控"）是**本质必需**；
- **任务对象是"多人共享的服务/知识/流程"**（多租户、审计、协作、跨设备）→ Web 前后端是**本质优势**（你的 JWT+RBAC+组织隔离在 CLI 形态里无法成立）；
- **两者都不是**（纯文本/分析/API 调用）→ 形态随便，看分发与交互偏好；
- **其余一切**（会话恢复怎么存、流式怎么推、UI 长什么样）→ **外壳**。

---

# 5. Web 架构补齐 harness 的可行路径 + 代价

## 5.1 远程/云沙箱对照表（CPU 单价量级；价格为检索快照，落地前复核官方页）

| 服务 | 隔离技术 | 冷启动 | 快照/恢复 | 定价量级 | URL |
|---|---|---|---|---|---|
| **E2B** | Firecracker microVM | ~150ms【推测·第三方】 | `pause()` 默认保内存+文件系统，**暂停无限期保留且不计费**；可跨沙箱复用快照 | ~$0.0504/vCPU·h + $0.0162/GiB·h；Hobby 免费+$100 额度 | e2b.dev/pricing |
| **Daytona** | Docker 默认，可切 Kata/Sysbox | sub-90ms（from-snapshot，宣称） | pause 不计内存费只收磁盘 | 同 E2B 单价 + 存储费；$200 注册额度；无平台费 | daytona.io/pricing |
| **Modal Sandboxes** | gVisor 默认（VM Sandboxes beta） | ~400ms 热 / ~3s 冷【推测】 | fs snapshot 30 天；memory snapshot（alpha）；恢复≈常规启动 1.05s p50 | Sandbox ≈ $0.071/vCPU·h + $0.024/GiB·h【第三方换算】；GPU T4~$0.59/H100~$3.95/h | modal.com/pricing |
| **Cloudflare Sandbox** | 每 sandbox 独占 VM 内容器（Durable Object 编排） | 全新 ~30s；**快照恢复 ~2s**【第三方实测】 | backup/restore API | Workers Paid $5/月含额度；活跃 CPU $0.00002/vCPU·s；常开成本高 | developers.cloudflare.com/sandbox/ |
| **Fly.io Machines** | Firecracker microVM | scale-to-zero ~1.5s；keep-warm ~61ms【推测】 | 无独立快照产品；suspend 不保证 | shared-cpu 256MB 起 ~$1.94/月 | fly.io/docs/about/pricing |
| **Vercel Sandbox** | Firecracker microVM（已 GA） | 亚秒级（官方） | 仅文件系统+已装包，默认 30 天 | Active CPU $0.128/vCPU·h + 内存费+创建费 | vercel.com/docs/vercel-sandbox/pricing |
| **CodeSandbox SDK** | Firecracker microVM；VM `fork()` <1s | 模板 P95 ~2.7s；hibernate 恢复 ~500ms | hibernate 保**完整 VM 快照（内存+磁盘）** | Pico ~$0.0743/h（2 核 1GB）；按分钟向上取整 | codesandbox.stream/docs/sdk/pricing |
| **Northflank** | Kata Containers 默认，回退 gVisor | boot <1s；P99 分配 566ms（官方） | scale-to-zero 保卷不保内存 | **$0.01667/vCPU·h + $0.00833/GB·h**（最便宜档） | northflank.com/blog/ai-sandbox-pricing |
| **AWS Fargate / Firecracker 自建** | Fargate 微 VM；Firecracker 须裸金属自建 | **30–60s（含镜像拉取，全计费）** | 无快照 | $0.04048/vCPU·h + $0.004445/GB·h | firecracker-microvm.github.io |
| **Google Cloud Run** | gen1 gVisor / gen2 microVM，两代同价 | gen1 快；gen2 持续负载更优 | 无状态服务模型，无快照 | ~$0.07/vCPU·h 量级【推测·未核实到官方数字】 | cloud.google.com/run/pricing |
| **K8s 自建** | kubernetes-sigs/**agent-sandbox**（Sandbox CRD/WarmPool，RuntimeClass 切 gVisor/Kata） | gVisor ~100ms / Kata 125–200ms | Agent Sandbox 提供 pause/resume/快照 | 集群成本自负 | github.com/kubernetes-sigs/agent-sandbox |

**2026 年趋势**：快照恢复已是沙箱标配；选型安全性的教训是"**隔离引擎选型不如补丁纪律重要**"（有研究指出 E2B 曾带逃逸级 CVE 的 Firecracker 版本运行 44+ 天）【二手·学术】。

## 5.2 浏览器内沙箱（客户端执行）边界

| 技术 | 能 | 不能（Wasm 边界） |
|---|---|---|
| **WebContainers**（StackBlitz） | 完整 **Node.js** 工具链（npm/终端/框架/dev server），Bolt.new 底座 | 非 JS 后端不可用；原生二进制要编译成 WASM；Firefox 仅 beta（需 Chromium-only 特性） |
| **Pyodide** | 200+ 预编译科学包（numpy/pandas…）、纯 Python wheel | **无 threading、无 subprocess、无 socket**；C 扩展需预编译；**2GB 内存上限** |
| **CheerpX** | 跑**未修改的 32 位 x86 Linux 二进制**、完整 Debian（WebVM 演示含 gcc/vim），可作不可信代码的客户端沙箱 | 仅 32 位 x86；无 3D；user-mode 模拟性能低；专有许可 |

三者共同点：**没有真实 shell 访问宿主机、没有任意二进制、没有后台常驻进程**；代码在用户浏览器里跑（对服务端零风险，但用户端可被滥用）。

## 5.3 本地桥接模式与安全边界（Web UI 驱动本地执行器的先例）

- **MCP stdio**：桌面客户端 spawn 本地子进程 JSON-RPC；"以用户全权限运行、默认无沙箱"；**官方明确其不是安全通道**（有 STDIO 命令注入 CVE 类）；缓解共识：容器隔离第三方 server、最小凭据、窄文件根。
- **VS Code Remote Tunnels / code-server**：浏览器里跑完整 IDE（终端/扩展全在宿主机）；code-server 有 CVE-2025-47269（<4.99.4，可窃取会话 cookie→RCE）；**绝不可无认证暴露**。
- **Jupyter Kernel Gateway**：REST+WS 驱动内核；WS 认证受限（浏览器 WS 不能带 header）；Enterprise Gateway 才有用户授权模型。
- **浏览器扩展 native messaging**：Chromium 安全 FAQ 明言"**不是安全通道**"（非商店扩展可伪造 ID、本机其他进程可直连 host）；实案：Edgecution 勒索软件经 Edge 扩展逃逸执行 shell。加固模式：per-run secret 认证的 localhost bridge + 逐 origin 批准。

**结论**：web 要驱动本地执行器，**必须有一个用户侧安装的本地代理**（桌面 app / VS Code Server / kernel / 扩展 host）；桥本身不提供安全边界——唯一稳健做法是"桥+应用层认证+最小权限+可审计"，且**最终执行决定权留在本地用户手里**。

## 5.4 判决：什么时候需要什么

| 场景 | 需要远程沙箱？ | 推荐 |
|---|---|---|
| 执行 LLM 生成的代码 / code interpreter | **必须** | microVM（Firecracker/Kata）或 gVisor；+ 网络白名单 + 出口凭据注入 |
| 大规模浏览器自动化 | **必须** | 云沙箱/浏览器集群（本地跑不动且凭据污染） |
| 超长任务（小时级） | **必须（或长驻）** | E2B pause（无限期）/ Vercel·Modal 24h / CodeSandbox hibernate / Fly 长驻 |
| 多租户"每客户一个执行环境" | **必须** | per-tenant 实例 + 服务端身份派生 ID + 配额 + 默认拒绝出站 |
| **RAG 问答 / 知识检索** | **不需要** | 检索+LLM+DB；最多受限表达式求值 |
| **纯文本/分析任务**（摘要、翻译、抽取、评审） | **不需要** | 模型直接完成 |
| 只调外部 API | 不需要 | 后端 HTTP + SSRF 防护 + 密钥管理（问题在工具网关，不在沙箱） |
| 已有自建集群 | 不需要租用 | K8s RuntimeClass：gVisor/Kata + agent-sandbox WarmPool |
| 用户数据不能出端 | 不需要远程 | 浏览器内沙箱（§5.2）或本地桥（§5.3） |

**成本速记**：CPU 单价从 ~$0.017/vCPU·h（Northflank）→ ~$0.05（E2B/Daytona）→ ~$0.07（Modal/CF 活跃）→ $0.128（Vercel）不等；**对单人开发者，Docker（已有）+ 容器桥接就够用；"多租户不可信代码"才是上 microVM 的信号。**

---

# 6. 给 DocMind + 个人工作台的架构建议（含反方）

## 6.1 直接回答"该保持前后端、转 CLI，还是双形态？"

**双形态——但"双形态"的正确含义不是"再做一个界面"，而是"内核与形态解耦 + 执行位置可插拔"。** 业界先例一抓一把：Codex（App Server 一核多端）、Claude Code（"same underlying engine everywhere"）、Goose、OpenHands、OpenCode、Hermes（TUI+桌面+23 平台+IDE 共享内核）、OpenClaw（Pi 内核+多壳）、Warp、Antigravity。**"业界有没有人这么干过？"——2026 年几乎每个头部产品都这么干。**

对你的两家具体落法：

**(1) DocMind：保持前后端，补"手"和"内核"，不做形态改造。**
理由（均有证据）：
- 前后端不是弱点：Supermew（你截图的产品，FE/BE+LangGraph+checkpoint+eval 门禁）、DeerFlow 2.0（自托管 Web，自称 super agent harness）、OpenHands（web-first）都是/web 系；**"效果"来自内核**（§0.1 三条）。
- 多租户/RBAC/审计是**Web 的独有资产**，转 CLI 等于把差异化扔掉。
- 你的内核其实已 70%：PER 循环、25 工具、context.py（自述吸收 hermes-agent 模式）、四层记忆、SSE 12 事件、replay、RunReport、评估框架（A3 已给出最小指标集）。缺口是**具体、可清单化**的：
  - **P0（1–2 周，直接提"效果"）**：① 任务级结果与 agent token/成本落库（A3 MVP-12 #1/#5/#6 已有公式与代码坐标）；② Run 级 resume/checkpoint——照抄三家先例的最小版：OpenHands 的"事件先落盘再分发" + LangGraph 的"每步 checkpoint+pending writes"即可（你已有 events/replay 基础设施）；③ 把已实现但无消费者的模块接线（subagent/skills/escalation——A6 列了死代码清单）。
  - **P1（2–4 周，"手和钥匙"）**：给 DocMind 加**两种执行位置可选**：a) 现状（服务端受限执行+Docker 沙箱）；b) **本地桥**——参考 Claude Code Remote Control 模式（本地装一个薄执行体，仅出站连接，web 只是窗口；设备信任+逐次审批）或更便宜的：**把 DocMind 暴露为 MCP server**，让用户自己已装的 Hermes/Pi/Claude Code 来调用你的知识库与工具（反向整合，比自研桌面端便宜一个数量级，且直接呼应你决策文档"记忆引擎独立成 MCP Server"的构想）。
  - **P2（多租户客户需要执行不可信代码时）**：再上 microVM 级沙箱（K8s 已有 manifest → 走 agent-sandbox/Kata/gVisor 路线）。
- **反方观点 A（"干脆转 CLI/桌面"）**：2026 注意力与星数都在 CLI/桌面（DeepSeek Harness 24.4 万星、Pi 10 万+、Hermes 十万级、OpenClaw 29.5 万）；CLI 项目对求职的 star 杠杆更大。**回应**：这个论点对**新项目**成立（所以你的决策文档 CLI 第一是对的），但对 DocMind 不成立——重写成本高、丢弃多租户叙事，且"效果"差距不来自壳（§0.1）。
- **反方观点 B（"保持现状不动"）**：DocMind 已收口（0 issue/0 PR/CI 全绿），有求职价值，别再动。**回应**：A3/A6 已证明"内核缺口"是真实且廉价的——不做，你的 agent 在"能感受到的效果"上就是比人家差；做，边际成本 1–2 周。**这也是你 MEMORY 里"用户偏好能感受到的改进"的正确用法：补 resume 和任务结果，就是"能感受到"的改进。**

**(2) 个人 AI 工作台（新项目）：按 v4 决策执行——CLI 第一入口、单进程、入口无关；把它做成"内核+薄入口"，桌面/Web 以后只是加壳。**
证据：Hermes 是"本地运行时+多入口"（你亲自跑过，踩坑记录也在），Pi 证明"4 个工具+<1000 token 提示词"也能是 10 万星 harness（**反过度设计**）；OpenClaw（Pi 内核+多平台壳）是 2026 最大开源现象。你的"单进程可跑"约束与"harness=内核工程"的行业共识完全同向；**唯一要防的是"入口多样化焦虑"**——先内核后壳，壳永远是最便宜的部分（Claude Code 给桌面加的每样东西，内核都没变）。

**(3) 关于"微信"**：2026 年的"养龙虾"生态（腾讯 QClaw/WorkBuddy 微信直连、智谱专项模型、字节豆包工作）进一步证明决策文档的判断——**通道层已被大厂做透，不要去卷**；如果做，就用官方通道做个薄插件（P3 定位正确），把精力留给你真正稀缺的东西：跨时间记忆与任务连续性。

## 6.2 建议的反方观点（独立列出，避免自说自话）

1. **"桌面/CLI 才是 2026 的形态答案，Web 在走下坡"**：证据面（Roo 关 IDE 转云、桌面大战、CLI 叙事爆棚）确实强；但反证据同样强（消费级 web/App 统治、Supermew/DeerFlow/OpenHands 都是 web 系、Manus/Devin 云 VM 模式、语义上"壳随场景"）。**天平读法：形态之争没有全局答案，只有任务匹配**——你的任务是"知识工作+多用户"，web 匹配。
2. **"你缺的是模型能力，不是 harness"**：被 arXiv 2605.23950 与 mini-SWE-agent 数据反驳（harness 层可造成同级模型 8–12pp 差；100 行 harness 也 >74%）。**但也要承认**：如果你追求的是 SWE-bench 类编码能力，任何 harness 都救不了一个弱模型——"内核"与"模型"是乘法关系。
3. **"双形态 = 两倍维护"**：真实风险。缓解：**第二种形态必须是"薄壳"**（CLI 客户端 / MCP server / 本地桥），**绝不复制业务逻辑**；先例们的教训是内核边界一旦泄漏到 UI 层，就退化成两个产品（Gemini CLI vs Jules 就是"两个产品"的现状）。

## 6.3 回到你最初的问题，一句话

> **"人家"里有桌面也有 Web，真正的共同点是"一个内核、多种入口、执行位置可插拔"；"效果"来自行动面与内核成熟度，不来自壳。你的 DocMind 在形态上没错（Supermew 们证明了这点），在效果上欠缺的部分是具体且可修的——补上"手和钥匙"与那几件内核，这个问题就永远消失了。**

---

# 附录 A · 关键来源索引（正文内联之外的汇总）

- Harness 定义：arxiv 2606.10106 / Anthropic SDK 博文（2025-09-29）/ Effective harnesses（2025-11-26）/ LangChain Anatomy（2026-03-10）/ Microsoft Learn / Willison / arxiv 2605.23950 / CMU 综述（picrew.github.io/LLM-Harness）
- 版图：code.claude.com/docs、developers.openai.com/codex、pi.dev + earendil.com、github.com/NousResearch/hermes-agent、github.com/block/goose、docs.opencode.ai、github.com/bytedance/deer-flow、github.com/OpenHands/OpenHands、manus.im/blog/manus-sandbox、devin.ai、openai.com（ChatGPT agent）、jules.google、github.com/icey1287/SuperMew
- 差异：The Verge（agent 与浏览器上下文）、IEEE Spectrum（computer-use 信任）、simonwillison.net（致命三要素）、36kr/少数派（CLI 复兴）、openai.com/index/unlocking-the-codex-harness
- 沙箱：e2b.dev / daytona.io / modal.com / developers.cloudflare.com/sandbox / fly.io / vercel.com/docs/vercel-sandbox / codesandbox.stream / northflank.com / aws.amazon.com/fargate / cloud.google.com/run / github.com/kubernetes-sigs/agent-sandbox；浏览器沙箱与 MCP/native messaging 安全（modelcontextprotocol.io、developer.chrome.com native messaging、chromium security FAQ）
- 本机一手证据：`AI-projects/hermes-wechat-experiment/`（SOUL.md、gateway_state.json、channel_directory.json、skills/autonomous-ai-agents/hermes-agent/SKILL.md v2.1.0、logs/）；`AI-projects/project-direction-decision.md`；`AI_projects/DocMind/backend/app/agent/context.py`、`AGENT_ARCHITECTURE.md`；同系列 `014_agent-metrics-spec.md`、`010_agent-core-audit.md`、`012_reference-rag-architecture.md`

# 附录 B · 未找到 / 未核实清单

- 【未找到】"harness" 一词的第一使用者；"The harness is the product" 的首发出处；"最强 agent 大多是 CLI/桌面"的原始权威文句；Inflection Pi 的桌面 agent 转型；Physical Intelligence 的 agent 软件。
- 【未核实】Hermes 星数（27k/47k/142k 口径矛盾）；DeepSeek Harness 24.4 万星（子 agent 经 GitHub API 一次实测，未复核）；部分 2026 评测数字（SWE-bench/Terminal-Bench 各榜口径混乱，至少相差 10pp）；Mars/Daytona/Modal 部分价格细项（第三方转述）。
- 【方法学】本机 WebFetch 全域被网络策略拦截；绝大多数网页未能逐字复核，【确证】= 官方来源或多源一致（经搜索摘要）。价格、星数、版本号等易变信息落地前请以官方页面复核。
