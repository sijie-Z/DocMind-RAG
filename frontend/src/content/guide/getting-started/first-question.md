文档进入「已索引」状态后，就可以在对话页提问了。

## 提问

1. 左侧菜单进入「智能对话」。
2. 在输入框写下问题，按 Enter 发送（当前版本 Shift+Enter 不会换行；长文本建议先在编辑器中写好再粘贴）。
3. 默认使用「快速问答」：一次检索 + 一次生成，适合事实型、定位型问题。
4. 需要多步推理、跨文档分析时，切换到「深度分析」。它会调用 PER Agent：先规划步骤，再调用工具执行，最后反思校正。深度分析只支持 SSE 流式执行。

## 阅读答案

- 答案正文里的 `[n]` 是引用标记，但正文中的标记本身不可点击；下方来源卡片可以点开，查看来源片段、文档名与相关度（重排分或排序位）。
- 对于 PDF 文档，来源还会显示页码；其他格式没有页码信息。
- 回答生成过程中可以点「停止生成」中断；已生成的部分会保留并标记为已停止。

## 导出与继续

- 对话页右上角「导出对话」会把当前对话导出为 Markdown 文件（浏览器端生成，包含来源引用），不会上传到服务器。
- 左侧会话列表的「新对话」只重置当前界面；真正的会话记录会在发送第一条消息时由后端创建。
- 删除会话需要二次确认，删除后不可恢复。

## 依据（代码事实）

- `frontend/src/views/chat/index.vue`（发送、导出、来源预览）
- `frontend/src/views/chat/components/ChatInput.vue`（快速问答/深度分析、Enter 键处理）
- `frontend/src/views/chat/composables/useChatSend.ts`（`useAgent` 与 SSE 强制）
- `frontend/src/utils/sseService.ts`（SSE 事件白名单）
- `backend/app/api/v1/endpoints/chat.py`（`useAgent` 转交 Agent 栈）
- `backend/app/services/chat_service.py`（来源结构与 `pageNumber`）
