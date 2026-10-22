DocMind 有两类观察界面：面向所有用户的工作台首页，以及仅平台超级管理员可见的系统监控。

## 工作台首页（`/dashboard`）

- 普通用户看到个人工作台：开始做事入口（新对话、上传文档、打开 Agent 工作台）、注册引导清单、最近会话、个人统计（对话数、消息数、上传文件、存储使用）、RAG 检索效果与 7 天活动趋势。
- 平台超级管理员看到管理仪表盘：用户与会话 KPI、系统状态与 Elasticsearch 健康、AI 资源监控（Token、成本、延迟、P95、成本预警）、RAG 检索效果、组织概览与最近操作日志。
- 组织管理员不会自动获得管理仪表盘；它只对 `is_superuser` 开放。

## 系统监控（`/monitoring`）

- 仅平台超级管理员可访问（路由同时要求 admin 与 platform admin）。
- 卡片：系统状态（CPU、内存）、应用状态（活跃连接、请求总数、错误数、平均响应时间）、知识库状态（文档数、分块数、索引大小、检索请求数）、告警状态。
- 图表：CPU、内存、请求数、响应时间趋势；时间范围可选最近 1 小时 / 6 小时 / 12 小时 / 24 小时 / 7 天。
- 表格：最近告警、操作日志。
- 监控数据每 30 秒自动刷新；操作日志表在进入页面或手动刷新时加载。数据来自 `/api/v1/monitoring/*` 与 `/api/v1/users/audit-logs`，不读取 Prometheus。
- `/metrics`（根路径）是独立的 Prometheus 指标出口；是否导出 RAG/Agent/LLM 细粒度指标由 `PROMETHEUS_ENABLED` 控制，默认关闭。

## 其他审计入口

- 组织设置页的「变更审计」。
- 平台级「操作日志」页面（`/audit-logs`，仅平台超级管理员），支持按操作类型、时间与文本（匹配动作/详情）筛选并导出 CSV；当前没有按用户筛选。

## 依据（代码事实）

- `frontend/src/views/dashboard/user.vue`、`dashboard/admin.vue`、`dashboard/index.vue`
- `frontend/src/views/monitoring/index.vue`
- `frontend/src/views/admin/audit/index.vue`
- `frontend/src/api/monitoring.ts`
- `backend/app/api/v1/endpoints/monitoring.py`
- `backend/app/main.py`（`/metrics` 与开关）
- `backend/app/core/config/base.py`（`PROMETHEUS_ENABLED`）
