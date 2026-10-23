# ADR-005：Webhook Runtime —— 入站规则、HMAC 验签与 outbox 建会话

- 状态：已采纳（issue #478）
- 日期：2026-10-23
- 关联：Epic #473；依赖 #476（会话谱系）与 #479（配置分层）
- 证据：`backend/tests/unit/test_webhook_signature.py`、`backend/tests/unit/test_webhook_state_machine.py`、
  `backend/tests/api/test_webhooks_api.py`、`backend/tests/integration/test_webhook_e2e.py`

## 结论

1. **签名**采用 Standard Webhooks 头格式（`webhook-id` / `webhook-timestamp` /
   `webhook-signature`）：HMAC-SHA256 覆盖 `"{id}.{timestamp}." + 原始 body`，
   `webhook-signature` 允许空格分隔多个 `v1,<base64>`（任一匹配即过，支持轮换期
   新旧 secret 并存），`hmac.compare_digest` 常数时间比较，时间戳偏差 > ±300s
   一律 401。**先取原始字节验签，再 `json.loads`**，绝不重序列化后验签。
2. **secret 只存 Fernet 密文**：服务端生成 `whsec_<base64(32 bytes)>`，明文仅在
   创建/轮换响应中出现一次；加密密钥是独立的 `WEBHOOK_ENCRYPTION_KEY`，
   **不复用 JWT `SECRET_KEY`**。轮换时旧密文保留 24h（`secret_prev_encrypted`），
   验签同时接受新旧；24h 后旧 secret 自动失效（读时按 `secret_rotated_at` 判定）。
3. **幂等**：`webhook-id` 优先，缺失/超列宽回退 `sha256(raw_body)`。Redis
   `SET NX EX 604800` 只是快路径；**DB `UNIQUE(rule_id, idempotency_key)` 是最终
   仲裁**（Redis 不可用时仅告警 + 指标，不阻断）。重复语义：`succeeded` → 200
   duplicate；`failed` 且还有自动重试 → 202 duplicate（不重置）；`received/processing`
   → 202 duplicate；`failed` 且 `next_attempt_at IS NULL`（三次失败后的终态）→
   重置 `received/attempts=0/next_attempt_at=NULL` → 202。
4. **持久化 outbox**：入站端点只写 `webhook_deliveries(status=received)` 并返回
   202，**禁止 `BackgroundTasks`**。`poll_once()` 用
   `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 10`（MySQL 8）领取
   `received` / 到点的 `failed` / 租约超时（`processing_started_at` 早于 10 分钟）
   的 `processing`，先提交领取结果再处理。多 worker 由 `SKIP LOCKED` 去重。
5. **重试语义**：`attempts` 在领取时 +1；失败时 `attempts < WEBHOOK_MAX_ATTEMPTS`
   → `failed + next_attempt_at = now + 2^attempts * 30s`；达到上限 →
   `failed + next_attempt_at = NULL`（终态，轮询不再领取）。发送方用**同一
   `webhook-id`** 重投终态失败即重新入队（见上条）。
6. **会话与身份**：处理时**直接调用** `AgentService.chat(...)`（不走 HTTP 自调用），
   身份固定为 `rule.created_by`（服务账号，绝不提权）；`session_mode=new` 每次建
   新会话，`reuse` 按 `(rule_id, payload.session_key)` 复用（缺失回退 new）。
   `ChatSession` 记录 `source="webhook"`、规则 id、投递 id、session_key。
7. **注入防护**：模板只允许裸占位符 `{payload}/{rule_id}/{delivery_id}`
   （`string.Formatter` 解析；属性访问/转换/格式说明一律拒绝），保存时拒绝
   `system_prompt/system/tools/tool_choice/model/messages/temperature/max_tokens/role`
   等关键字。渲染时 payload 包在
   `<<<UNTRUSTED_WEBHOOK_PAYLOAD>>> ... <<<END_UNTRUSTED_WEBHOOK_PAYLOAD>>>`
   分隔区里。
8. **进程内 poll 循环只在 `web` 入口启动**（与 #479 对调度器的裁定一致），
   `WEBHOOKS_ENABLED=true` 且缺/非法 Fernet key 时**启动失败（fail-closed）**。

## 与 issue 的偏离（有意为之，评审请重点核对）

1. **`webhook_deliveries.payload` 列**：issue §5.1 的字段清单只有 `payload_hash`，
   但 outbox 在请求结束之后才处理投递；没有持久化 payload 就无法渲染 prompt /
   取 `session_key`，`payload_hash` 不可逆。为让 issue 的异步语义可实现，新增
   `payload TEXT NOT NULL`（原始 JSON 文本），`payload_hash` 仍按 issue 计算原始
   字节 sha256。`docs/decisions` 与迁移注释均标注该偏离。
2. **`core/prometheus.py` 的修改**：任务书 §1.6 明确要求把
   `webhook_delivery_total{status}` / `webhook_poll_errors_total` 等挂到现有指标
   注册方式，但 §0.3 白名单未列该文件；按 §1.6 执行并在报告列明。
3. **全局 body 上限是硬顶**：规则 `max_body_bytes` 只能收紧到不高于
   `WEBHOOK_MAX_BODY_BYTES`，避免单条规则的配置错误放大全局边界。
4. **dry-run 验签失败**返回 200 + `signature_valid=false` + `signature_error`
   （issue 只定义了 `true`/`null`；诊断端点返回布尔结果比 401 更有用）。
5. **`secret_prefix` 按字面取前 6 位**：所有 secret 共享 `whsec_` 方案前缀，因此
   该字段按设计不泄露随机材料；完整明文只在创建/轮换响应出现一次。

## 被否决的方案

- **仅 GitHub `X-Hub-Signature-256`**：无时间戳，防重放只剩幂等键；且与提供方
  强绑定，无法覆盖通用事件源。否决。
- **Kafka 队列**：现有 Kafka 消费者只服务文档处理，接入需要新的消费组/部署单元，
  与"投递量与业务量同量级"的预期不成比例；DB outbox + `SKIP LOCKED` 已满足
  跨进程不重复领取。否决。
- **`BackgroundTasks`**：进程重启/多 worker 即丢事件，issue 明令禁止。否决。
- **纯 Redis 幂等**：Redis 不持久（重启/淘汰即丢），必须 DB 唯一约束兜底。
  否决"只靠 Redis"。
- **JWT `SECRET_KEY` 兼作 Fernet key**：密钥用途混用，JWT 轮换会同时废掉全部
  webhook secret。否决。

## 关键实现细节

- 签名密钥解码：`whsec_` 前缀去掉后 base64 严格解码；单个损坏 secret 跳过，
  继续尝试其他（轮换期）secret。
- `Content-Length` 超限时**直接 413，不调用 `request.stream()`**（API 测试用 ASGI
  receive 计数器断言为 0）；chunked 请求流式累计，每块检查累计值，超限立即停止。
- 处理租约：`processing_started_at` 超过 10 分钟即可被重新领取；崩溃的尝试计入
  `attempts`（有界重试仍收敛）。
- 重试延迟与 issue §8 示例的 30s 存在口径差：§8 写"失败一次后 30s 到点"，而
  §5.3 与任务书的正则是 `2^attempts*30s`，本实现按后者（首次失败 60s）；测试通过
  直接改写 `next_attempt_at` 模拟到点，不依赖真实睡眠。
- 投递失败错误摘要经 `app.core.log_safety.log_safe` 脱敏并限长（500 字符），
  不把原始异常/响应体直接落库。
- 审计：规则创建/修改/删除/轮换写 `user_activity_logs`（`action=*_webhook_*`）；
  投递处理成功/失败各记一条（`action=process_webhook_delivery`）。审计是
  best-effort（`audit_service` 自身吞异常），不阻塞投递状态机。

## 运维手册（对接 / 轮换 / 排查）

### 对接示例（Python）

```python
import base64, hashlib, hmac, json, time, uuid, requests

secret = "whsec_..."  # 创建规则时响应里的 data.secret，仅出现一次
body = json.dumps({"event": "ping"}, separators=(",", ":")).encode()
msg_id = f"msg_{uuid.uuid4().hex}"
timestamp = str(int(time.time()))
key = base64.b64decode(secret[len("whsec_"):])
signature = base64.b64encode(
    hmac.new(key, f"{msg_id}.{timestamp}.".encode() + body, hashlib.sha256).digest()
).decode()
requests.post(
    "http://localhost:8000/api/v1/webhooks/<rule_id>",
    data=body,
    headers={
        "webhook-id": msg_id,
        "webhook-timestamp": timestamp,
        "webhook-signature": f"v1,{signature}",
        "content-type": "application/json",
    },
)
# 202 {"success":true,"data":{"delivery_id":"...","status":"received"}}
```

### secret 轮换步骤

1. `POST /api/v1/webhooks/rules/{id}/rotate-secret`（组织管理员）→ 响应含新
   `data.secret`（仅一次）。
2. 把新 secret 配置到发送方；24h 内旧 secret 仍可验签，窗口内完成切换。
3. 24h 后旧 secret 自动失效；`GET /rules` 只显示 `secret_prefix` 与
   `secret_rotated_at`，可按时间确认轮换生效。

### 失败排查

| 现象 | 排查 |
|---|---|
| 401 `AUTHENTICATION_ERROR` | 时间戳超出 ±300s（对时钟）/ secret 不匹配 / 签名内容用了重序列化 body |
| 404 `NOT_FOUND` | 规则 id 不存在、规则被禁用，或 `WEBHOOKS_ENABLED=false`（三者同形，防枚举） |
| 413 `PAYLOAD_TOO_LARGE` | `detail.max_body_bytes` 为生效上限（全局硬顶与规则值的较小者） |
| 429 `RATE_LIMIT_EXCEEDED` | 看 `Retry-After`；限流按 规则+IP 每分钟；超限请求不落投递行 |
| `GET /deliveries` 显示 `failed` | `error` 为脱敏摘要；`next_attempt_at` 非空 = 会自动重试，为空 = 终态；用同一 `webhook-id` 重投可重置 |
| 投递长期 `processing` | 进程崩溃；10 分钟租约后 poll 重新领取（`attempts` 已计入上次尝试） |

## 已知风险 / 未验证项

- **Redis 不可用**：幂等降级为 DB-only（安全但多一次 DB 查询）；限流降级为
  进程内计数（多 worker 下是近似值）。本机验证时 Redis 不可达，降级路径被 e2e
  实际走过；Redis 正常路径未做集成验证。
- **`SKIP LOCKED` 需要 MySQL 8**（项目基线）。MariaDB/旧版本会退化为阻塞锁，
  多 worker 吞吐下降但不丢事件。
- **Fernet key 轮换**：更换 `WEBHOOK_ENCRYPTION_KEY` 前必须用旧 key 解密、新 key
  重新加密全部 `webhook_rules` 密文；否则所有规则解密失败（启动校验只验格式，
  不验存量密文）。
- **审计为 best-effort**：DB 抖动时审计行可能缺失，投递状态机不受影响。
- **LLM 失败不落半成品**：`error` 事件或异常都按整次失败处理，不写用户/助手消息；
  成功路径才 `save_session_interaction`。
