# DocMind 面试口径手册（Cheat Sheet）

> 事实基线：`origin/develop` @ `653715d2`（2026-10-24）。9 个修复 PR 已合入 develop，对应 issue #492–#500 已关闭；#507–#514 仍 open。
> 用法：每题按 **承认 → 根因 → 止损（已合并 PR / issue）→ 未完成（issue 链接）→ 教训** 五段回答。先承认，永远比被戳穿强。
> 禁止：把目标态写成已完成；写未复测的并发 / 延迟数字；使用"精通 / 企业级 / 高并发"等无证据形容词。

---

## 0. 一页速查

### 0.1 已合入的 9 个修复 PR（可以讲"已修"）

| PR | 合并提交 | 日期 | 修什么 | 对应 issue（已关闭） |
|---|---|---|---|---|
| #504 | f67ef9f8 | 2026-10-24 | 登录校验 / 错误提示收敛 + MCP 异常类兼容 | #492 |
| #503 | a80ae306 | 2026-10-24 | 数据库迁移纳入启动与发布流程（一次性 migrate + schema guard） | #495 |
| #502 | ee30ae4c | 2026-10-24 | bcrypt 校验 / 哈希移入线程池，解除事件循环阻塞 | #500 |
| #506 | 175006de | 2026-10-24 | SDK stdio 运行时：stdout 纯净 / cancel 收敛 / 严格 Redis / 幂等与退出 | #493 |
| #520 | 0ff446e9 | 2026-10-24 | ACP cancel 真取消 + stdout 零泄漏 | #494 |
| #517 | 00dbeaa7 | 2026-10-24 | API Key scope 强制（端点读写按 scope 门禁，api-keys 仅 JWT） | #496 |
| #505 | 1d4d4060 | 2026-10-24 | Webhook 配置门控 / 限流前置 / 租约 fencing / 复用历史 | #497 |
| #515 | 963aeaa1 | 2026-10-24 | Commands 契约信封 / Redis 锁与幂等 / 内置命令收口 | #498 |
| #518 | 653715d2 | 2026-10-24 | Session Fork 路由 / 响应 / 幂等 / seq / history_limit 契约收口 | #499 |

### 0.2 仍未完成（只能讲"进行中"，不能讲"已做"）

| issue | 内容 | 状态 |
|---|---|---|
| [#507](https://github.com/sijie-Z/DocMind-RAG/issues/507) | P0 安全：轮换泄露密钥 + gitleaks | open，未执行 |
| [#508](https://github.com/sijie-Z/DocMind-RAG/issues/508) | Chat 深析 SSE / HITL | open |
| [#509](https://github.com/sijie-Z/DocMind-RAG/issues/509) | 工具注入门控 + utility 伞形 + 别名入口归一 | open |
| [#510](https://github.com/sijie-Z/DocMind-RAG/issues/510) | 可观测收敛 + JSONL 调用级记录 | open |
| [#511](https://github.com/sijie-Z/DocMind-RAG/issues/511) | ACL 最小实现（8–12 周） | open |
| [#512](https://github.com/sijie-Z/DocMind-RAG/issues/512) | Eval v1.5（80 条 + dense/hybrid 双基线 + rerank live） | open |
| [#513](https://github.com/sijie-Z/DocMind-RAG/issues/513) | docs/METRICS.md + facts 对齐 | open |
| [#514](https://github.com/sijie-Z/DocMind-RAG/issues/514) | 归档 eval_first_report.json 0/10 与过期产物 | open |

### 0.3 三个实验，三句话（永远不要拼成一句）

| 实验 | 样本 | 指标 | 数字 | 不能怎么讲 |
|---|---|---|---|---|
| 30 题 structured planner | 30 题（20 L1 + 10 L2） | keyword coverage | ALL 30 **78.7%**；L1 20 题 **93.1%**；L2 50% | 不能说"任务成功率 93.1%" |
| 001 → 004 对比 | L1 20 题 | keyword coverage | ~69% → 93.1%（+24pp） | 001 / 004 模型不同，有 confound；不要把 +24pp 当因果 |
| 5 题人工多步 Plan 对照 | 5 题，同模型 | keyword coverage | 76% → 92%（**+16pp**） | 不能说"30 题实验 +16pp" |

### 0.4 现场禁语替换表

| 不要说 | 改成 |
|---|---|
| "120 条 eval / 4 组基线 / bootstrap / 87% / 0.89" | "当前 28 条、7 指标、零容差、Wilson；Eval v1.5 扩到 80 条 + 双基线（#512，进行中）" |
| "fork 50 并发→1" | "修复前 5 并发建 5 个、10 并发建 10 个；#518 已合入契约 / 幂等；50 并发复测未做" |
| "ACP cancel <2s" | "#520 已合入 cancel 真取消；延迟未测，不写数字" |
| "Locust / 火焰图" | "Python 并发脚本，p50 12.4s；工具与火焰图未做" |
| "3 次回归" | "没有 3 次回归记录；只有单次压测报告" |
| "密钥已轮换" | "密钥未轮换；#507 已开，方案是轮换 + gitleaks + SECURITY.md" |
| "CI 全绿 / 8 项全部完成" | "代码已合并；SDK / ACP 的缺口由 #506 / #520 修复；修复后的回归数字以 CI 为准" |
| "企业级 / 高并发 / 精通" | 直接说测得的条件与数字（如"50 并发、单进程 8010"） |

---

## Part A — DARK §7 的 20 个必问问题

### Q1. "这个项目多少人做的？"（追问：2223 个测试谁维护？）

- **承认**：一个人做的；测试主要是我自己写的；"2223 passed / 51 skipped"是 README 2026-10-15 的冻结快照，不能代表当前默认分支。
- **根因**：单人项目没有第二双眼睛，只能靠对抗审计（FINDINGS / DARK / REDTEAM）和 CI 门禁补位；测试数量不等于测试质量。
- **止损**：把"可复现"落到命令：checkpoint 49 个单测 2026-10-24 复跑 `49 passed`；索引 8 个真实 ES 集成测试；Eval Gate 每 PR 跑 7 指标零容差。9 个修复 PR 已合入 develop。
- **未完成**：全量测试数没有重新统计；README 冻结数字待 #513（METRICS.md + facts 对齐）统一口径。
- **教训**：不要用测试数量证明质量；用"哪个门禁、在什么触发条件下、拦住什么回归"证明。

### Q2. "8 项 harness 功能都完成了吗？"

- **承认**：8 项 harness 的代码已合并（9 个 squash 提交，PR #482 / #483 / #485–#491），但我不会说"全部完成"——合并时 SDK / ACP 还有 2 个失败负向测试，SDK stdout 被 SQL 日志污染，ACP cancel 后任务仍 COMPLETED。
- **根因**：验收标准是"代码合并 + 自测通过"，不是"契约 / 真实进程验证"。
- **止损**：issue #493 / #494 记录；#506（SDK stdio 运行时）与 #520（ACP cancel + stdout 零泄漏）已合入 develop；#515（commands）、#518（fork）也已合入。
- **未完成**：修复后的 ACP / SDK 回归数字未在本次材料中复跑；以 PR #506 / #520 正文与 CI 为准（不虚构 passed 数）。
- **教训**：协议入口的验收必须是"真实进程 + stdout 0 字节 + cancel 后状态 CANCELLED + 负向测试全绿"，不是单测通过。

### Q3. "登录 50 并发 p95 多少？"

- **承认**：50 并发正确密码登录 p50 **12,421 ms**、p95 **13,432 ms**（50×200，2026-10-23，单进程 8010）；同刻 `/health` p95 4,692 ms。
- **根因**：bcrypt 同步校验 / 哈希在事件循环里执行（修复前定位在 `auth_service.py` 的校验路径），登录 burst 拖慢全站。
- **止损**：issue #500；PR #502 已合入，bcrypt 校验 / 哈希移入线程池。
- **未完成**：修复后没有同 harness 复测，所以不写 0.4s / P99 0.9s；复测未排期、无独立 issue。
- **教训**：性能故事要有"坏数字 → 根因 → 修复 → 复测"四段；缺最后一段就只能讲前三段。

### Q4. "数据库迁移怎么做的？"

- **承认**：修复前 `alembic current` = `011_organization_settings`、heads = `014_api_keys`，按 README 启动后登录 500（`Unknown column 'chat_sessions.parent_session_id'`）；迁移只在 Makefile 的手动目标里。
- **根因**：发布工程缺失——迁移没有进启动 / 发布入口；这不是环境问题。
- **止损**：issue #495；PR #503 已合入：一次性 migrate + schema guard 纳入启动与发布流程；CI 有真实 MySQL 的 migration smoke 步骤。
- **未完成**：干净 clone → compose up → 登录 200 的端到端 smoke 未在本次材料中复验；无独立 issue。
- **教训**：可部署性是一条验收标准，不是 README 的一句话。

### Q5. "API Key 权限怎么隔离？"

- **承认**：修复前仅带 `scopes:["rpc"]` 的 key 能访问 `/chat/conversations`、`/agent/sessions`、`/api-keys`（含列出全部密钥），全部 200；`chat:read` / `knowledge:read` 从未被消费。
- **根因**：scope 只在签发时记录，没有在端点侧强制。
- **止损**：issue #496；PR #517 已合入：端点读写按 scope 门禁，api-keys 仅 JWT；当前代码 `require_scope` 已接入 28 处。
- **未完成**：没有按"端点 × scope"的渗透测试矩阵入库；建议补。
- **教训**：最小权限要落在"每个端点消费哪个 scope"的矩阵上，不能只写在 token 里。

### Q6. "密钥怎么管理？"

- **承认**：密钥没有轮换。公开仓库历史 `fd00b3c:backend/.env.docker` 里的 `SECRET_KEY` / `JWT_SECRET_KEY` 与当前 `.env` 的 SHA-256 前 16 位一致（`b6ef735f9d2ad6d4` / `019987b2c7d69aeb`）；没有 gitleaks、没有 SECURITY.md、没有轮换记录。
- **根因**：把 `.env` 当配置而不是机密；改 `.gitignore` 不会清除历史。
- **止损**：issue #507 已开（P0），方案是轮换全部泄露凭据 + gitleaks 接 pre-commit / CI + SECURITY.md；不 rewrite 历史。
- **未完成**：#507 仍是 open；轮换前不能写"密钥已轮换"。这是安全叙事上的一票否决项，投递前必须先处理。
- **教训**：安全债是一票否决；作品投递前先做轮换，再谈功能。

### Q7. "fork 的幂等怎么保证？"

- **承认**：修复前同 `Idempotency-Key` 并发 5 次建 **5 个**子会话、10 次建 **10 个**；只有串行 2 次才复用。
- **根因**：幂等键没有进入创建事务的原子判重；进程内锁不跨 worker。
- **止损**：issue #499；PR #518 已合入：路由 / 响应 / 幂等 / seq / history_limit 契约收口（Redis 锁 + 幂等）。
- **未完成**：没有 50 并发复测，所以不写"50 并发→1"；复测未排期、无独立 issue。
- **教训**：幂等要按"并发同键"验收，不能按"串行两次"验收。

### Q8. "cancel 真的能停吗？"

- **承认**：修复前 ACP cancel 返回 `{"cancelled": false}`；任务最终 **COMPLETED**；错误启动路径 stdout 有 **12,587 字节** SQL 日志，违反 stdout 0 字节；`tests/api/test_acp_stdio.py` 实测 20 passed / 2 failed。
- **根因**：stdio 进程没 `init_redis()`；cancel 只改了返回体没改任务状态；SQLAlchemy `echo=DEBUG` 污染 stdout。
- **止损**：issue #494；PR #520 已合入：cancel 真取消 + stdout 零泄漏；#506 同时收口 SDK stdio。
- **未完成**：cancel 延迟没有 <2s 的证据，不写；ACP 扩展冻结，先修再谈。
- **教训**：协议适配器的验收是"真实进程 + stdout 0 字节 + cancel 后状态 CANCELLED + 负向测试全绿"。

### Q9. "39 个工具怎么选？会不会选错？"

- **承认**：当前注册 **39** 个工具（`docs/facts.json`；`check_doc_facts.py --print-derived`），默认注入 36 个；工具 schema 19,001 chars，默认 36 个约 17,592 chars ≈ **5,026 tok/步**；terminal / feishu / delegate 无条件注入。
- **根因**：功能按"再加一个工具"扩展，没有常驻 schema 预算；审批又按模型原始名判定，别名可绕过。
- **止损**：issue #509 已开（P1）：`enabled_predicate` + 统一 `build_tool_schemas`（loop / executor / planner / 端点同源）；默认关 terminal / feishu / delegate（预计降到约 12,050 chars ≈ 3,440 tok，**-32%**）；语义同族合并 + 低风险 `utility` 伞形；别名在 executor 入口 canonicalize 后再走审批。
- **未完成**：#509 open；目标 **10–12** 个常驻（不是 8；8 会破坏审批 / 沙箱语义）。
- **教训**：工具数量是成本，不是功能勋章；先做门控，再做合并。

### Q10. "RAG 效果怎么证明？"（追问：28 条够吗？）

- **承认（强项）**：28 条 clause-level golden（25 正 + 3 负，24 条 near-miss），7 指标零容差 floor / cap，Wilson 95% CI；live ES + Qwen3 冒烟（2026-10-09）：ClauseHit@1 **80%**、@3 = @5 **84%**、MRR **0.82**、NearMiss@5 12.5%、EmptyAcc 100%、0 错误（n=25，CI 65.3–93.6%）；Eval Gate 每 PR + main/develop push 跑，接入 `CI Pass` 必需检查。
- **根因 / 边界**：只覆盖检索一层，不含 rerank / LLM 生成；28 条样本小；CI 用确定性伪嵌入，与 live 数字不可混比。
- **止损**：Eval Gate 本身是止损：任何指标越界即红；基线变更必须显式 PR，不能"红灯直接重盖"。
- **未完成**：28 条不够；#512 Eval v1.5 扩到 80 条 + dense/hybrid 双基线 + rerank live；`backend/eval_first_report.json` 0/10 旧产物由 #514 归档。
- **教训**：28 条能证明"没退化"，不能证明"够好"；回答时要分开说。

### Q11. "Agent 任务成功率多少？"

- **承认**：没有 agent golden set、没有 pass^k、没有任务成功率 / 成本落库；`014_metrics-spec.md` 大部分仍是规格。
- **根因**：评测投入集中在检索层，Agent 层没有定义"什么算成功"。
- **止损**：30 题规划实验是现有最接近的结果证据，但它是 keyword coverage（L1 20 题 93.1%、ALL 30 78.7%、L2 50%），不是任务成功率；+16pp 是另一组 5 题人工 Plan 对照。
- **未完成**：agent 结果指标未排期；#512 仍只做检索 eval。
- **教训**：不要把 keyword coverage 说成成功率；没有 pass^k 就承认没有。

### Q12. "GraphRAG 在生产链路里吗？"

- **承认**：GraphRAG 是独立 API（`knowledge.py` 的 stats / clear 等端点），不在主检索链路；主链路是 adaptive → ES keyword + vector → RRF k=60 → MMR → rerank → LLM stream。
- **根因**：图能力按"先有 API"落地，没有进主链路；节点没有 document_id，ACL 模式下会漏。
- **止损**：不把它当卖点；STRATEGY 明确不做自动抽取扩面（`GRAPH_AUTO_EXTRACT=False` 的收敛方向）。
- **未完成**：没有生产链路接入计划；#511 要求 ACL 模式下禁用或补 document_id。
- **教训**：孤岛功能不是资产；要么进主链路，要么不写。

### Q13. "Terminal 在 Docker 里跑过吗？"

- **承认**：Terminal 只有单测；真实 Docker 未验证（Docker 集成 5 skipped、e2e 未跑；Docker Desktop npipe 不可用；WSL dockerd 容器后端连不上）；`TERMINAL_ENABLED=false` 时存量容器不清理（`terminal_service.py` 的 `startup()` 直接 return）；配额 / owner 仅进程内计数，多 worker 会放大。
- **根因**："完成"= 代码 + 单测，不是"真 PTY 可用"。
- **止损**：#489 已合入（容器持久 PTY + WebSocket 票据 + xterm 面板）；但真环境验证没有闭环。
- **未完成**：真 Docker / PTY 验证无独立 issue（本次不虚构链接）；建议开 issue 跟踪。
- **教训**：容器 / PTY 类功能必须在真 Docker 上验收；单测只能证明状态机。

### Q14. "为什么有四套 tracing？"

- **承认**：仓库里有四套重叠 tracing（Langfuse 5 模块 6 span、`core/trace_logger`、`agent/tracing.TraceStore`、Mining 898 行），多数软降级 / 无消费者；OTel 有 exporter 但无 collector；`trace_id_var` 全仓零 set。
- **根因**：观测按"加一套"演进，没有统一事实源。
- **止损**：issue #510 已开：先加 JSONL 调用级记录（model / tokens / latency / prompt hash，不含正文），再删 Langfuse / trace_logger / TraceStore / Mining；OTel 保留 optional extra 或删除并在 release note 说明。
- **未完成**：#510 open；删除未执行。
- **教训**：观测的价值是"能回答哪个请求慢 / 贵 / 错"，不是 tracer 的数量。

### Q15. "多租户是行级还是库级？"（追问：外部系统 ACL 呢？）

- **承认**：多租户是行级 `org_id` 强制过滤 + **23** 个 RBAC 权限点（8 个零消费、15 个在用）；没有外部系统 ACL 同步；检索缓存键是 `org|topk|mode|q`（不含 user_id），同 org 用户 B 可能命中 A 的结果；直读工具只按 org + document_id；GraphRAG 节点无 document_id；飞书只有 bitable / tenant token。
- **根因**：从"组织隔离"到"文档级权限感知"之间缺数据访问层。
- **止损**：issue #511 已开（P1，8–12 周）：`departments/groups/doc_acl/retrieval_audit` 四表；`get_document_for_user` + retriever + 所有 document_id 直读工具收口；缓存键加 `user_id/acl_version`；GraphRAG 补 document_id 或 ACL 模式禁用；先轮询同步（SLO 15 分钟）。
- **未完成**：#511 open；未实现。
- **教训**：行级租户隔离不等于权限感知检索；ACL 要按"8 类入口 × 冷 / 热缓存"验收。

### Q16. "和 Dify / RAGFlow 比呢？"

- **承认**：不和它们比功能密度；它们在生态、连接器、OCR / 表格、交付、社区规模上更强。
- **根因 / 定位**：DocMind 的差异点是条款级引用 + eval 门禁 + 可审计执行链；但这些目前只在检索层有硬证据。
- **止损**：用 28 条 clause-level eval 和 9 个修复 PR 讲"质量工程"；不吹"企业版"。
- **未完成**：Agent 层结果指标、ACL、SSO、商业交付都没有；#511 / #512 只是开始。
- **教训**：选一个有证据的轴对比；承认对方更强的轴。

### Q17. "为什么不做工作流编辑器？"

- **承认**：Workflow 编辑器代码在，但没有消费者 / 真实环境证据；当前主路径是 Chat / Agent 工作台。
- **根因**：早期按"平台化"铺功能，边界没想清楚。
- **止损**：STRATEGY 决定只保留引擎 / API / 页面，隐藏菜单；不删路由（会拉红 3 个测试，且 AgentMemory 失去入口）。
- **未完成**：菜单隐藏尚未实施（无独立 issue）。
- **教训**：知道什么不做，比多做一条功能线更难；工作流编排不是本项目的主战场。

### Q18. "这个项目能部署吗？"

- **承认**：修复前按 README 启动会登录 500（迁移缺失）；现在 #503 已把迁移纳入启动 / 发布（一次性 migrate + schema guard）。
- **根因**：发布工程缺失，不是环境问题。
- **止损**：#495 closed；#503 已合入；CI 有真实 MySQL 的 migration smoke 步骤。
- **未完成**：干净 clone → compose up → 登录 200 的端到端 smoke 未在本次材料中复验；无独立 issue。
- **教训**：部署是产品能力，必须有一条"干净环境从零到登录"的验收命令。

### Q19. "最大的技术债是什么？"

- **承认**：最大的技术债是功能通胀 + 验收标准太低：把"代码合并 / 单测通过"当成"能力可用"；证据是 39 工具、4 套 tracing、8 项 harness 的缺口、GraphRAG 孤岛、Workflow 编辑器无消费者。
- **根因**：单人项目用功能数量替代验收深度；没有统一的 METRICS / 契约门禁。
- **止损**：9 个修复 PR + Eval Gate + 8 个索引集成测试 + 49 个 checkpoint 单测，是把"验收"往前提的一步；#509 / #510 / #511 是减法。
- **未完成**：减法未完成；#513 还没建立统一 METRICS。
- **教训**：先定义"什么叫完成"，再写功能。

### Q20. "如果重做一次，你会怎么改？"

- **承认 / 回答**：先做 eval、迁移、可观测，再做功能；工具面先门控到 10–12 个常驻；协议入口只保留一个真源（不要 SDK / ACP / Webhook / Terminal 四条同时铺）；ACL 从第一天就作为数据访问层设计；不碰工作流编辑器 / IM 网关 / 插件市场。
- **根因**：重做时会先写"验收命令"，再写功能。
- **止损**：这些判断已经写进 STRATEGY v0.2 和 issue #509 / #510 / #511。
- **未完成**：路线图是计划，不是已完成。
- **教训**：架构决策要用实验数据支撑（69% → 93.1% 的规划实验就是例子），不是功能清单。

---

## Part B — REDTEAM §2 的 5 个致命追问

> 这 5 条是对旧简历稿的"处刑"。新稿的写法逐条对应如下。

### F1. "目标态写成已完成：120 条 / 4 组基线 / bootstrap / 87% / 0.89 / 拦截 3 次？"

- **承认**：旧稿把这些写成了已完成。clone 仓库只有 28 条、1 个 fake 基线、Wilson 不是 bootstrap，一条都复现不了。
- **根因**：把 M1 / M2 目标写进了当前态；没有"当前 / 目标"两栏纪律。
- **止损**：新稿只写 28 条 / 7 指标 / 零容差 / Wilson / 80-84-0.82；目标态单列；#512 只承诺 80 条 + 2 组基线 + rerank live。
- **未完成**：120 条 / 4 组基线 / 87% / 0.89 均未开始。
- **教训**：简历上的每个数字都要能在默认分支用一条命令复现；否则删掉。

### F2. "0.4s 是未合并的 PR？"

- **承认**：0.4s 来自未合并 PR #501（closed，merged=false），没有产物；12.4s 有压测表和脚本；没有 Locust、没有火焰图。
- **根因**：把 PR 里的目标数字当成已达成；没有复测。
- **止损**：#500 已由 #502 修复（bcrypt 移线程池）；当前只写 12.4s 的坏数字和根因。
- **未完成**：修复后没有同 harness 复测；不写 0.4s / P99 0.9s。
- **教训**：性能数字必须绑定"同一脚本 + 同一环境 + 修复前后"。

### F3. "自己的审计报告反证简历？"

- **承认**：旧稿写"fork 50 并发→1、ACP cancel<2s"，但我自己的 FINDINGS 实测 fork 10 并发建 10 个、ACP cancel 后任务 COMPLETED；这是自证矛盾。
- **根因**：写简历时没有回查审计报告；把目标态当成了修复结果。
- **止损**：#518（fork 契约 / 幂等）、#520（ACP cancel）已合入；新稿只写"已修"，不写未复测的并发 / 延迟数字。
- **未完成**：50 并发→1、cancel<2s 都没有复测；不写。
- **教训**：自己的审计报告是最高优先级的证据源；先读它，再写简历。

### F4. "三个实验拼成一句话？"

- **承认**：旧稿把三个实验拼成一句"93.1%（+24pp）"。实际：93.1% 是 L1 20 题 keyword coverage；ALL 30 是 78.7%；L2 50%；+16pp 是另一组 5 题人工 Plan 对照（76% → 92%）。
- **根因**：只挑最好看的数字，混了样本和指标。
- **止损**：新稿按"样本 + 指标 + 数字"三列分开写；93.1% 只按 L1 20 题口径；+16pp 只按 5 题对照口径；不写 +24pp（那是 004 vs 001 的另一个对比，且 001 / 004 模型不同）。
- **未完成**：30 题样本仍小；没有 agent 成功率。
- **教训**：一个句子只能放一个实验；换样本 / 换指标必须换句子。

### F5. "密钥没轮换？"

- **承认**：没有轮换。公开历史密钥与当前 `.env` 一致；没有 gitleaks / SECURITY.md / 轮换记录；大厂安全审查一票否决。
- **根因**：把 `.env` 当配置；以为删掉文件就安全。
- **止损**：#507 已开（P0）；方案：轮换全部泄露凭据 + gitleaks 接 pre-commit / CI + SECURITY.md；不 rewrite 历史。
- **未完成**：#507 open；轮换未执行。
- **教训**：投递前先处理安全债；否则功能越强，反证越强。

---

## Part C — 反问与收尾

- **"如果只能看一条证据，看哪条？"** → `make eval-ci` 的零容差门禁 + 28 条 golden 报告；它能证明"检索质量不会悄悄退化"。
- **"你最大的成长是什么？"** → 从"功能清单"转向"验收标准"：9 个修复 PR、Eval Gate、8 个索引集成测试、49 个 checkpoint 单测，都是把"可用"变成"可验证"。
- **"你接下来 30 天做什么？"** → #507（轮换 + gitleaks）、#512（Eval v1.5 80 条）、#509（工具门控）；三件事都有验收命令。
- **"你还有什么没解决？"** → Agent 层结果指标、ACL、登录性能复测；不回避。

---

## Part D — 事实来源

| 编号 | 事实 | 来源 |
|---|---|---|
| S1 | 28 条 golden（25 + 3；24 near-miss） | `backend/evals/golden_set/clause_set_v1.json`；计数命令见 `RESUME_CURRENT.md` §4.1 |
| S2 | live 80 / 84 / 0.82、NearMiss 12.5%、EmptyAcc 100%、0 错误、CI 65.3–93.6% | `backend/docs/eval-gate-phase1.md` §5；`_orchestrate/round2/_eval208_report_baseline.json` / `.md` / `_eval208_run_baseline.txt` |
| S3 | 7 指标零容差 + Eval Gate | `backend/evals/baselines/ci_baseline.json`；`backend/evals/runner.py:75-81,701-731`；`.github/workflows/ci-fast.yml` `eval-gate` / `ci-pass` |
| S4 | Wilson CI | `backend/evals/metrics.py:104` |
| S5 | 50 并发登录 p50 12,421ms / p95 13,432ms | `_scratch/audit/stress_round2.py:72`；`_scratch/audit/FINDINGS.md:210` |
| S6 | 3 条不变量 + 8 个集成测试 + 3 个变异 | `backend/tests/integration/test_index_verification.py`；`.github/workflows/ci-fast.yml` `integration` job |
| S7 | checkpoint 49 个单测 | 4 个单测文件；2026-10-24 `49 passed`（命令见 `RESUME_CURRENT.md` §4.2） |
| S8 | 30 题 / 5 题实验数字 | `docs/research/004_structured_planning.md` §验证实验、§3、§4.1；`docs/blog/dag-vs-planning.md` |
| S9 | 9 个修复 PR | `git log origin/develop --format="%h %ad %s" --date=short -9` |
| S10 | issue #492–#500 closed；#507–#514 open | `_scratch/reliability-api/open-issues.json`；GitHub Issues API 快照 `_scratch/career-gh/issues_all.json` |
| S11 | 14 merged / 11 项目 | GitHub Search API 查询（`_scratch/career-gh/opensource_merged.json`） |
| S12 | pnpm #12284 / storybook #35055 的 review | GitHub Reviews API（`_scratch/career-gh/reviews_pnpm_pnpm_12284.json`、`reviews_storybookjs_storybook_35055.json`） |
| S13 | MiqroForge 241 / 188（待确认） | `_scratch/audit/REDTEAM_FINDINGS.md` §2；本次核查 repo 404 / search 0 |
| S14 | 39 工具 / schema 19,001 chars / 默认约 5,026 tok/步 | `docs/facts.json`；`backend/scripts/check_doc_facts.py --print-derived`；测量记录见 `_scratch/audit/STRATEGY.md` §4.2 / `REDTEAM_FINDINGS.md` B3 |
| S15 | 23 个 RBAC 权限点 / 缓存键不含 user_id | `backend/app/models/rbac.py:24-92`；`backend/app/rag/cache.py:330-338` |
