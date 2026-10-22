组织设置页面（`/organization-settings`）面向组织管理员，用于调整本组织的模型、配额、检索与隐私策略，并查看设置变更审计。

## 可配置项

- 默认 Provider、默认模型。
- 月度 Token 配额。
- 工具白名单。
- 检索 top_k（1–50）：影响知识库搜索与 RAG 检索；请求显式传 top_k 时以请求为准。
- 允许隐私模式：关闭后，成员的隐私模式开关会置灰（组织策略优先）。

根据页面运行时提示：Provider 引用、月度配额与工具白名单当前只存储并参与生效值展示；默认模型当前作用于 RAG 问答生成；这些字段的强制执行尚未落地（页面不会伪装已拦截生效）。检索 top_k 与「允许隐私模式」会被检索/聊天链路实际消费。

保存时只提交发生变化的字段。页面还会展示关键配置的「生效值 / 来源（组织设置或平台默认）/ 是否需重启（需重启或即时生效）」。

## 审计

页面内嵌「变更审计」卡片，展示时间、操作人与变更内容，数据来自组织的设置审计接口；没有记录时显示「暂无变更记录」。

## 权限

- 前端路由要求管理员（`requiresAdmin`），左侧「管理」分组仅管理员可见。
- 后端：读取设置允许组织成员（聊天页需要读取隐私策略），修改与审计要求组织管理员或平台超级管理员。
- 平台超级管理员可跨组织操作；组织管理员只对自身组织生效。

## 不存在的功能

- 没有「隐私政策」文本编辑器；登录页的隐私政策链接指向占位法务页面。
- 没有多知识库管理；每个组织只有一个逻辑知识库。

## 依据（代码事实）

- `frontend/src/views/organizations/settings.vue`
- `backend/app/api/v1/endpoints/organization_settings.py`
- `frontend/src/api/organization.ts`
- `backend/app/services/chat_service.py`（隐私模式与 top_k 消费）
- `frontend/src/router/index.ts`（`requiresAdmin`）
- `frontend/src/views/legal/index.vue`（法务占位页）
