# review-001：CI 投入策略该怎么排？

> 求助文档。作者是一个人开发的项目维护者，需要外部视角来校准优先级。
> 背景与数字均为实测可核验，非估算。

---

## 1. 背景

项目 **DocMind-RAG**（https://github.com/sijie-Z/DocMind-RAG），一个企业级 RAG + Agent 系统的全栈实现（FastAPI + SQLAlchemy async + MySQL + Redis + Elasticsearch + MinIO + Vue 3）。约 30k 行后端 Python，46 个前端 vue/ts 文件。

**开发条件（关键约束）**：
- **单人开发**，无团队、无预算、无 CI 分钟数购买（用 GitHub 免费额度）
- 目标：既是自用工具，也是**求职作品**（面向大厂 AI Infra / Agent Platform 方向）
- 时间优先级：宁可少做，也不要长时间维护不动的流水线

**项目刚经历一次大修**：一个 PR 修掉了 19 个缺陷（含 1 个跨租户读取、1 个沙箱 RCE 逃逸、13 处"架构写了但运行时是空转的"接线断点），CI 从红转绿。现在处于"基础稳固、该补基础设施"的节点。

---

## 2. 已知事实（全部实测）

### 2.1 当前 CI 结构

三个 workflow：

| Workflow | 触发 | 跑什么 |
|---|---|---|
| `ci-fast.yml` | push 到 main/develop、**所有指向它们的 PR** | backend-tests(3.11+3.12)、migration-mysql、frontend-tests、workflow-lint、ci-pass(汇总门禁) |
| `ci-nightly.yml` | 定时（每天） | backend-integration(真 MySQL+Redis)、docker-build、security scan(bandit + pip-audit + npm audit) |
| `codeql.yml` | PR + 定时 | CodeQL 静态分析（python + js/ts） |

### 2.2 实测耗时（最新一次成功的 CI Fast run）

```
Backend Tests (3.12)     124 s   ← 关键路径
Backend Tests (3.11)     123 s
Migration (MySQL)         94 s
Frontend Tests (22)       51 s   ← 含 eslint + vue-tsc + vitest --coverage + vite build
Workflow Lint              5 s
CI Pass                    3 s
────────────────────────────────
PR 墙钟总计               171 s  （含 runner 排队 36 s）
```

**关键观察**：墙钟由最慢 job 决定。**任何 120 秒内完成的并行 job，对 PR 墙钟的贡献是 0。**

### 2.3 覆盖率现状

- 后端：`unit + behavior + 两个集成文件` 跑出 **44%**（16737 行中 9356 行未覆盖）
- 前端：有 vitest --coverage 采集
- **两边都没有任何阈值门禁**（`codecov.yml` 不存在，`fail_ci_if_error: false`，`pyproject.toml` 无 `fail_under`）

也就是说覆盖率在被采集、被上传，但**掉了也没人管**。

### 2.4 一个刺眼的现状：nightly 已经连续红 ≥11 天

- `Backend Integration` 两个 Python 版本都在 `alembic upgrade head` **第一步就失败**，后面的集成测试被 **skip**——所以那批测试**从来没有真正跑过**
- `Security Scan` job 也失败（pip-audit 报告解析问题）
- `Docker Build` job 是绿的但耗时 **429 秒**（占 nightly 墙钟 7 分 12 秒的绝大部分）

**含义**：所有"放在 nightly 跑"的检查，实际上跑在一条没人看的红线上。这直接导致我最近引入的一个 HIGH 级静态安全问题（SHA1 用于安全用途的告警）没被拦住——bandit 只在 nightly 跑。

### 2.5 仓库的协作规范（写进 CONTRIBUTING）

- 严格 Git Flow：`main`(保护) ← `develop`(集成) ← `feat/* | fix/* | docs/*`
- issue + PR 模式，PR 必须按 `.github/pull_request_template.md` 填写
- 分支保护开启，但**未强制要求 review**（单人项目，自己 approve 自己也不被 GitHub 允许）

### 2.6 前端测试现状

- 有 vitest 单测（9 个 `src/**/__tests__/*.test.ts`）
- **完全没有 E2E**：无 playwright 配置、无 e2e 目录、无依赖
- 前端 CI 已覆盖：eslint → vue-tsc --noEmit → vitest --coverage → vite build

### 2.7 E2E 的可行性（已做技术侦察）

要跑**真实登录**的 E2E，需要：
- MySQL service container（CI 里已有，容器初始化 28 s）
- 后端 uvicorn + 显式设 `ENABLE_DEMO_ACCOUNT=true` 才会种出演示账号
- 前端用已构建的 `dist/` 起 preview

**做不了的**：登录 → 上传 → 检索 全流程。原因：
- 上传主路径走 MinIO
- 检索需要 Elasticsearch + **真实 embedding API key**（CI 里的 key 是字面量 `test-key`，远程 embedding 调用必然失败）

所以 E2E 只能做**纯 UI 冒烟**：未登录跳转登录页 / 登录页渲染与校验 / 登录成功进入首页。

估时：Playwright 装依赖+浏览器冷跑 35-50 s（可缓存到 ~5 s），3 条用例本身 15-25 s。**塞进现有的 frontend-tests job**（复用 node_modules 和已构建的 dist），该 job 从 51 s 变约 100-110 s，**仍低于 Backend Tests 的 124 s 关键路径 → PR 墙钟仍是 171 s**。

### 2.8 一个可以"白捡 40 秒"的优化

`Backend Tests` 里 **pip install 占 47-50 秒**（是最大单步，pip cache 只省了 HTTP 层）。
换成 `uv pip install -r requirements.txt` 预计可省 **35-40 秒**，把关键路径从 124 s 压到约 85 s。

---

## 3. 问题陈述

在**单人维护 + 免费 CI 额度 + 求职作品**这三个约束下，CI 该投入到什么程度？

具体要决定的是：**下一轮该做哪些、放弃哪些、按什么顺序**。

---

## 4. 候选动作（我按自己的判断排了序）

### P0 · 覆盖率设棘轮门禁
- 做法：`[tool.coverage.report] fail_under = 42`（比实测 44% 低 2 点）
- 耗时：**+0 秒**
- 我的理由：现在覆盖率"在被测量但无人管"，等于装饰。设成棘轮下限能挡住继续恶化，成本为零。

### P0 · 把 bandit 从 nightly 前置到 PR
- 做法：在现有 `backend-tests` job 里加一个 step（bandit 已在 dev 依赖里，无需额外安装）
- 耗时：**+3~5 秒**
- 我的理由：实测有效——刚跑一次就抓到一个 HIGH 级告警（我上一个大 PR 引入的 SHA1 用法）。而且 nightly 已红 11 天，等于这条门禁事实上不存在。

### P1 · 修 nightly（让它变绿、且真的有价值）
- 要修：`alembic upgrade head` 在 MySQL 上失败（导致 155 个集成测试从未运行）；给 Docker Build 加 `paths:` 过滤（429 秒的活不该每晚无条件跑）
- 耗时：**不影响 PR 墙钟**
- 我的理由：一条长期红的流水线会让所有"以后挪到 nightly"的决定失效。

### P1 · 装依赖换 uv（省 35-40 秒）
- 耗时：**−35~40 秒**
- 我的理由：白送的预算，而且降低 runner 排队概率。风险是可能与现有 pip cache 策略冲突，需要实测。

### P2 · E2E 冒烟（3 条纯 UI 用例）
- 耗时：**+0 秒墙钟**（塞进现有 job，仍低于关键路径）
- 代价：新增 playwright 依赖与维护面；只能覆盖 UI 外壳，**覆盖不到项目真正的核心（RAG 检索质量、Agent 行为）**

### P2 · hygiene 杂项（commitlint / PR 自动打标签）
- 耗时：**+0 秒**（独立并行 workflow）
- 我的理由：仓库已有严格的 conventional commit 习惯，但没有强制

### P3 · 后端类型检查（mypy / pyright）
- 耗时：**+0 秒墙钟**（并行 job ≤70 s）
- **但真实成本是首轮报错量**：38k 行、无类型注解的 SQLAlchemy 2.0 + FastAPI 代码，首轮通常是三位数条报错，修起来可能几天

### 明确不做的
- pip-audit / npm audit 进 PR 门禁：前者 25 s 且当前有报告解析问题，后者要 60 s，都不值
- Docker build 进 PR：429 秒，绝无可能
- 抬覆盖率到有实际意义的水平（如 60%）：这是另一个数量级的工程，不该混在 CI 工作里

---

## 5. 需要你回答的问题

1. **P0 两项（覆盖率棘轮 + bandit 前置）我准备立刻做，你有异议吗？** 有没有更该优先的、被我漏掉的快速门禁？

2. **nightly 该救还是该砍？** 它现在连续红 ≥11 天，含 155 个从未运行过的集成测试。
   - 选项 A：修好它（alembic 问题 + Docker paths 过滤）
   - 选项 B：砍掉 nightly，把其中有价值的部分（集成测试）改成"PR 打特定标签时才触发"
   - 选项 C：保持现状，先不管
   你推荐哪个？理由是什么？

3. **E2E 值不值得做？** 我的顾虑是：能做的只有 3 条 UI 冒烟（登录/跳转/表单校验），**碰不到项目真正的复杂度**（RAG 检索质量、Agent 多步推理、多租户隔离）。它更像是"简历上有一行 E2E"的装饰，而不是真的能挡住回归。但另一方面它墙钟成本为 0。
   请问：对一个**求职作品**来说，3 条 UI 冒烟的 E2E 是加分项还是噪音？

4. **覆盖率 44% 这个数字该怎么看待？** 它是 unit+behavior+两个集成文件的口径（不含 155 个从未运行的集成测试）。如果那些集成测试能跑起来，覆盖率会显著上升。**在判断"要不要抬覆盖率"之前，是不是应该先把 nightly 修好拿到真实基线？** 还是说 44% 这个数字本身已经说明测试策略有问题？

5. **有没有我完全没想到的、单人项目里性价比更高的 CI 投入？** 比如：
   - PR 大小标签 / 自动化的 issue 关联
   - 依赖自动更新（仓库已有 dependabot，但产生了 30+ 个分支，很多没合并——这是负担还是资产？）
   - 任何"一次配置、长期省事"的基础设施

6. **求职视角**：招聘方看一个候选人的 GitHub 仓库时，**真的会看 CI 配置的深度吗**？还是说 CI 再精致也不如 README 和代码质量重要？如果是后者，我在 CI 上投入的边际收益在哪一点开始变成负的？

---

## 6. 约束清单（不可违反）

- **单人维护**：任何新增基础设施都必须"配置后基本不用管"，需要频繁人工干预的方案直接否决
- **免费 CI 额度**：不能无限制增加 job 数（并发排队已经是现存问题：3.12 那条腿实测排了 36 秒）
- **PR 墙钟不能显著变长**：目前 171 秒是可接受的，若涨到 5 分钟以上会拖慢开发节奏
- **不能引入需要付费服务或密钥的检查**
- **求职作品属性**：配置要经得起面试官追问"你为什么这么设计"，不能是抄来的复杂流水线

---

## 7. 我自己的不确定之处（欢迎直接指出）

- **覆盖率 44% 是不是"测试策略有问题"的信号？** 612 个测试通过但只覆盖 44% 的代码——这可能意味着测试集中在少量核心路径上，也可能意味着大量代码根本没被测过。我目前无法区分这两者。
- **"低覆盖率 + 高测试数"这个组合，在面试官眼里是加分还是减分？** 一个候选人说"我有 612 个测试"，另一个说"我覆盖率 44%"，哪个印象更好？
- **nightly 的红线是否值得花时间修？** 它已经红了 11 天而项目照常推进——这是否本身说明它不重要？
