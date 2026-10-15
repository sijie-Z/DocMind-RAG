# DocMind CI 章程

> 这份文件定义 CI 的边界：有哪些门禁、各自管什么、什么时候该加、什么时候该删。**改 CI 之前先读它。**
> 最后更新：2026-10-09（第 5 轮：#208 第⑤步 Eval Gate 检索评估门禁）

## 1. CI 的目标

用尽量短的墙钟时间，在合并前拦住真实回归；拦不住的部分必须在 nightly / 每周例行中**真实执行**——挂在页面上当绿装饰的检查等于不存在。

## 2. 门禁全景（PR 门禁 + 其它 workflow 的职责）

`ci-fast.yml`（PR → main/develop；push 也触发，develop push 上额外跑 integration，见 §3）：
- **Backend Tests**（3.11 + 3.12 矩阵）：ruff（app/tests/scripts/**evals**）→ bandit(medium+ 阻断) → SQLite 迁移冒烟 → 快速测试集（unit + behavior + 2 组安全回归；仅 3.12 腿带覆盖率门禁）
- **Migration (MySQL)**：真实 MySQL 上 alembic upgrade/downgrade/upgrade 往返 + 生产路径 create_all
- **Eval Gate**（#208 第⑤步）：真实 ES 上跑检索评估门禁——专用评估索引先删后建 + 确定性伪嵌入 + 与入库基线比对（floor/cap，容差 0）；非零退出 = 红。口径与维护纪律见 `backend/docs/eval-gate-phase1.md` §6
- **Frontend Tests**：eslint → vue-tsc → vitest(+coverage) → build
- **Workflow Lint**：actionlint 校验 workflow 文件自身
- **CI Pass**：上述无条件 jobs 的聚合门禁（把并行结果收敛成单一结论；eval-gate 是无条件 job，直接进汇聚——红点必须有人消费，见 §8 孤儿检查教训）

`ci-hygiene.yml`（仅 PR）：commitlint（只查本 PR 新增提交）、PR labeler、conflict-markers。
`dependency-review.yml`（仅 PR）：只审查本 PR **新增**依赖的已知漏洞，high+ 阻断；**scope 含 runtime + development**（默认只含 runtime，而本仓库 4/6 个 high 源自 devDependency）。
`codeql.yml`：push / PR 都跑（security-extended，python + js/ts），周一 schedule 补扫。
`docker-build.yml`：仅当容器相关文件（Dockerfile / requirements / compose / deploy…）变化时才构建镜像。
`ci-nightly.yml`（每周一 02:00 UTC + 手动）：backend integration 的**环境漂移检查**（代码没变时上游依赖 / runner 镜像变化也能被发现）+ bandit / 依赖审计。

## 3. integration 测什么、什么时候跑

integration 是 `ci-fast.yml` 里的 job：在 **push 到 develop** 与 **PR → develop** 时运行（issue #176 起 PR 也跑集成用例，避免集成测试改动在合并前从未执行），且需 backend-tests / migration-mysql 通过（真实 MySQL + Redis + ES，Python 3.12）。
语义：integration 验证「一个已通过快速门禁的 develop 提交」；指向 main 的 PR 不触发它。**develop 的绿点包含 integration，PR 的绿点不需要。**

Eval Gate 的触发面比 integration 宽：**每个 PR / 每次 push 都跑**（无条件、无 needs、与后端测试并行起步，不进关键路径）。理由：它是「拦住任何一次合入」的评估门禁，必须进 CI Pass 才不是孤儿检查（§8 教训）；只跑到 develop 就失去了 PR 阶段的拦截。为控制墙钟，它不等待其它 job、只装 runtime 依赖、跑 <2 分钟。

## 4. main / develop 的语义

- **develop**：集成分支。合入 = 已过 PR 门禁 + integration 真实运行过；判断「链路是否通」一律以 develop 为准。
- **main**：发版分支，只在发版时更新。main 上的绿不代表最新代码，只代表某个发布快照（nightly 因此显式 checkout develop）。

## 5. 明确不做的事

- **E2E（真实浏览器 / Playwright）**：单人项目维护不起；前端由 vitest + vue-tsc + build 兜底。
- **mypy**：全量整改成本 > 收益；后端回归靠测试覆盖，不靠类型检查。
- **license gate**：没有许可证策略；dependency-review 的 license-check 保持关闭。
- **没有对应回归类型的「专业摆设」job**：见 §11，先回答三问再动手。

## 6. 时间 / 墙钟预算

硬上限（timeout-minutes，超时 = 失败）：backend 20 / migration 15 / eval 15 / frontend 20 / workflow-lint 5 / integration 30 / docker 30 / codeql 30。
已做的瘦身：integration 只留 3.12 一条腿（省 ~95s/次合并）；docker 构建移出 nightly（曾占 nightly 墙钟 429s/432s）改为路径触发；nightly 从每天改为每周一。
原则：**快速门禁的墙钟是稀缺资源——加检查前先想清楚它替代谁、以及会不会把发现问题的时刻推得更晚。**

## 7. Coverage 策略

- 唯一覆盖率高门禁：`--cov-fail-under=42`，挂在后端**快速测试集那条命令**上，只对 3.12 腿生效。
- 下限写在使用点而非 pyproject 全局段：integration 只跑 `tests/integration/`（约 32%），套用 42% 会把一次 179 个测试全过的运行报成失败——已真实发生过，别改回去。
- 42 是棘轮下限：留约 2 个点余量防环境波动，只拦「继续下滑」，不追高分。
- Codecov 是仪表盘不是门禁（`fail_ci_if_error: false`）：上传失败不阻断。

## 8. Security 策略（工具职责边界）

| 工具 | 管什么 | 何时 | 阻断? |
|---|---|---|---|
| bandit (medium+) | 代码内安全问题 | PR + nightly | 是 |
| bandit (low) | 同上，低信噪比 | nightly | 仅报告 |
| CodeQL（security-extended） | 语义级漏洞（数据流） | PR / push + 周一 | 是 |
| Dependency Review | 本 PR **新增**依赖的已知漏洞（scope: runtime + development） | PR | high+ 阻断 |
| ^ 例外 | 当前仅一条：`braces` 的 DoS（最新版即受影响版，官方无修复，且仅在构建期使用）。豁免必须在配置里写明理由。**能换包就换包** —— `markdown-it-katex` 的 XSS 就是靠换用`@vscode/markdown-it-katex` 从根上消除的，不是靠豁免。 | — | — |
| Dependabot | 存量依赖更新与漏洞 | 持续 | 走它自己的 PR |
| pip-audit / npm audit | 存量依赖树审计 | nightly | 仅报告（advisory） |
| conflict-markers / commitlint | 冲突标记、提交信息 | PR | 是 |

孤儿检查没人看 = 没有检查。真实教训：bandit 曾只在 nightly 跑，而 nightly 已连续红 ≥11 天（见 ci-fast.yml 注释；经 Actions API 核实为连续 59 次 failure），这条门禁事实上不存在——存量代码里的问题一直存在，只因 nightly 长期红而被淹没；直到 bandit 前置到 PR，`rag/retriever.py` 的 HIGH 级告警（B324：SHA1 生成 chunk_id）才暴露出来，修复（`usedforsecurity=False` + 注释）与门禁在同一次提交（9e4ba1a）落地。**阻断级扫描必须挂在每天都会触发的路径上。**

## 9. Failure semantics

- **fail-closed 是默认**：工具崩溃 / 报告解析失败 = 红，不是绿。pip-audit 用「报告结构能否校验」判定而不是退出码（2026-09-20 教训：把「真扫出漏洞」误判成「工具失败」）。
- **advisory 必须自报家门**：仅报告的 job 名带 "audits advisory"、summary 写 "STATUS: ADVISORY ONLY"；绿色对勾 ≠ 没有漏洞。
- **超时即失败**（除汇总用的 `ci-pass` 外，每个 job 都设了 timeout-minutes）；**同分支新 push 取消旧 run**（nightly 例外，跑完便于比对历史）。

## 10. 每个检查都必须验证 fail-closed（全篇最重要）

> 新增 CI 检查时，至少构造一次「预期失败场景」和一次「工具异常场景」，证明它不会在检查失效时错误地返回 green。
> **一个永远绿的检查，比没有检查更危险。**

真实案例：conflict-markers 的首版用三点 diff（`A...B`），而 Actions 的 PR checkout 是浅克隆 + merge 提交，git 求不出 merge-base 直接 fatal——**而 fatal 被管道里 `grep` 的退出码掩盖成了绿**。内部对抗评审用沙箱实验抓出后才修正（现为两点 diff + diff 先单独落盘、失败立即红，再对落盘文件 grep）。

## 11. 什么时候允许新增 CI

仅在出现「现有 CI 无法发现的**真实回归类型**」时。新增前三问：① 这个回归真实发生过（或高概率）吗？② 哪个现有 job 覆盖不了它？③ 失败场景演示过了吗（§10）？

## 12. 什么时候必须删除 / 收缩 CI

- 连续 30 天没红过、**也没拦下过任何东西**（先区分「没红」与「没跑」）。
- 它发现的问题已有更早的门禁覆盖（重复只增加墙钟）。
- 它长期红 / 跳过且无人修（orphan gate——nightly 的教训）。
- 它拦的是「仅报告」性质、修复受上游制约的问题（降级为 advisory，保留可观测性）。

## 13. 停止扩张的达成条件

以下条件全部满足后，不再主动增加 CI 检查：PR 快速门禁 ≤ 3 分钟；后端 / 前端 / migration 测试可靠；coverage 有稳定 floor；Bandit + CodeQL 有效执行；Dependency Review 有效执行；develop 的 integration 真实运行；所有检查均通过「失败场景验证」。
之后只有在出现「现有 CI 无法发现的真实回归类型」时，才允许新增（回到 §11）。
