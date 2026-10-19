# DocMind 前端

DocMind 企业级 RAG 知识库系统的 Vue 3 前端。后端为 FastAPI 应用，业务接口统一挂在 `/api/v1` 前缀下（根级 `/health`、`/metrics` 除外）。

## 技术栈

- **框架**：Vue 3 + TypeScript + Vite
- **UI**：Naive UI + UnoCSS
- **状态管理 / 路由**：Pinia + Vue Router
- **可视化 / 编辑器**：ECharts、Vue Flow（工作流 DAG 编辑器）
- **消息渲染**：markdown-it + KaTeX + highlight.js + DOMPurify
- **测试**：Vitest（单元）、Playwright（E2E）

## 环境要求

- Node.js 18+
- 后端（本仓库 `backend/`）：Python 3.11+、MySQL 8、Redis 7、Elasticsearch 8、Kafka、MinIO

## 启动

### 1. 启动后端

```bash
cd backend
cp .env.example .env               # Windows: copy .env.example .env
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 文档处理 worker（独立进程，可选）
python start_worker.py
```

- API 文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health
- 业务接口前缀：`/api/v1`

使用 Docker 部署时另需 `backend/.env.docker`（模板：`backend/.env.docker.example`），完整说明见仓库根 `README.md` 与 `SETUP.md`。

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

开发服务器按 `vite.config.mts` 将 `/api`、`/health` 与 WebSocket 代理到后端。

## WebSocket

- 聊天：`/api/v1/chat/ws`
- 通知：`/api/v1/notifications/ws`

开发模式下聊天连接走 Vite 代理的 `/ws`（转发到 `/api/v1/chat/ws`），生产构建使用完整路径。

## 常用脚本

| 命令 | 说明 |
|------|------|
| `npm run dev` | 启动开发服务器 |
| `npm run build` | 类型检查 + 生产构建 |
| `npm run preview` | 预览构建产物 |
| `npm run test` | Vitest 单元测试 |
| `npm run test:watch` | 单元测试 watch 模式 |
| `npm run test:coverage` | 单元测试覆盖率 |
| `npm run test:e2e` | Playwright E2E |
| `npm run type-check` | `vue-tsc --noEmit` |
| `npm run lint` | ESLint 自动修复 |

## 目录结构

```
frontend/
├── src/
│   ├── api/          # Axios API 客户端（baseURL 默认 /api/v1）
│   ├── components/   # 共享组件
│   ├── composables/  # 组合式函数
│   ├── locales/      # i18n（zh/en/ja/fr）
│   ├── router/       # Vue Router 配置
│   ├── stores/       # Pinia 状态
│   ├── utils/        # 工具函数（含 WebSocket 封装）
│   └── views/        # 页面模块
├── public/           # 静态资产（PWA 图标等）
└── vite.config.mts   # Vite / PWA / 开发代理配置
```

页面与路由数量等文档数字以仓库根 `docs/facts.json` 为单一事实源（由 `backend/scripts/check_doc_facts.py` 在 CI 校验），本文件不再手写具体数字。

## 许可证

MIT License
