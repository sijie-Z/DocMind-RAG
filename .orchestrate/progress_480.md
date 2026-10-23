# progress_480

- [x] 任务书与 issue 规格读取；基线核对 71addabe；迁移 head 012_session_fork
- [x] 1. API key 模型/服务/端点/迁移/认证分支/require_scope + tests（8 passed）
- [x] 2. SSE format_sse + Redis ring buffer + Last-Event-ID + 断连保留 + 前端（11 passed）
- [x] 3. dispatcher + error_mapping + rpc server/http（13 passed）
- [x] 4. 8 个 RPC 方法（session/prompt 幂等/双向审批/shutdown）
- [x] 5. stdio 入口（管道手测 initialize/tools/shutdown；缺失身份 exit 2/stdout 0 字节）
- [x] 6. Python SDK（7 passed）
- [x] 7. TS SDK（tsc --noEmit + npm test 7 passed）
- [x] 8. 指标/ADR-007/facts/文档（check_doc_facts OK）
- [x] 9. 自测（ruff 全绿；分块 pytest 全绿；alembic 往返+parity；SDK 9+8；stdio 手测；两项变异试验红→还原绿）
- [~] 10. 内部对抗评审：首轮 FAIL（4 blocker）已全部修复并回归；复审进行中
- [ ] 11. 收尾报告
