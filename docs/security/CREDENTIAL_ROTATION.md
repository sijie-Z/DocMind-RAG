# 凭据轮换清单与操作手册（issue #507）

> **本文件禁止写入任何真实密钥值。**
> 只记录凭据名称、历史暴露位置、配置键、轮换方式、状态与完成日期；
> 生成的新值只存在于部署环境（环境变量 / Secret），不得回填到本文件或任何被提交的文件。

## 1. 背景与原则

- 仓库 DocMind-RAG 为公开仓库。issue #507 的安全审计确认：公开 git 历史中的
  `backend/.env.docker`、`backend/docker-compose.yml`、`backend/.env.notification`、
  `backend/demo.py`、`backend/app/agent_api.py`（已删除）曾包含真实或疑似真实凭据
  （详见 [`SECURITY.md`](../../SECURITY.md) 「历史安全事件」）。
- 凭据一旦进入公开历史，即视为已泄露。**轮换（吊销并重建）是唯一止损手段**；
  删文件、改 `.gitignore`、改写历史均不能消除已有暴露。
- 本仓库不执行历史改写（`git filter-repo` / `filter-branch` / force-push 均禁止）。
- 下表每项轮换完成后：把状态改为 `已完成`，填入实际日期，并保留本文件作为审计记录。

## 2. 部署环境需轮换的凭据清单

> 历史暴露位置中的 `fd00b3c` / `b0324c1` 为公开提交号；`backend/.env` 指部署机上
> 未入库的本地文件（其值与 `fd00b3c:backend/.env.docker` 同值）。

| # | 凭据 | 历史暴露位置 | 配置键 / 环境变量 | 轮换方式 | 状态 | 完成日期 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | JWT 签名密钥 | `fd00b3c:backend/.env.docker`；`backend/.env` | `JWT_SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"`，更新部署环境后重启；旧 JWT 全部失效（预期，用户需重新登录） | 待轮换 | YYYY-MM-DD |
| 2 | 应用主密钥 | `fd00b3c:backend/.env.docker`；`backend/.env` | `SECRET_KEY` | 同上命令生成 64 位十六进制；当前代码仅做非空启动校验，仍必须轮换（历史已公开） | 待轮换 | YYYY-MM-DD |
| 3 | 智谱（BigModel，OpenAI 兼容）API Key | `fd00b3c:backend/.env.docker`；`b0324c1:backend/docker-compose.yml`；`backend/demo.py`（历史与本 PR 前）；`backend/app/agent_api.py`（已删除） | `DEEPSEEK_API_KEY` | 智谱开放平台**吊销旧 Key 并新建**，仅写入部署环境变量；先更新配置再吊销，或接受短暂中断 | 待轮换 | YYYY-MM-DD |
| 4 | Embedding API Key | `fd00b3c:backend/.env.docker`；`b0324c1:backend/docker-compose.yml` | `EMBEDDING_API_KEY` | 同 #3（若与 `DEEPSEEK_API_KEY` 复用同一 Key，一并轮换） | 待轮换 | YYYY-MM-DD |
| 5 | Rerank API Key | `fd00b3c:backend/.env.docker`；`b0324c1:backend/docker-compose.yml` | `RERANK_API_KEY` | 同 #3（默认本地 rerank 时该键可为空） | 待轮换 | YYYY-MM-DD |
| 6 | MySQL root 口令 | `fd00b3c:backend/.env.docker` | `MYSQL_ROOT_PASSWORD` | `ALTER USER 'root'@'%' IDENTIFIED BY '<新值>';`（或重建容器卷时重置），同步更新部署 Secret | 待轮换 | YYYY-MM-DD |
| 7 | MySQL 应用账号口令 / 连接串口令 | `fd00b3c:backend/.env.docker` | `MYSQL_PASSWORD`、`DATABASE_URL` | 改密后同步更新 `DATABASE_URL`；滚动重启 backend/worker | 待轮换 | YYYY-MM-DD |
| 8 | Redis 口令 | `fd00b3c:backend/.env.docker` | `REDIS_PASSWORD` | 更新服务端 `requirepass` / ACL 与客户端配置（默认 compose 未启用认证时该键为空，确认无泄露值即可） | 待轮换 | YYYY-MM-DD |
| 9 | MinIO Access Key / Secret Key | `fd00b3c:backend/.env.docker` | `MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY` | `mc admin user` 新建凭据并更新配置，删除旧凭据 | 待轮换 | YYYY-MM-DD |
| 10 | Grafana 管理员口令 | `fd00b3c:backend/.env.docker` | `GRAFANA_USER`、`GRAFANA_PASSWORD` | Grafana 改密或环境变量重置 | 待轮换 | YYYY-MM-DD |
| 11 | SMTP 用户名 / 口令（授权码） | `b0324c1:backend/.env.notification`（占位符形态；若曾填真实值需轮换） | `SMTP_USERNAME`、`SMTP_PASSWORD` | 邮箱服务商改密 / 重置授权码 | 待轮换 | YYYY-MM-DD |
| 12 | 飞书 App Secret / Webhook | `b0324c1:backend/.env.notification`（同上） | `FEISHU_SECRET`、`FEISHU_WEBHOOK_URL` | 飞书开放平台重置 Secret、重建机器人 Webhook | 待轮换 | YYYY-MM-DD |
| 13 | 钉钉 App Secret / Webhook | `b0324c1:backend/.env.notification`（同上） | `DINGTALK_SECRET`、`DINGTALK_WEBHOOK_URL` | 钉钉开放平台重置 Secret、重建机器人 Webhook | 待轮换 | YYYY-MM-DD |
| 14 | 通用 Webhook URL / 自定义 Header | `b0324c1:backend/.env.notification`（同上） | `WEBHOOK_URL`、`WEBHOOK_HEADERS` | 在接收端重新签发地址 / Token | 待轮换 | YYYY-MM-DD |
| 15 | Webhook 规则加密密钥 | 未入库；按 #507 清单做纵深防御轮换（issue #478 引入，独立于 JWT 密钥） | `WEBHOOK_ENCRYPTION_KEY` | `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`；**轮换后存量 webhook 规则密文将无法解密**，需重加密或作废旧规则后重建 | 待轮换 | YYYY-MM-DD |
| 16 | DocMind 自签 API Key（`dm_*`） | 未发现入库泄露；若曾用于对外演示 / 集成，一并吊销重建 | `api_keys` 表（`/api/v1/api-keys`，仅 JWT 可管理） | 通过管理端点吊销旧 Key 并新建；调用方同步更新 | 待轮换 | YYYY-MM-DD |
| 17 | 演示账号口令（如启用） | `b0324c1:backend/docker-compose.yml` 默认 `ENABLE_DEMO_ACCOUNT=true` | `ENABLE_DEMO_ACCOUNT` / 演示账号 | 修改演示账号口令或关闭演示账号 | 待轮换 | YYYY-MM-DD |

## 3. 通用轮换步骤

1. **生成新值**：使用上表命令或供应商控制台生成；不要复用任何历史值。
2. **更新部署环境**：写入环境变量 / Secret（compose `env_file`、K8s Secret、CI Secret 等），
   确认没有把新值写入仓库内文件。
3. **滚动重启** backend 与 worker，使新值生效。
4. **验证**（见 §4）：旧凭据失效、新凭据可用。
5. **吊销旧值**：在供应商控制台 / 数据库侧删除或禁用旧凭据。
6. **回填记录**：更新本表状态为 `已完成` 并填写日期；如发生泄露衍生事件，在
   `SECURITY.md` 追加记录。

### 顺序注意

- 第三方 API Key：先新建并部署，再吊销旧 Key，避免服务中断；
- `JWT_SECRET_KEY` / `SECRET_KEY`：直接替换即可，代价是存量 JWT 失效（预期）；
- `WEBHOOK_ENCRYPTION_KEY`：先处理存量规则密文（重加密或删除重建）再切换；
- 数据库 / Redis / MinIO：先改服务端再改客户端（或反之）会有一段认证失败窗口，
  建议在维护窗口执行。

## 4. 轮换后验证命令

```bash
# 旧 JWT 必须失效：期望 401
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer <旧 JWT>" \
  http://localhost:8000/api/v1/users/me

# 新密钥登录 / 刷新 / 调用必须正常：期望 200
curl -s -o /dev/null -w "%{http_code}\n" \
  -X POST http://localhost:8000/api/v1/auth/login/json \
  -H "Content-Type: application/json" \
  -d '{"username":"<user>","password":"<password>"}'

curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer <新 JWT>" \
  http://localhost:8000/api/v1/users/me
```

第三方 Key 的验证：用**旧 Key** 调用供应商接口应返回鉴权失败；用新 Key 跑一次
embedding / chat 冒烟（或在应用内发起一次问答）应成功。

## 5. 检查清单

- [ ] #1 `JWT_SECRET_KEY` 已轮换并验证旧 JWT 401
- [ ] #2 `SECRET_KEY` 已轮换
- [ ] #3–#5 第三方 API Key 已吊销重建（`DEEPSEEK_API_KEY` / `EMBEDDING_API_KEY` / `RERANK_API_KEY`）
- [ ] #6–#10 数据库 / Redis / MinIO / Grafana 口令已轮换
- [ ] #11–#14 通知凭据（SMTP / 飞书 / 钉钉 / Webhook）已确认或轮换
- [ ] #15 `WEBHOOK_ENCRYPTION_KEY` 已轮换且存量规则已重加密 / 重建
- [ ] #16–#17 自签 API Key 与演示账号已处置
- [ ] 全部完成日期已回填上表
- [ ] `SECURITY.md` 状态说明已同步

## 6. 相关文件

- [`SECURITY.md`](../../SECURITY.md)：报告渠道、支持版本、密钥管理原则、历史事件记录
- `.gitleaks.toml` / `.gitleaksignore`：密钥门禁配置与历史命中登记（只含指纹）
- `.pre-commit-config.yaml`、`.github/workflows/ci-hygiene.yml`（`secret-scan` job）：门禁入口
