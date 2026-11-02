# DocMind Demo 安全闸（上线前逐项复核）

公网演示面对的是匿名访问者 + LLM 账单 + 上传的任意文件；“能打开”不等于“能上线”。
下表是上线前必须过的闸，状态以仓库内实现为准，部署动作标为“部署时”。

| # | 闸门 | 实现 | 验证方式 | 状态 |
|---|------|------|---------|------|
| 1 | 基础设施只绑本机 | `backend/docker-compose.yml` 全部端口 `127.0.0.1`（本批修复 backend 8000 / Kafka 29092 / Zookeeper 2181 的 `0.0.0.0` 绑定；Kafka 内部监听器不再发布宿主端口） | 宿主机 `ss -lntp` 无非回环监听（除 Cloudflare Tunnel 的出站连接） | ✅ 仓库内 |
| 2 | 密钥 fail-closed | compose `${VAR:?required}`：缺 MYSQL/MINIO/GRAFANA 密码直接拒绝启动 | `docker compose config` 缺值时即报错 | ✅ 仓库内 |
| 3 | TLS | Cloudflare 边缘终止（Tunnel 或代理）；nginx 下发 HSTS | 浏览器 https + `curl -I` 看 `strict-transport-security` | 部署时 |
| 4 | 访问门 | **Cloudflare Access**（Zero Trust，Free ≤50 用户）保护 `docmind.<域名>`，只允许指定邮箱；未开通时可用 nginx basic auth 兜底（见文末） | 未授权邮箱访问 → Access 登录页/403 | 部署时 |
| 5 | 上传限额 | nginx `client_max_body_size 12m` + 后端 `MAX_FILE_SIZE=10MB` | `verify_demo.sh` 用 11MB 文件验证被拒 | ✅ 仓库内 |
| 6 | 限流 | nginx 两档：普通 API 300r/m（IP 维度，突发 60）、聊天 30r/m（突发 5）；后端 `RATE_LIMIT_REQUESTS_PER_MINUTE=300` 兜底 | 压测出现 429；上线后实测 | ✅ 仓库内 |
| 7 | 演示数据合成 | `seed_demo.py` 只用合成采购文本，标题标注“演示” | 人工检查文档与案件 | ✅ 仓库内 |
| 8 | LLM 成本 | Access 门 + 限流；DeepSeek 账户侧设配额/余额告警 | 平台账单页 + 日志 `LLM_CALL_LOG_*` | 部分（配额在平台侧） |
| 9 | 日志 | 容器 json-file `10m×3`（backend/worker；其余服务用默认）；后端 `backend/logs/` | `docker compose logs --since=1h` | ✅ 仓库内 |
| 10 | 备份 | `backup.sh`：MySQL 逻辑备份 + MinIO 原始文件卷 | 备份文件可解压；恢复演练 | ✅ 脚本；演练待做 |
| 11 | 公开功能面 | 本批未动；按产品决策后续把产品 profile 冻结到 文档入库 / 证据问答 / 核验工作台 | 导航与 API 能力清单 | 计划 |
| 12 | 注册开放性 | 当前开放注册（在 Access 门内）；未加邀请码 | —— | 已知边界 |
| 13 | 每用户 LLM 配额 | 未实现（B4 骨架只有展示） | —— | 已知边界 |

## 已知残余风险（明确接受再上线）

- Kafka / Elasticsearch / Redis 无认证，但只监听容器网络与 127.0.0.1，宿主机不对外暴露。
- Cloudflare Free 在中国大陆的访问质量波动；对内演示可接受。
- 无邮箱验证与找回密码；演示账号密码只发给被邀请的人。
- 上传文件按合成数据对待；不要用真实合同做公开演示。

## 兜底：不用 Cloudflare Access 时的 nginx basic auth

1. 生成口令文件：`htpasswd -Bc deploy/demo/.htpasswd demo`
2. 在 `deploy/demo/nginx.demo.conf` 的 `server {}` 内加：
   ```nginx
   auth_basic "DocMind Demo";
   auth_basic_user_file /etc/nginx/.htpasswd;
   ```
3. 在 Dockerfile.frontend 增加 `COPY deploy/demo/.htpasswd /etc/nginx/.htpasswd`（该文件不入库，加入 .gitignore）。
