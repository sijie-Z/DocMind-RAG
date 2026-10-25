# DocMind 指标事实源（Single Source of Metrics）

> **规则**：任何对外数字必须能链到本表的一行；每行必须带 **值 · 口径 · 命令 · 日期 · 备注**。没有这五样的数字不写。
>
> **更新顺序**：先跑命令 → 改本表 → 改引用文档 → `cd backend && python scripts/check_doc_facts.py`（逐项校验七项 facts 与 `docs/facts.json` 同值）。
>
> **口径纪律**：`live`（真实嵌入 / 真实 ES / 真实模型）与 `fake`（确定性伪嵌入）不可混写；含嵌套 / 不含嵌套必须写明；旧版本（Eval v1：28 条）与新版本（Eval v1.5：80 条）不可混比。

维护：DocMind 维护者（issue #513）。最近复核：2026-10-25。

## 1. 源码派生 facts（机器校验，七项与 docs/facts.json 同值）

| 指标 | 值 | 口径 | 命令 | 日期 | 备注 |
|---|---|---|---|---|---|
| registered_tools | 31 | live：未隐藏（`hidden=False`）的 `@register_tool(name=...)`（`core_tools.py` 11 + `tools/` 20；不含 MCP 动态工具与未接线模块；issue #509 合并/降级的旧名以 hidden 保留注册，仍可经别名调用但不计入本口径） | `cd backend && python scripts/check_doc_facts.py --print-derived` | 2026-10-25 | `docs/facts.json` 同值；源码 `backend/app/agent/` |
| default_tools | 10 | live：**默认 AgentConfig + 默认环境**（`TERMINAL_ENABLED=false`、未配置飞书凭据、MCP 未连接）下实际注入 schema 的工具数（非 hidden、未声明 `enabled_predicate`、且不在 `DEFAULT_DISABLED_TOOLS`；issue #509 目标常驻档 10–12，schema ≤1,500 tokens）。显式开启门控会按族增加：terminal +6 / 飞书 +1 / 委派 +1 / 扩展面按非空 `tool_tags` 解锁 | `cd backend && python scripts/check_doc_facts.py --print-derived` | 2026-10-25 | `docs/facts.json` 同值；运行时 token 实测 1,464（cl100k，2026-10-25） |
| sse_event_types | 24 | live：`EventType = Literal[...]` 闭集（含 agent 事件子集 16；不含 `ThinkingType` 等非事件 Literal） | `cd backend && python scripts/check_doc_facts.py --print-derived` | 2026-10-24 | `docs/facts.json` 同值；源码 `backend/app/agent/events.py` |
| endpoint_modules | 28 | live：`ROUTER_SPECS` 字典键（声明顺序、去重；不含嵌套 router 内部子模块） | `cd backend && python scripts/check_doc_facts.py --print-derived` | 2026-10-24 | `docs/facts.json` 同值；源码 `backend/app/api/v1/router.py` |
| frontend_routes | 34 | live：`routes` 数组 `path:` 字面量（**含 `children` 嵌套**；注释与 redirect 回调内对象不计） | `cd backend && python scripts/check_doc_facts.py --print-derived` | 2026-10-24 | `docs/facts.json` 同值；12 条顶层 + 22 条页面子路由；**29 条具名路由是另一口径** |
| http_route_decorators | 206 | live：`@router.get/post/put/patch/delete/head/options` 装饰器总数（`app/api/v1/endpoints/**`，含子目录） | `cd backend && python scripts/check_doc_facts.py --print-derived` | 2026-10-24 | `docs/facts.json` 同值；不与 `endpoint_modules` 混用 |
| websocket_route_decorators | 3 | live：`@router.websocket` 装饰器数（`app/api/v1/endpoints/**`；与 HTTP 分开计） | `cd backend && python scripts/check_doc_facts.py --print-derived` | 2026-10-24 | `docs/facts.json` 同值 |

## 2. 检索评测（Eval v1.5）

**统一口径**：`mode=fake`（确定性伪嵌入 `hash-bigram-char-v1`，dim=256；只测检索层，不含 rerank / 缓存 / LLM）；日期 2026-10-24；语料 commit `d867a2b3`；语料指纹 `sha256:838fd02f…97d9e`；**来源 PR #525（已合并，squash 552efaad）**。**live 行**（`eval_v1.5_live_embedding`，2026-10-25）为真实嵌入口径，见行内标注与 `docs/evidence/EVAL_LIVE_2026-10-25.md`。

| 指标 | 值 | 口径 | 命令 | 日期 | 备注 |
|---|---|---|---|---|---|
| eval_golden_items | 80（60 正 + 20 负） | fake；检索层，不含 rerank / 缓存 / LLM | `cd backend && python -m evals check` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad）；语料 8 文件 / 362 chunks / 354 条 clause 定义 |
| eval_clause_hit_at_1 | 70.0% | fake；hybrid 检索，n=80 | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad）；与 v1 的 28 条不可混比 |
| eval_clause_hit_at_3 | 76.7% | fake；hybrid 检索，n=80 | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad）；与 v1 的 28 条不可混比 |
| eval_clause_hit_at_5 | 80.0% | fake；hybrid 检索，n=80 | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad）；Wilson 95% CI 68.2–88.2% |
| eval_near_miss_at_5 | 25.0% | fake；cap 指标（越低越好），n=80 | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad）；旧 v1 口径为 16.7%，不可混比 |
| eval_mrr | 0.74 | fake；hybrid 检索，n=80 | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad） |
| eval_empty_accuracy | 100% | fake；负例空结果准确率，n=20 | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad） |
| eval_errors | 0 | fake；评测执行错误数（cap） | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad） |
| eval_gate_result | 7/7 PASS | fake；容差 0（floor 实测 ≥ 基线、cap 实测 ≤ 基线） | `cd backend && make eval-ci` | 2026-10-24 | 来源 PR #525（已合并，squash 552efaad）；基线在该 PR 内重盖为 v1.5 hybrid |
| eval_v1.5_live_embedding | ClauseHit@1 75.0% / @3 86.7% / @5 88.3%；NearMiss@5 21.7%；MRR 0.8125；EmptyAcc 100%；errors 0 | live embedding（Ollama qwen3-embedding:0.6b，1024 维）+ hybrid 检索 + LLM assist 关闭 + ES 8.11.0；n=80（60 正 + 20 负）；只测检索层；**非 rerank** | 见 `docs/evidence/EVAL_LIVE_2026-10-25.md`（`python -m evals seed --recreate --embedding live ...` + `run --embedding live --retriever hybrid ...`；索引 `docmind_knowledge_eval_live_v1`，`--org-id 9901`） | 2026-10-25 | Wilson 95% CI 77.8–94.2%（@5）；commit 2b0574ab；与 fake / v1 28 条不可混比；live 嵌入 ≠ rerank |

### 历史口径（保留以防混写；不得当作当前新数字对外引用）

| 指标 | 值 | 口径 | 命令 | 日期 | 备注 |
|---|---|---|---|---|---|
| eval_v1_ci_baseline | ClauseHit@1 80.0% / @3 84.0% / @5 84.0%；NearMiss@5 16.7%；MRR 0.82；EmptyAcc 100%；errors 0 | fake（v1：28 条 = 25 正 + 3 负；不含 rerank / 缓存 / LLM） | `cd backend && make eval-ci` | 2026-10-09 | develop 当前基线（PR #525 合并前）；与 v1.5 不可混比 |
| eval_live_qwen3 | ClauseHit@1 80.0% / @3 84.0%；MRR 0.82（n=25 正例） | live（Qwen3 真实嵌入 + 真实 ES；检索层） | `cd backend && make eval`（需真实 ES + embedding 服务） | 2026-10-09 | Wilson @1 65.3–93.6%；与 fake 不可混写；旧文档 88%@3/@5 为另一套 live 口径，已作废 |
| planning_benchmark_legacy | 30 题；L1 覆盖率 69%→93.1% | 历史 planning 实验（关键词覆盖率；live 模型，模型版本混杂） | 历史 benchmark 记录见 `docs/career/README.md`（非 CI 门禁） | 2026-06-21 | **不是** clause-level eval；与 Eval v1.5 不可混比；原文保留不删 |

## 3. 测试快照

**统一口径**：2026-10-15 冻结快照；CI 是实时真源，本表只记录该快照。

| 指标 | 值 | 口径 | 命令 | 日期 | 备注 |
|---|---|---|---|---|---|
| backend_tests | 2223 passed / 51 skipped（0 failed） | 冻结快照；全量 `pytest tests/`（含 `tests/unit`、`tests/api`、`tests/integration` 等子目录） | `cd backend && python -m pytest tests/ -q` | 2026-10-15 | CI 是实时真源；本行只记冻结快照 |
| frontend_tests | 861 passed（128 个测试文件） | 冻结快照；`vitest run`（`vue-tsc --noEmit` 0 错误同快照） | `cd frontend && npm run test` | 2026-10-15 | CI 是实时真源；本行只记冻结快照 |
| tests_total | 3084 passed | 快照合计：后端 2223 + 前端 861（只加 passed，51 skipped 不计入） | `cd backend && python -m pytest tests/ -q` + `cd frontend && npm run test`（两行 passed 相加） | 2026-10-15 | 首页 / 架构页引用 3084 时以本行为准 |

## 4. 引用与边界

- 文档引用写法：数字旁标注 `（见 docs/METRICS.md）`；同一文档同一指标只能出现一个数字，漂移由 `backend/scripts/check_doc_facts.py` 校验（多数字先告警，facts 七项不一致则红）。
- 父目录 `AI_projects/CLAUDE.md` 不在本仓库内，其 `25+ tools` 表述无法在本仓库修订；本仓库对外一律以本表 `registered_tools=31` / `default_tools=10`（2026-10-25）为准。
- `docs/USER_GUIDE*.md` 仍描述 develop 当前的 Eval v1（28 条）；Eval v1.5 数字随 PR #525 合并生效，本 issue 不改该两文件。
- 本表不是 CI 实时结果；测试与评测的实时真源是 CI artifact / 报告。
