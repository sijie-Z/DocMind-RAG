# DocMind 求职材料 · 简历（当前态，可复现）

> 基线：`origin/develop` @ `2b0574ab`（2026-10-25）；工作树 `_scratch/wt-live-evidence`（分支 `docs/live-eval-evidence`）
> 原则：本文件只写"默认分支上存在 / 本地可复跑"的事实；每条数字给出来源命令或报告路径；目标态单独成节并按实际状态标注（已合入标 PR / commit，未完成标进行中 / 待批准）；不写无证据形容词。
> 适用：2027 届 AI Agent / 后端 / AI 工程 校招与实习。

---

## 0. 可投状态总览

| 模块 | 当前可投 | 说明 |
|---|---|---|
| RAG 评估门禁 | 是 | 80 条 golden（v1.5，60 正 + 20 负）、7 指标零容差、Wilson CI、live 数字有原始报告 |
| 索引正确性 | 是 | 3 条不变量 + 8 个真实 ES 集成测试 |
| Agent checkpoint | 是 | 49 个单测，2026-10-24 本地复跑 `49 passed` |
| 规划实验 | 是（口径受限） | 只能按 L1 20 题 keyword coverage / ALL 30 / 5 题对照分开写 |
| 可靠性修复 | 是 | 9 个修复 PR 已合入 develop，对应 issue #492–#500 已关闭 |
| 开源贡献 | 是 | 14 merged / 11 项目；只展开 pnpm #12284、storybook #35055 |
| MiqroForge | 待确认 | 241 PR / 188 merged；性质与公开范围待本人确认，未确认前不投 |
| 120 条 / 4 组基线 / bootstrap / 87% / 0.89 / fork 50 并发→1 / ACP<2s / Locust / 火焰图 / 3 次回归 / 密钥轮换 | 否 | 未完成或未合并，见 §5 |

---

## 1. 简历正文（当前态）

### 1.1 个人项目：DocMind（独立完成，2025–2026）

**项目定位（一句话）**：一个带条款级检索评估门禁、可恢复 Agent 运行时与多租户治理的 RAG / Agent 系统；本简历只写能在默认分支复现的部分。

- **条款级 RAG 评估门禁**：建立 clause-level golden 与 7 项指标（ClauseHit@1/@3/@5、MRR、EmptyAcc、NearMiss@5、errors），零容差 floor/cap 比对与 Wilson 95% 置信区间；CI 的 Eval Gate 在每个 PR 与 main/develop push 上先删后建评估索引、用确定性伪嵌入（`mode=fake`，只测检索层，不含 rerank / 缓存 / LLM）跑分并比对入库基线，接入分支保护必需检查 `CI Pass`。v1.5 扩展集 80 条（60 正 + 20 负）的 fake 门禁数字为 70.0 / 76.7 / 80.0 / 0.74——这是防回归的工程保守下界，不当作检索质量对外。同一题集的 live 嵌入结果（2026-10-25；本机 Ollama `qwen3-embedding:0.6b` 1024 维 + hybrid 检索，LLM 辅助关闭，**非 rerank**）：ClauseHit@1 75.0%、@3 86.7%、@5 88.3%（Wilson 95% CI 77.8–94.2%）、NearMiss@5 21.7%、MRR 0.8125、负例 EmptyAcc 100%、0 错误。历史口径：v1 的 28 条 live 冒烟（2026-10-09，Qwen3-Embedding 0.6B，25 正 + 3 负，24 条声明 near-miss）：ClauseHit@1 80%、@3=@5 84%、MRR 0.82、NearMiss@5 12.5%、负例 EmptyAcc 100%、0 错误（正例 n=25，95% CI 65.3–93.6%）；v1 与 v1.5 题集不同，不可直接比较。【S1b】【S2b】【S1】【S2】【S3】【S4】
- **索引正确性不变量**：把"文档状态 = INDEXED ⟹ 可检索"落成 3 条可执行不变量（至少 1 条 chunk 落库；至少 1 条带 embedding；organization_id 与文档一致），用 8 个真实 Elasticsearch 集成测试覆盖"一条未落库 / 他人文档 chunk 挡枪 / 无 embedding / org 不匹配 / 全量落库 / 超出预期 / 部分落库告警 / 部分缺向量告警"；3 个变异验证证明删掉任一断言对应用例必红。测试在 CI `integration` job 运行（develop push 与指向 develop 的 PR；该 job 不在 `CI Pass` 必需检查聚合里）。【S6】
- **Agent checkpoint 与 HITL 恢复**：实现 checkpoint 持久化、任务状态机、租约与审批恢复链路（sqlite 全链；Redis 租约以 fake client 覆盖冲突 / 释放 / 续租 / 缺失降级）。49 个单测覆盖往返、partial 截断、归属校验、损坏快照降级、合法 / 非法状态迁移、审批暂停 / 记账 / 取消旗收敛；2026-10-24 在基线 653715d2 复跑 `49 passed`。【S7】
- **Agent 规划实验（口径受限）**：设计并执行 30 题规划实验（20 L1 + 10 L2，keyword coverage 评分）：结构化模板把 L1 20 题覆盖率从 ~69% 提升到 93.1%，L1 失败 5–6 → 0；全量 30 题 78.7%，L2 50%。另做 5 题人工多步 Plan 对照（同模型）：76% → 92%（+16pp），步数 2.0 → 4.6，耗时 58.3s → 58.9s。明确口径：93.1% 是 L1 20 题的 keyword coverage，不是端到端任务成功率；+16pp 来自另一组 5 题对照，不与 30 题结果混写。【S8】
- **可靠性修复（9 个 PR 已合入 develop）**：#504 登录校验 / 错误提示 + MCP 异常兼容；#503 数据库迁移纳入启动与发布流程（一次性 migrate + schema guard）；#502 bcrypt 校验 / 哈希移入线程池；#506 SDK stdio 运行时（stdout 纯净 / cancel 收敛 / 严格 Redis / 幂等与退出）；#520 ACP cancel 真取消 + stdout 零泄漏；#517 API Key scope 强制（端点读写按 scope 门禁，api-keys 仅 JWT）；#505 Webhook 配置门控 / 限流前置 / 租约 fencing / 复用历史；#515 Commands 契约信封 / Redis 锁与幂等 / 内置命令；#518 Session Fork 路由 / 响应 / 幂等 / seq / history_limit 契约收口。对应 issue #492–#500 均已关闭。【S9】【S10】
- **登录性能测量**：用 Python 并发脚本复现登录路径瓶颈：单进程 8010、50 并发正确密码登录 50×200，p50 12,421 ms、p95 13,432 ms；同刻 `/health` p95 4,692 ms，定位为 bcrypt 同步校验阻塞事件循环。修复 PR #502 已合入（bcrypt 移入线程池）。复测状态：未排期（#502 已合入；无独立 issue），简历不写 0.4s / P99 0.9s。【S5】【S9】

### 1.2 开源贡献

- 14 个 merged PR，覆盖 11 个开源项目（2026-06–2026-10；GitHub 搜索 API 可复现）。只展开 2 个有实质 maintainer review 的案例：
  - **pnpm/pnpm #12284**：`fix(bins.linker): skip redundant .exe warning when hardlink is already correct`；maintainer zkochan 多轮 review 后 approve（PR 共 12 条 review，含 2 次 approve）。【S11】【S12】
  - **storybookjs/storybook #35055**：`UI: Fix args not preserved in isolation mode`；maintainer Sidnioulz 两轮 changes-requested 后 approve（PR 共 7 条 review）。【S11】【S12】

### 1.3 团队协作（待确认区，未确认前不投）

- **MiqroForge-Desktop：241 PR / 188 merged（2026.06–09）** —— 来源仅为 `_scratch/audit/REDTEAM_FINDINGS.md` §2 的团队证据建议。本次核查：GitHub `GET /repos/sijie-Z/MiqroForge-Desktop` 返回 404；`search/repositories?q=MiqroForge+user:sijie-Z` 返回 0 条（可能为私有、改名或未公开）。**性质与公开范围待本人确认；未确认前不得写入投递版简历，也不得当作已确认事实。**【S13】

---

## 2. 目标态跟踪（2026-10-25：已合入项标注 PR / commit；未完成项不得与当前态混写）

| 目标 | 状态 | issue | 写入简历的条件 |
|---|---|---|---|
| 泄露密钥轮换 + gitleaks + SECURITY.md | 已合入止血（PR #524）：gitleaks 全历史扫描 0 命中 + Secret Scan CI + SECURITY.md；**真实密钥轮换仍未执行**（需部署环境，本人执行） | [#507](https://github.com/sijie-Z/DocMind-RAG/issues/507) | 轮换完成并有记录后才可写"已轮换"（issue 已关闭，轮换仍是待办） |
| Eval v1.5：语料扩容 + 80 条 + dense/hybrid 双基线 + rerank live | 已合入（PR #525，squash `552efaad`）：80 条与 dense/hybrid 双基线入库；**rerank live 仍未测量**（占位 `unavailable`）；live embedding 证据见 2026-10-25 报告 | [#512](https://github.com/sijie-Z/DocMind-RAG/issues/512) | 80 条与双基线已入库；live 数字按 `docs/evidence/EVAL_LIVE_2026-10-25.md` 引用，不得写 rerank |
| 工具注入门控 + utility 伞形 + 别名入口归一 | 已合入（PR #534，`2b0574ab`）：默认 10 工具 / 实测 1,464 tokens（≤1,500）；前端面板展示等后续项未完成 | [#509](https://github.com/sijie-Z/DocMind-RAG/issues/509) | 默认 schema 已实测 ≤1,500 tokens；工具数写法受 `docs/METRICS.md` 口径约束 |
| ACL 最小实现（部门 / 组 / doc_acl / retrieval_audit） | 未开始（issue #511 按指令封存） | [#511](https://github.com/sijie-Z/DocMind-RAG/issues/511) | Permission Leak 0/200 等验收通过后才可写 |
| 可观测收敛 + JSONL 调用级记录 | **仅 step1-2 已合入**（PR #533，`5c4aafdf`）：JSONL 调用级记录 + `task_id`/`request_id` 贯通；issue #510 保持 open，删除 / 降级**待逐项批准** | [#510](https://github.com/sijie-Z/DocMind-RAG/issues/510) | 删除 / 降级批准并完成后才可写"收敛完成" |
| Chat 深析 SSE / HITL | 已合入（PR #532，`8df21f32`）：深析入口强制跳转 `/agent`，后端拒绝 legacy useAgent；`/agent` 消费 `?prompt=` 与审批 UI 为后续项 | [#508](https://github.com/sijie-Z/DocMind-RAG/issues/508) | 服务端投影 + 前端审批 UI 完成并验收后才可写 HITL 完成 |
| 指标单一事实源 + facts 门禁（`docs/METRICS.md`） | 已合入（PR #528，`d4ab8411`；PR #531 增补 Eval v1.5 标注） | [#513](https://github.com/sijie-Z/DocMind-RAG/issues/513) | 已满足；对外数字以 `docs/METRICS.md` 为准 |
| Eval 旧快照归档 / 历史口径标注 | 已合入（PR #526，`4618475b`） | [#514](https://github.com/sijie-Z/DocMind-RAG/issues/514) | 已满足 |
| 登录性能复测（#502 之后） | 未排期（无独立 issue） | — | 同 harness 复测后写新 p50/p95；不写 0.4s |
| fork / ACP 并发与延迟复测 | 未排期（#518 / #520 已合入，无独立复测 issue） | — | 复测后写"50 并发同键 → 1"/"cancel 延迟"；当前不写 |
| 120 条 / 4 组基线 / bootstrap / 87% / 0.89 | 未开始 | — | 不写入简历；M2 条件触发后再评估 |

---

## 3. 数字—来源对照表

| 编号 | 事实 / 数字 | 值 | 来源（命令 / 路径） | 状态 | 备注 |
|---|---|---|---|---|---|
| S1 | clause-level golden 条数（v1） | 28（25 正 + 3 负；24 条声明 near-miss） | `cd backend && python -I -c "import json;d=json.load(open('evals/golden_set/clause_set_v1.json',encoding='utf-8'));print(len(d['items']),sum(i['expect']=='hit' for i in d['items']),sum(i['expect']=='empty' for i in d['items']))"` → `28 25 3` | 历史口径（Eval v1） | 数据文件 `backend/evals/golden_set/clause_set_v1.json` |
| S1b | v1.5 clause-level golden 条数 | 80（60 正 + 20 负；60 条声明 near-miss） | `backend/evals/golden_set/clause_set_v1_5.json`（dataset_id `docmind_clause_v1_5`，version 1）；`cd backend && python -m evals check` | 当前态 | 来源 PR #525（squash `552efaad`）；语料 8 文件 / 362 chunks / 354 条 clause 定义 |
| S2 | live 检索指标（2026-10-09，Eval v1） | ClauseHit@1 80%、@3 84%、@5 84%、MRR 0.82、NearMiss@5 12.5%、EmptyAcc 100%、errors 0；ClauseHit@5 95% CI 65.3–93.6%（n=25） | `backend/docs/eval-gate-phase1.md` §5；原始报告 `_orchestrate/round2/_eval208_report_baseline.json`（aggregates 与逐条结果）、`_eval208_report_baseline.md`、`_eval208_run_baseline.txt` | 历史口径（v1；与 v1.5 不可混比） | 环境：dev ES + 本地 Qwen3-Embedding 0.6B，LLM 辅助关闭；与 CI fake 嵌入数字不可混比 |
| S2b | live 检索指标（2026-10-25，Eval v1.5） | ClauseHit@1 75.0%、@3 86.7%、@5 88.3%（95% CI 77.8–94.2%）、NearMiss@5 21.7%、MRR 0.8125、EmptyAcc 100%、errors 0（n=80，60 正 + 20 负） | `docs/evidence/EVAL_LIVE_2026-10-25.md`；原始报告 `_scratch/orchestration/evidence/eval_v1.5_live_ollama_latest.json`（meta：commit `2b0574ab`、ES 8.11.0、`embedding_mode=live`、LLM off） | 当前态 | 环境：本机 Ollama `qwen3-embedding:0.6b`（1024 维）+ hybrid；**live embedding，非 rerank**；与 fake / v1 28 条不可混比 |
| S3 | 7 指标零容差 + Eval Gate | `clause_hit@1/@3/@5`、`mrr`、`empty_accuracy` 为 floor；`near_miss@5`、`errors` 为 cap；tolerance=0 | `backend/evals/baselines/ci_baseline.json`；`backend/evals/runner.py:75-81,701-731`；`.github/workflows/ci-fast.yml` 的 `eval-gate` job 与 `ci-pass.needs` | 当前态 | Eval Gate 在每 PR + main/develop push 跑，接入 `CI Pass`（分支保护必需检查） |
| S4 | Wilson 95% CI | `wilson_ci(successes,total,z=1.96)` | `backend/evals/metrics.py:104` | 当前态 | 小样本一律带 Wilson 区间 |
| S5 | 50 并发登录延迟 | p50 12,421 ms、p95 13,432 ms；50×200；同刻 `/health` p95 4,692 ms | `_scratch/audit/stress_round2.py:72`（`burst("login_50", 50, ...)`）；`_scratch/audit/FINDINGS.md:210`（2026-10-23，8010 单进程） | 当前态（修复前测量） | 根因：bcrypt 同步阻塞；#502 已合入；修复后未复测 |
| S6 | INDEXED⟹可检索 | 3 条不变量 + 8 个集成测试 + 3 个变异验证 | `backend/tests/integration/test_index_verification.py`；`.github/workflows/ci-fast.yml` 的 `integration` job（develop push 与指向 develop 的 PR；不在 `ci-pass.needs`） | 当前态 | 需要真实 Elasticsearch；`RUN_INTEGRATION=1` 门控 |
| S7 | checkpoint 单测 | 49 个 | `backend/tests/unit/test_checkpoint_roundtrip.py`(8) + `test_checkpoint_store.py`(13) + `test_checkpoint_wiring.py`(9) + `test_hitl_checkpoint_flow.py`(19)；2026-10-24 复跑 `49 passed in 40.01s` | 当前态 | 运行命令见 §4；需把 TMP/TEMP 指到项目内 `_scratch` |
| S8 | 30 题规划实验 | L1 20 题 keyword coverage ~69% → 93.1%；ALL 30 78.7%；L2 50%；5 题人工 Plan 对照 76% → 92%（+16pp） | `docs/research/004_structured_planning.md` §验证实验、§3、§4.1；`docs/blog/dag-vs-planning.md` 结果表 | 当前态 | 004 用 glm-4-flash；69% 基线来自 001（模型不同，存在 confound）；+16pp 是同模型 5 题对照 |
| S9 | 9 个修复 PR 已合入 develop | #504/#503/#502/#506/#520/#517/#505/#515/#518；合并日期均 2026-10-24 | `git log origin/develop --format="%h %ad %s" --date=short -9` | 当前态 | squash 提交哈希：f67ef9f8/a80ae306/ee30ae4c/175006de/0ff446e9/00dbeaa7/1d4d4060/963aeaa1/653715d2 |
| S10 | issue 状态 | #492–#500 closed；#507/#508/#509/#512/#513/#514 closed（均已合入 develop）；#510 open（仅 step1-2 合入，删除待批准）；#511 open（封存）；#309 open（dependabot，挂起） | 夜班报告 `_scratch/orchestration/NIGHT_REPORT.md`（2026-10-25）；历史查询 `_scratch/career-gh/issues_all.json`（2026-10-24） | 当前态（2026-10-25） | #507 真实轮换仍未执行；#510 删除清单待本人批准 |
| S11 | 开源 merged PR | 14 个 merged，覆盖 11 个项目 | `curl -H "User-Agent: DocMind-career-research/1.0" "https://api.github.com/search/issues?q=is%3Apr+author%3Asijie-Z+is%3Amerged+-user%3Asijie-Z&per_page=100"` → `total_count=14`（保存于 `_scratch/career-gh/opensource_merged.json`） | 当前态 | 统计口径排除了本人仓库 |
| S12 | 2 个有实质 review 的 OSS PR | pnpm #12284（12 reviews，zkochan 多轮 + approve）；storybook #35055（7 reviews，2× CHANGES_REQUESTED → APPROVED） | `GET /repos/pnpm/pnpm/pulls/12284/reviews`、`GET /repos/storybookjs/storybook/pulls/35055/reviews`（保存于 `_scratch/career-gh/reviews_*.json`） | 当前态 | 不写 redis #15323、click #3556、element-plus |
| S13 | MiqroForge 241 PR / 188 merged | 241 / 188（2026.06–09） | `_scratch/audit/REDTEAM_FINDINGS.md` §2；本次核查 `GET /repos/sijie-Z/MiqroForge-Desktop` → 404，`search/repositories?q=MiqroForge+user:sijie-Z` → 0 条 | 待确认（未核实） | 性质与公开范围待本人确认；未确认前不投 |
| S14 | 工具面现状（内部口径，不上简历） | 39 个注册工具；schema 19,001 chars；默认 36 个约 17,592 chars ≈ 5,026 tok/步 | `docs/facts.json`；`backend/scripts/check_doc_facts.py --print-derived`；schema 测量见 `_scratch/audit/STRATEGY.md` §4.2 / `REDTEAM_FINDINGS.md` B3 | 内部参考 | 只在面试 Q9 讲技术债，不作简历亮点 |
| S15 | 多租户 / 缓存现状（内部口径） | 23 个 RBAC 权限点；检索缓存键 = org + topk + mode + q（不含 user_id） | `backend/app/models/rbac.py:24-92`；`backend/app/rag/cache.py:330-338` | 内部参考 | 外部 ACL 缺失，见 #511 |

---

## 4. 本地复跑记录（2026-10-24）

### 4.1 golden set 计数

```bash
cd backend
python -I -c "import json;d=json.load(open('evals/golden_set/clause_set_v1.json',encoding='utf-8'));print(len(d['items']),sum(i['expect']=='hit' for i in d['items']),sum(i['expect']=='empty' for i in d['items']))"
# 输出：28 25 3
```

### 4.2 checkpoint 49 个单测

```bash
cd backend
mkdir -p <项目内>/_scratch/pytest-career-20261024/tmp <项目内>/_scratch/pytest-career-20261024/base
PYTHONDONTWRITEBYTECODE=1 \
TMP="<项目内>/_scratch/pytest-career-20261024/tmp" \
TEMP="<项目内>/_scratch/pytest-career-20261024/tmp" \
<Windows venv>/python.exe -m pytest \
  tests/unit/test_checkpoint_roundtrip.py \
  tests/unit/test_checkpoint_store.py \
  tests/unit/test_checkpoint_wiring.py \
  tests/unit/test_hitl_checkpoint_flow.py \
  -q -p no:cacheprovider \
  --basetemp="<项目内>/_scratch/pytest-career-20261024/base"
# 输出：49 passed, 6 warnings in 40.01s
```

> 本机默认临时目录 ACL 异常时，pytest 的 `tmp_path` fixture 会 ERROR；把 TMP/TEMP 指到项目内 `_scratch` 后 49 个用例全绿。这是本机复现注意点，不是测试失败。

### 4.3 9 个修复 PR

```bash
git log origin/develop --format="%h %ad %s" --date=short -9
# 653715d2 2026-10-24 feat(agent): Session Fork 契约收口 ... (#499) (#518)
# 963aeaa1 2026-10-24 feat(commands): 契约信封/Redis 锁与幂等/内置命令收口 (#498) (#515)
# 1d4d4060 2026-10-24 fix(webhook): 运行时加固 ... (#497) (#505)
# 00dbeaa7 2026-10-24 feat(auth): API Key scope 强制 ... (#496) (#517)
# 0ff446e9 2026-10-24 fix(acp): cancel 真取消 + stdout 零泄漏 (#494) (#520)
# 175006de 2026-10-24 fix(sdk): stdio 运行时收口 ... (#493) (#506)
# ee30ae4c 2026-10-24 fix(auth): bcrypt 校验/哈希移入线程池 ... (#500) (#502)
# a80ae306 2026-10-24 feat(deploy): 数据库迁移纳入启动与发布流程 ... (#495) (#503)
# f67ef9f8 2026-10-24 fix(ux): 登录校验与错误提示收敛 + MCP 异常类兼容 (#492) (#504)
```

### 4.4 开源贡献查询

```bash
curl -sS -H "User-Agent: DocMind-career-research/1.0" \
  "https://api.github.com/search/issues?q=is%3Apr+author%3Asijie-Z+is%3Amerged+-user%3Asijie-Z&per_page=100"
# total_count = 14；11 个 distinct repo
# 2 个展开案例的 review 证据：
#   GET /repos/pnpm/pnpm/pulls/12284/reviews
#   GET /repos/storybookjs/storybook/pulls/35055/reviews
```

---

## 5. 禁写清单（硬约束）

| 禁写表述 | 原因 / 当前真实状态 | 何时可写 |
|---|---|---|
| 120 条 eval / 4 组基线 / bootstrap / 87% / 0.89 | 当前只有 28 条、1 个 fake 基线、Wilson；#512 只承诺 80 条 + 2 组基线 + rerank live；120 条是 M2 条件项 | 数据与报告入库后，按实际数字重写 |
| fork 50 并发同键 → 1 | 修复前实测 5 并发建 5 个、10 并发建 10 个；#518 已合入契约 / 幂等，但没有 50 并发复测 | 复测并记录后才可写 |
| ACP cancel < 2s | 无任何产物；#520 已合入 cancel 真取消，但延迟未测 | 有复测报告后 |
| Locust / 火焰图 | 未使用；压测是 Python urllib 脚本；PR #501 closed 未合并 | 引入工具并产出报告后 |
| 3 次回归 | 无 3 次回归记录 | 有记录后 |
| 密钥轮换 | 未轮换；公开历史密钥与当前 `.env` 一致；#507 open | 轮换 + gitleaks + 记录完成（issue #507） |
| 0.4s p50 / P99 0.9s | PR #501 closed；无新 benchmark | 同 harness 复测后 |
| 2223 passed / 51 skipped | README 2026-10-15 冻结快照，早于本轮合并 | 用当前 CI / 复跑数字替换 |
| "CI 全绿" / "8 项全部完成" | 代码合并 ≠ 能力可用；以具体 issue / PR 状态为准 | 每项有验收证据后 |
| 精通 / 企业级 / 高并发 / 丰富 / 深度 | 无证据形容词 | 永不 |
| "39 个工具"作为简历亮点 | 功能通胀；schema 19,001 chars；默认 36 个约 5,026 tok/步；#509 才做门控 | 工具收敛并实测 schema 后 |
| redis #15323 / click #3556 / element-plus | 自关 / AI spam / 全关 | 永不 |

---

## 6. 仓库内风险点

- `docs/career/README.md`（2026-06-21 旧稿）：含 "33/39 工具"、"69%→93.1%（+24pp）"、"规划质量比执行架构重要 24 倍"、"5 轮实验" 等口径；与当前证据不一致（+24pp 是 004 vs 001 的另一个对比，且 001/004 模型不同；+16pp 才是 5 题人工 Plan 对照；"24 倍"无来源）。**不要提交该文件；建议移入归档或由本人确认后再改。**
- `backend/eval_first_report.json`：git-tracked 的 10 场景 0 passed / 0.0（2026-06-15），面试官 clone 即见；issue #514 跟踪归档 / 说明（删除需用户批准）。
- README 的 "2223 passed / 51 skipped" 是 2026-10-15 冻结快照，早于本轮合并，不能当作当前测试数。
- 旧稿 / README 的 "8 项 harness 全部完成" 不要写；写"代码已合并 + 具体 issue / PR 修复"。
- 开源风险点：redis #15323（自关）、click #3556（AI spam 关闭）、element-plus 系列（全关）不得作为贡献案例；只讲 pnpm #12284 与 storybook #35055。

---

## 7. 投递前检查清单

- [ ] 全文搜索禁写词：`120 条`、`4 组基线`、`bootstrap`、`87%`、`0.89`、`50 并发→1`、`ACP<2s`、`Locust`、`火焰图`、`3 次回归`、`密钥轮换`（除非在"进行中（issue #507）"上下文中）
- [ ] 全文搜索无证据形容词：`精通`、`企业级`、`高并发`、`丰富`、`深度`
- [ ] MiqroForge 行已由本人确认性质与公开范围；未确认则删除
- [ ] `docs/career/README.md` 旧稿未被提交 / 引用
- [ ] 每个数字能指回 §3 的来源
- [ ] §2 状态与 issue / PR 实际状态一致（已合入行须有 PR / commit 证据；未完成行标"待批准 / 封存 / 未排期"）
