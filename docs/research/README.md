# 研究记录（research）

本目录存放**编号的研究与实验记录**：一次具体的调查、实验或审计，
回答「当时发现了什么、依据是什么」。设计决策类文档在 `../decisions/`。

## 命名约定

`NNN_topic.md`，三位编号，编号不复用。

## 现有文档

| 文件 | 内容 |
|---|---|
| `001_dag_vs_serial.md` | DAG vs 串行执行模式的对照实验 |
| `002_planner_granularity.md` | Planner 粒度实验 |
| `003_planner_compliance.md` | Planner 可控性（粒度操纵） |
| `004_structured_planning.md` | 规则化任务分解 |
| `006_failure_taxonomy.md` | 结构化规划后的失败分类 |
| `007_retrieval_ceiling.md` | 压缩 oracle 下的检索上限 |
| `008_rag-never-indexed.md` | **活体实测：RAG 从未成功索引过任何文档**（ES 0 篇 / chunk 全无向量 / 上传全部失败于 embedding 余额） |
| `009_rag-pipeline-audit.md` | 检索链路端到端审计，22 条缺陷（P0×4 / P1×6 / P2×12），逐条带 `file:line` |
| `010_agent-core-audit.md` | Agent 核心审计：PER 循环 / 工具 / 上下文 / 记忆 / 子代理 / 沙箱 / 可观测性现状与差距 |
| `011_reference-rag-failure.md` | 对标产品一次检索失败的六环因果链（含 7 个可独立执行的最小实验） |
| `012_reference-rag-architecture.md` | 对标产品的完整架构拆解（含其评估契约与门禁原文） |
| `013_agent-harness-landscape.md` | Agent harness 版图：形态（桌面/CLI/Web）与内核的区分，及"一个内核多种入口"的产业证据 |
| `014_agent-metrics-spec.md` | 指标规格：最小可行指标集、公式、采集点、阈值 |
| `015_deerflow-architecture.md` | DeerFlow 2.0 拆解（中间件链、Agent Teams、沙箱矩阵、扩展契约） |

## 关于证据副本

`008`–`015` 中的若干结论基于**本地下载的第三方仓库快照与网页缓存**。
那些快照**没有入仓**（体积大、且可重新获取），因此**照着文档复现时需要重新下载**。
文档里凡引用到具体文件路径的，路径都是**对方仓库内的相对路径**，可直接在其仓库中定位。
