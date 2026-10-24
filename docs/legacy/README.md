# 历史归档（legacy）

本目录保存**不再代表当前质量**的旧评测 / 审计产物，仅用于历史对照与追溯。
这些文件不被 CI 引用，不参与任何门禁判定，也不得作为现行评测结果对外引用。

---

## eval_first_report_2026-06-15.json

- **原路径**：`backend/eval_first_report.json`（2026-10 通过 `git mv` 迁入本目录，文件内容未做任何改动）。
- **生成时间**：`2026-06-15T16:48:24`（报告内 `timestamp` 字段）。
- **性质**：2026-06 的旧 benchmark 失败快照——`total_scenarios = 10`、`passed = 0`、`failed = 10`、`overall_success_rate = 0.0`，平均耗时 2276.9 ms。按分类：analysis 3、feishu 2、mixed 2、search 2、general 1，全部失败；工具调用错误以 `not_found` 为主。
- **为何保留**：这是「评测跑出来了但没人接门禁，等于没跑」的工程教训证据（复盘见 `docs/roadmap.md` 的 S3 一节与 `docs/decisions/2026-10-06-harness-refactor-plan.md`）。
- **当前口径**：评测已由 **clause-level eval-gate** 取代——28 条 golden set（25 正例 + 3 负例），ClauseHit@1/@3/@5、MRR、EmptyAcc、NearMiss@5、errors 七项指标零容差，逐 PR 在 CI 中 fail-closed 执行（见根 README「质量与门禁」与 `backend/docs/eval-gate-phase1.md`）。
- **重要**：本文件的 0/10 **不代表当前质量**，请勿引用为现行评测结果。

### 历史查询

```bash
# 跟随重命名查看该文件的完整历史
git log --follow --oneline -- docs/legacy/eval_first_report_2026-06-15.json

# 按旧路径查询迁移前的提交
git log --all --oneline -- backend/eval_first_report.json

# 查看某个历史版本的内容
git show <commit>:backend/eval_first_report.json
```

---

## 审计范围（其它旧快照，只读检查，未做移动 / 删除）

审计时间：2026-10-24；基线 `origin/develop` @ `653715d2`（分支 `docs/legacy-artifacts`）。

| 位置 | 发现 | 处置 |
|---|---|---|
| `backend/*.json`（根目录报告） | `eval_first_report.json` 即本目录归档文件；`mcp_servers.json` 是 MCP 服务配置，不是评测报告 | 报告已移动（本目录）；配置不动 |
| `backend/benchmark/reports/*` | `skill_report_20260613_180227.md`：2026-06-13 的技能挖掘报告（47 次回放、13 个模式、2 条技能建议，覆盖率均为 0%）。该目录已在 `.gitignore` 中，但此文件在 ignore 规则之前就已被跟踪 | **保持原位**（历史记录；移动 / 删除需另行批准），在此登记以免被当作当前结果 |
| `docs/reports/*` | 当前分支不存在该目录，无快照 | 无 |
| 根 `README.md` 测试数字 | `2223 passed / 51 skipped` 已明确标注为「冻结快照 2026-10-15」，并声明 CI 为唯一真源 | 无需修改（保留） |
| `docs/career/README.md` | 2026-06-21 求职材料，含 30 题 benchmark、关键词覆盖率评分、33/39 工具等旧口径 | 已加「历史材料」说明并指向 `docs/METRICS.md`（#513）；未删除任何内容 |
| `docs/roadmap.md`、`docs/decisions/2026-10-06-harness-refactor-plan.md` | 历史文档仍引用旧路径 `backend/eval_first_report.json` | 作为历史记录保留原样（路径未改；超出本 issue 允许修改范围） |
| `.gitignore` | 已忽略 `backend/benchmark/reports/`、`backend/benchmark/results/`、`backend/evals/reports/`；根级与 `backend/` 根级的一次性 `*_report.json` 未被忽略 | 已追加 `/*_report.json`、`/backend/*_report.json` 忽略规则；`docs/legacy/` 为人工归档目录，不受影响 |
