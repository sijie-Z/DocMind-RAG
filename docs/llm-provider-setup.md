# 接入任意 OpenAI 兼容模型（Provider 配置）

> 本文记录一条**实测跑通**的路径：把 DocMind 接到本地 Ollama / OpenAI / vLLM / one-api
> 这类 OpenAI 兼容端点。写作起因是 2026-11-13 的一次真实排查——号称"支持 OpenAI 兼容"，
> 实际只有 DeepSeek 官方基址能跑通。

## 两层配置，职责不同

| 层 | 配置位置 | 作用域 | 典型用途 |
|---|---|---|---|
| 服务端默认 | `.env`：`DEEPSEEK_API_KEY` / `DEEPSEEK_API_URL` / `DEEPSEEK_MODEL` | 全实例 | 生产环境统一出账 |
| 用户级覆盖 | `PUT /api/v1/user/settings/providers`（存 DB，加密保存 key） | 单个账号 | 试用、接入本地模型、按团队计费 |

解析顺序见 `app/services/llm_settings_service.py::resolve_for_user`：
**用户级存在则用用户级，否则回退服务端默认**。

## 用户级覆盖的正确写法（实测）

```bash
# 1) 注册/登录拿 token
curl -s -X POST http://127.0.0.1:8010/api/v1/auth/login \
  -d 'username=<u>&password=<p>' | jq -r '.data.access_token'

# 2) 指定 provider（注意路径是 /user/settings/providers，单数 user）
curl -X PUT http://127.0.0.1:8010/api/v1/user/settings/providers \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"provider":"custom","model":"qwen2.5:3b","api_key":"ollama",
       "base_url":"http://localhost:11434/v1"}'

# 3) 确认生效
curl -s http://127.0.0.1:8010/api/v1/user/settings -H "Authorization: Bearer $TOKEN" \
  | jq '.data.preferences.ai_provider'
# → {"provider":"custom","model":"qwen2.5:3b","base_url":"http://localhost:11434/v1","source":"user"}
```

回退服务端默认：`{"use_server_default": true}`。

## 三个坑（都踩过，且都已修）

### 1. `base_url` 必须带 `/v1`

OpenAI SDK 会把它拼成 `<base_url>/chat/completions`。写成 `http://localhost:11434`
会得到 `404 page not found`；必须是 `http://localhost:11434/v1`。

> 修复：`app/dependencies.py` 曾把 `/v1` 当后缀剥掉（PR #686），现已只剥离
> `/chat/completions`。

### 2. 模型名不能被写死

`AgentConfig.model` 曾硬编码 `deepseek-v4-flash`，改了 base_url 仍拿厂商型号去请求，
报 `404 model 'deepseek-v4-flash' not found`。

> 修复：默认值改为 `settings.DEEPSEEK_MODEL`（PR #686）。

### 3. 信封不同：`/users/settings` vs `/user/settings`

`/api/v1/users/settings/providers` → **404**；正确的是 `/api/v1/user/settings/providers`。

## 实测结果（本地 Ollama qwen2.5:3b）

4 轮 Agent 任务、共 6 次工具调用，平均 1.50 次/轮：

```
list_documents        2 次（成功 2）
search_knowledge_base 2 次（成功 2）
utility               1 次（成功 1）
translate_text        1 次（失败 —— 3B 模型能力不足）

端到端耗时：14.0s / 22.0s / 26.7s / 27.1s（CPU 推理）
```

结论：链路编排正确，模型能力是瓶颈；换更大模型或官方 API 后应复测
`translate_text` 与耗时。

## 排查顺序（下次直接照这个走）

1. `GET /api/v1/user/settings` 看 `ai_provider.source` 是 `user` 还是 `server`
2. 直接打一次端点：`curl <base_url>/chat/completions`，确认不是 404
3. 看后端日志里 `HTTP Request: POST <url>` 的**真实 URL**
   —— 如果少了 `/v1`，就是坑 1；如果模型名不对，就是坑 2
4. 再看 `agent_tool_calls` 表：**`tool_error` 不等于"没调用"**，
   统计口径要同时算 `tool_call` 与 `tool_error`
