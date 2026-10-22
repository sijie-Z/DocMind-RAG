反馈问题时，如果能提供 `request_id`，后端就能在日志中准确定位那一次请求。

## 日志在哪里

- 默认写入 `logs/docmind.log`（相对后端进程工作目录；从 `backend/` 启动时就是 `backend/logs/docmind.log`），每天午夜轮转，保留 30 份。
- 默认 `LOG_JSON=True`，每行是一条 JSON；`LOG_LEVEL` 默认 INFO。
- JSON 日志里带 `request_id` 字段。`trace_id` 与 `user_id` 字段在格式化器中存在，但当前没有代码写入，实际以 `request_id` 为准。

## request_id 如何产生

- 如果请求头带了 `X-Request-ID`，且内容匹配 `[A-Za-z0-9._-]{1,64}`，就沿用它；否则服务端生成一个 uuid4。
- 响应头会回写 `X-Request-ID`。
- 后端的错误响应信封里也会包含 `request_id`。

前端 axios 会自动发送 `X-Request-ID`。

## 反馈时请附上

1. 大致时间（精确到分钟）与时区。
2. 响应头或错误提示里的 `request_id`。
3. 当时使用的账号与组织。
4. 操作步骤、期望结果与实际结果。
5. 界面截图或错误提示原文（注意遮盖敏感信息）。

平台超级管理员可以在「系统监控」页查看操作日志；组织管理员可在「组织设置」查看变更审计。也可以直接检索 `docmind.log` 中的 `request_id`。

## 依据（代码事实）

- `backend/app/core/logging.py`（JSON 格式化与 request_id 字段）
- `backend/app/core/config/base.py`（LOG_FILE / LOG_JSON / LOG_LEVEL）
- `backend/app/core/request_id.py`（请求头校验与生成）
- `backend/app/main.py`（错误信封包含 request_id）
- `frontend/src/utils/request.ts`（发送 X-Request-ID）
