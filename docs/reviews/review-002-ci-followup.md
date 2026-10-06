# review-002：对 review-001 答复的核实结果与新一轮提问

> 这是 review-001（CI 投入策略）收到外部答复后的**反馈轮**。
> 上一轮答复质量很高、方向基本采纳，但其中有若干条**与代码现状不符**（因为答复方看不到实际代码）。
> 本文档逐条给出核实结果，并提出新的决策点。

---

## 1. 背景更新

review-001 发出后收到一份详细答复，已执行其中大部分建议。本文档有三件事要说：

1. **答复方的若干判断与代码现状不符** —— 逐条核实（§2）
2. **一个关键根因发现，与答复方的假设不同** —— nightly 为什么红（§3）
3. **新的决策点**（§4）

项目约束重申：单人开发、**public repo**（Actions 免费，这点答复方纠正得对）、求职作品、长期可维护性优先。

---

## 2. 对上一轮答复的逐条核实

### 2.1 答复方**正确**、我已采纳的

| 答复方意见 | 状态 |
|---|---|
| public repo → Actions 免费，应优化墙钟/排队/噪音而非省 minutes | ✅ **采纳**。我原文档把"免费额度紧张"当约束写进去了，是错的 |
| P0：覆盖率 floor | ✅ **已做**（`fail_under = 42`），并做了正负两向验证（27.87% → 失败退出 1） |
| P0：Bandit 前置到 PR | ✅ **已做**，且**立刻兑现价值** —— 抓到 `retriever.py` 用 SHA1 生成 chunk_id 的 HIGH 告警（是我自己上一轮引入的） |
| `paths:` 对 `schedule` 触发无效 | ✅ **属实，我原方案错误**。已改为：Docker Build 独立成 path 触发的 workflow |
| Docker Build 该从 nightly 拆出 | ✅ **采纳**（429s 占 nightly 墙钟 99%） |
| E2E 应后置 | ✅ **采纳，说服了我**。理由见 §2.3 |
| pipeline 不要堆成"单人维护灾难"、要停止扩张 | ✅ **原则采纳** |

### 2.2 答复方**与代码现状不符**的（重要，请答复方知悉）

| 答复方建议 | 实际情况 |
|---|---|
| "建议加 `concurrency + cancel-in-progress`" | ❌ **早已存在**。`ci-fast.yml:7-9` 就有 `concurrency: group: ci-fast-${{ github.ref }}` + `cancel-in-progress: true`。答复方基于我文档的描述提建议，而我文档没写这条已存在 |
| "确认 Secret Scanning 是否启用" | ❌ **早已启用**，而且 `secret_scanning_push_protection` 也开着（API 读回：`secret_scanning: enabled`） |
| "Dependabot 30+ 分支是负担，该加 grouping/limit/weekly" | ❌ **两处都错**：① 远端实际只有 **10 个**分支（我文档里的"30+"数的是本地陈旧 refs）；② `dependabot.yml` **早已配好** `target-branch: develop`、weekly、`open-pull-requests-limit: 5`、minor+patch 已分组 |
| "把 Bandit 从 nightly 挪到 PR"（暗示 nightly 有 Bandit 但没生效） | ⚠️ **部分正确**：nightly 确实有 bandit，但它**从来没跑到过** —— job 在更早的步骤就死了（见 §3） |

**方法论备注**：这些偏差不是答复方的问题 —— 它只能看到我写的 review-001，而那份文档**没写"哪些已经做了"**。这是我的疏漏。review-002 之后附上了实际状态清单。

### 2.3 一处我接受但想说明细节的采纳

答复方说 E2E 暂缓、优先救活 155 个集成测试。**我接受这个判断**，理由是它的论证成立：

> 3 条 UI smoke（登录/跳转/表单校验）碰不到项目的真正复杂度（多租户、RAG、Agent、权限边界）；而"155 个测试存在于仓库但从未运行"是更大的可信度缺口。

补充细节：E2E 的脚手架**没有丢弃**，已完整保留在分支上（含侦察成果：登录/用户信息接口形状、token 存储位置、路由守卫逻辑、以及任务书没预料到的 `/user/settings` 打桩需求）。等集成环境就绪后，E2E 可以升级成真正的端到端（登录 → 上传 → RAG 检索 → 权限），那时价值大得多。

---

## 3. 关键发现：nightly 的根因与答复方的假设不同

答复方建议"修 nightly 的 MySQL integration"。我按"先核实再动手"的规矩查了失败日志原文，**根因不是 migration 代码坏了**：

```
main 落后 develop 95 个提交
  ↓
main 的 requirements.txt 仍是 sqlalchemy>=2.0.23（缺 [asyncio] extra）
  ↓
greenlet 不会被安装
  ↓
alembic/env.py:6  from sqlalchemy.ext.asyncio import async_engine_from_config
  ↓  ImportError: The SQLAlchemy asyncio module requires that the Python 'greenlet' library is installed
  ↓
job 在第一步就死 → 后续 155 个集成测试全部 skip
```

**两条决定性证据**：

1. 失败日志里是 `ModuleNotFoundError: No module named 'greenlet'`，**不是** migration 的 SQL/schema 错误；
2. **同一段 migration 代码在 PR 门禁（`ci-fast.yml` 的 `migration-mysql` job）里一直是绿的** —— 因为它 checkout 的是 PR 分支，那个分支有修复。

**真正的结构性问题是 `schedule` 事件只在默认分支（main）上触发**，而 Git Flow 下 main 只在发版时更新 —— 所以 nightly 天天在测一个陈旧快照。

**修法**（已实施）：nightly 里 `actions/checkout` 显式 `ref: develop`，让夜间集成测试真正验证"即将发布的代码"。**不需要改 migration 逻辑。**

---

## 4. 新的决策点（请答复）

### 问题 1：nightly 该测哪个分支？

我的选择：**测 `develop`**。理由：active development 在 develop，main 是发版快照；测陈旧 main 只会产生噪音。

反问：有没有理由坚持测 main（例如"夜间应该验证生产状态的健康度"）？如果两者都要，是否值得开两条 nightly（成本是 155 个测试跑两遍 + 两套容器）？

### 问题 2：`main` 落后 develop 95 个提交 —— 要不要做一次 release？

Git Flow 下 main 只在 release 时更新，所以落后是正常的。但**长期不 release 的后果**是：
- main 变成"死分支"，上面的 CI 全是噪音
- 任何指向 main 的定时任务（如 nightly）都在测陈旧代码
- 对求职作品而言，访客默认看到的是 main（陈旧的那份）

是否应该建立一个轻量的 release 节奏？如果是，单人项目的合理频率是什么？

### 问题 3：覆盖率门禁是否该只在一条 matrix 腿上生效？

答复方指出：`backend-tests` 是 3.11/3.12 两条腿，如果两条都跑 `--cov` 且都应用 `fail_under`，某个版本差 0.2 个百分点就可能莫名卡住 PR。

**我核实了现状**：`fail_under` 写在 `pyproject.toml` 里，所以**两条腿都会应用**。答复方的担忧成立。

三个选项：
- **A**：只在一条腿跑 `--cov`（另一条只跑测试不加 `--cov`）
- **B**：两条都跑，接受偶发的版本差异导致失败
- **C**：两条都跑但只有一条有 `fail_under`（用环境变量或命令行 `--cov-fail-under` 覆盖）

我倾向 **C**：两条腿都采集覆盖率（便于对比），但只有 3.12 那条是门禁。理由：3.12 是主版本，且避免"两个 gate"造成的假阳性。

### 问题 4：Dependency Review 对 public repo 的正确配置？

答复方提到了 GitHub 原生的 Dependency Review。我确认它会加，但想确认几个细节：
- 它需要 `pull-requests: write` 吗？还是只读即可？
- 对**新引入**的依赖和**已存在**的漏洞，它的行为分别是什么？
- 是否应该设为 required check？会不会因为上游新披露的漏洞突然卡住所有 PR？

### 问题 5：已经做了的"低优先级"项要不要回退？

答复方说 commitlint / PR 自动打标签"现在不要碰"。但**在做这份评审之前我已经做了**（它们在 `ci-hygiene.yml`，是独立并行 job，实测墙钟贡献 0 秒：Commit Lint 17s、PR Labels 5s、Conflict Markers 4s，均低于关键路径 133s）。

反问：既然已经落地且边际成本为零，**回退它们是净收益还是净损失**？我倾向保留（沉没成本已付、维护面很小），但如果答复方认为"多一个 workflow 就多一分噪音"，我可以拆掉。

同时补一条我自己踩的坑，供答复方参考：我加的 `conflict-markers` job **首版是个永远绿的摆设** —— 它用三点 diff，而 Actions 的 PR checkout 是浅克隆 + merge commit，git 求不出 merge-base 直接 fatal，**而 fatal 被管道里 grep 的退出码掩盖了**。是内部对抗评审抓出来的。教训：**新加检查必须构造"它应该失败"的场景实测一次；永远绿的检查比没有检查更糟。**

### 问题 6：CI 的"完成标准"该怎么定？

答复方给了一个 7 条的 Done Criteria。我想把它落成仓库里的一份可执行文档（例如 `docs/ci-charter.md`），写明"达到以下条件后停止扩张 CI"。请评估：这样一份文档对**单人项目**是资产还是形式主义？如果要写，应该包含哪些条目？

---

## 5. 当前状态清单（供答复方参考实际基线）

**PR 门禁（`ci-fast.yml`）实测**：墙钟约 133–171 秒（波动来自 runner 排队），关键路径是 Backend Tests 3.12 的 ~124–133 秒。

| 检查 | 耗时 | 状态 |
|---|---|---|
| Backend Tests (3.11 / 3.12) | 123 / 124 s | 绿 |
| Migration (MySQL) | 94 s | 绿 |
| Frontend Tests（eslint + vue-tsc + vitest + vite build） | 51–78 s | 绿 |
| Workflow Lint（actionlint） | 5 s | 绿 |
| **coverage `fail_under=42`** | +0 s | 新增，正负验证过 |
| **Bandit（medium+）** | +3–5 s | 新增，已抓到 1 个 HIGH 并修复 |
| **Commit Lint** | 17 s | 新增（并行 workflow） |
| **PR Labels / Conflict Markers** | 5 s / 4 s | 新增（并行） |
| CodeQL（python + js/ts） | 49 / 58 s | 绿 |

**已启用的平台原生能力**：Secret Scanning ✅、Push Protection ✅、Dependabot 告警 ✅（本轮新开）、Dependabot 安全更新 ✅（本轮新开）。

**Nightly**：修复中（checkout 改 develop + Docker Build 拆分）。

**覆盖率**：44.10%（unit + behavior + 两个集成文件，16737 行中 9356 行未覆盖）。**注意**：另有 155 个集成测试从未运行过，它们跑起来后真实基线会上升 —— 这也是我说"现在还不是抬覆盖率门槛的时候"的理由。

---

## 6. 我在 review-001 里写错的地方（自我更正）

1. **"免费 CI 额度紧张"** —— 错。public repo 的 Actions 免费。正确的优化目标是墙钟、排队、噪音、维护成本。
2. **"Dependabot 30+ 分支是负担"** —— 数字错，实际 10 个；且配置早已合理。
3. **"给 nightly 的 Docker Build 加 `paths:` 过滤"** —— 技术上错，`paths:` 对 `schedule` 无效。

这三条都是我在写文档时**没有先核实就断言**，正好违反了我自己定的规矩。记在这里，供答复方评估我的信息可信度时参考。

---

## 7. 我的不确定之处（欢迎直接指出）

- **"停止扩张 CI"的边界在哪？** 我已经有了 5 个 workflow（ci-fast / ci-hygiene / ci-nightly / codeql / 待加的 dependency-review）。对单人项目，这个数量是合理还是已经开始臃肿？
- **求职视角**：面试官看到 `.github/workflows/` 下 5 个文件，会觉得"工程纪律好"还是"过度工程"？我不确定分界线在哪。
- **nightly 测 develop 之后**，如果 develop 因为某个 WIP 提交而红，nightly 会一直红到那个提交被修 —— 这会不会让 nightly 重新变成"长期红的背景噪音"？有没有更好的机制（例如只在 develop 的提交通过 PR 门禁后才跑）？
