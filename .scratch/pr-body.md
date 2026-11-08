## 问题

个人 AI 模型（用户自带 provider / API Key / Base URL）上游调用失败时，`RAGPipeline.chat_stream` 把一句中文诊断文案当作正常 chunk 产出，聊天层随后把它当成 assistant 回答**落库**并写入记忆/缓存。后果：

1. 用户看到一条"回答"，实际是错误提示，且永久留在会话历史里；
2. 前端无法区分"回答"与"错误"，无法本地化（只有中文）；
3. 失败答案可能进入语义缓存。

## 改动

- `pipeline.py`：新增 `UserLLMRequestError`；`chat_stream(..., raise_user_errors=False)`，用户模型路径失败时上抛，系统模型路径行为不变（保持原有兜底文案）。
- `rag_service.py`：透传 `raise_user_errors`。
- `chat_service.py`：捕获 `UserLLMRequestError` 后发 `RAGEvent(type="error", debug_data={"error_code": "USER_LLM_REQUEST_FAILED"})`，**不落库、不写记忆、不写缓存**。
- `chat.py`：SSE / WS 的 error 帧透传 `code` 字段。
- `error_mapping.py`：该静态文案加入白名单，避免被公共清洗替换。
- 前端：`SSEMessage.code?: string`；`useChatSend.onError` 按 code 映射四语 `chat.userLlmRequestFailed`，无 code 时回退原逻辑。

## 验证

- 后端：`pytest`（个人模型相关 + chat stream）88 passed；`ruff check/format` 干净。
- 前端：`vue-tsc --noEmit` 通过；ESLint 通过；`useChatSendWarningToast` / i18n key snapshot / completeness 共 12 tests passed。
- 新增测试：
  - pipeline 在 `raise_user_errors=True` 时抛 `UserLLMRequestError`；
  - chat_service 发 error 事件且不持久化消息；
  - SSE error 帧带 `code`。

## 影响面

仅个人模型失败路径；系统模型（服务器默认）行为不变。
