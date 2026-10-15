# DocMind 前端

DocMind（企业级 AI Agent 知识库系统）的 Web 前端。完整项目说明、架构与整体启动步骤见仓库根目录 [README.md](../README.md)。

## 技术栈

- **框架**：Vue 3 + TypeScript
- **构建**：Vite
- **UI**：Naive UI（图标使用 @iconify/vue 与 @vicons/ionicons5）
- **状态 / 路由**：Pinia + Vue Router（24 条路由）
- **可视化**：ECharts（仪表盘）、Vue Flow（工作流编辑器）
- **其他**：KaTeX 数学公式、i18n（zh / en / ja / fr）、DOMPurify 富文本净化

## 开发与构建

```bash
npm install
npm run dev          # 开发服务器 http://localhost:5173
npm run build        # 类型检查 + 生产构建（产物在 dist/）
npm run type-check   # vue-tsc --noEmit
npm run test         # Vitest 单元测试
npm run test:e2e     # Playwright 端到端测试
```

开发服务器经 Vite 代理访问后端 API（配置见 `vite.config.ts`）；后端默认运行在 8000 端口，启动方式见仓库根目录 `SETUP.md`。

## 默认账户

- 用户名: admin
- 密码: 见 `_试用清单.md`（本地文档，不随仓库分发；部署环境由 `reset_admin.py` 的 `ADMIN_PASSWORD` 指定）

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `VITE_API_BASE_URL` | `/api/v1` | 后端 API 前缀 |

## 目录结构

```
src/
├── api/          # Axios API 客户端
├── components/   # 共享组件
├── composables/  # 组合式函数
├── locales/      # i18n（zh / en / ja / fr）
├── router/       # 路由
├── stores/       # Pinia 状态
├── utils/        # 工具函数
└── views/        # 页面
```
