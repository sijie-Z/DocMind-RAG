上传成功不等于可检索。只有文档完成解析、切分、向量化并变成「已索引」后，检索才会命中它。按下面的顺序排查。

## 1. 看状态

在「知识库」列表视图确认状态：

- 「等待处理 / 解析中」：还在处理中，稍等并刷新（「已上传」「已解析」是兼容/历史状态，正常上传不会经过）。
- 「失败」：解析出错；失败文档的卡片/行内会出现红色错误提示条，点击查看解析错误详情。
- 「已索引」：索引已完成，可以进入下一步。

## 2. 处理失败文档

- 单个失败文档：点击「重新解析」。
- 批量失败：工具栏「最近失败文档重建」。
- 重建需要 `knowledge_base:rebuild_index` 权限；普通成员可能收到 403，请联系管理员。

常见失败原因：格式不在解析器支持列表（如 .doc、.ppt / .pptx）、图片里没有可识别文字、文件损坏、超过 100MB。

## 3. 确认搜索范围

- 知识库页面的搜索框只按标题匹配；请到「语义搜索」页面按内容检索，或在对话中提问。
- 检索只在本组织范围内进行：确认你当前所在组织就是上传文档的组织。
- 文档可能确实不含相关内容，换用文档中的关键词再试。

## 4. 检查检索基础设施

- 打开 `/health`，查看 `elasticsearch` 与 `knowledge_base` 两个检查项。
- `knowledge_base` 的覆盖率低于 95% 会标记 unhealthy，说明索引分块与已标记文档不一致；可用后端脚本 `backend/scripts/reconcile_knowledge_index.py --reindex` 对账重建（需运维执行）。
- 若最近删除或重建过文档，缓存会被清理，属正常现象。

## 依据（代码事实）

- `frontend/src/views/knowledge/index.vue`（状态、失败提示、重新解析、最近失败文档重建）
- `backend/app/api/v1/endpoints/knowledge.py`（重建接口与权限）
- `backend/app/rag/retriever.py`（组织过滤）
- `backend/app/main.py`（`/health` 的知识库覆盖率检查）
- `backend/scripts/reconcile_knowledge_index.py`（全量对账重建）
