# DocMind 公网 Demo 部署手册（挂到 sterlingzeng.cn）

## 先厘清一件事：博客本身没有服务器

`blogsterlingzeng` 是 **Hexo 静态站 + GitHub Pages + Cloudflare**（见博客仓库
`docs/DEPLOYMENT.md`），没有任何可运行后端的服务器。DocMind 是 FastAPI + MySQL +
Elasticsearch + Kafka + MinIO 的有状态栈，**不能放进博客仓库/Pages**。

可以复用的是博客已经在用的：**域名 `sterlingzeng.cn`、Cloudflare 托管 DNS、HTTPS 与
访问控制**。把 Demo 挂成子域名即可，例如 `docmind.sterlingzeng.cn`。

## 推荐拓扑（源站零入站端口）

```
面试官/手机 4G
      │ HTTPS
      ▼
Cloudflare 边缘（TLS / Access 邮箱门 / DDoS）
      │ Cloudflare Tunnel（出站长连接）
      ▼
云主机 127.0.0.1（无公网入站端口）
  frontend(nginx:8080) → backend(8000) → mysql/redis/es/minio/kafka（仅容器网络+回环）
```

- **TLS 与访问门在 Cloudflare**：源站不需要证书、不需要开放 80/443。
- 访问门用 **Cloudflare Access**（Zero Trust Free ≤ 50 用户）：只有被邀请的邮箱能打开。
- 产品登录页仍要求账号（演示账号见下），双保险。

## 资源规格（重要）

| 规格 | 可行性 | 说明 |
|------|--------|------|
| 4C8G | ✅ 推荐 | Kafka/ES/MySQL/MinIO 全栈 + API Embedding，舒适 |
| 4C4G + 2G swap | ⚠️ 勉强 | 必须用云端 Embedding（不要跑 Ollama）；构建前端时开 swap |
| 2C2G | ❌ | ES + Kafka 就会打满 |

- 不要在该机器跑 Ollama：`deploy/demo/.env.demo.example` 默认走 OpenAI 兼容的云端
  Embedding（百炼 text-embedding-v3 / SiliconFlow bge-m3，1024 维），省 1–2GB 内存。
- 国内访问优先选**阿里云中国内地**（域名已备案：蜀ICP备2026016586号）；Cloudflare
  Free 在高峰期可能有延迟，可接受再上线。

## 路线 A（推荐）：云主机 + Cloudflare Tunnel

### 1. 准备主机

1. 购买阿里云轻量应用服务器/ECS（Ubuntu 22.04，≥4C8G），安全组**不开任何入站端口**
   （Tunnel 是出站连接；SSH 只对你的 IP 开放）。
2. 安装 Docker Engine + Compose 插件（官方脚本或阿里云镜像源）。

### 2. 部署 Demo 栈

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git   # 国内直连不稳时用代理
cd DocMind-RAG

cp deploy/demo/.env.demo.example backend/.env.docker
# 逐项填写：SECRET_KEY / JWT_SECRET_KEY / MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD /
#          MINIO_ACCESS_KEY / MINIO_SECRET_KEY / GRAFANA_PASSWORD /
#          DEEPSEEK_API_KEY / EMBEDDING_API_KEY / DEMO_PASSWORD
# 密码生成：python3 -c "import secrets; print(secrets.token_hex(24))"

./deploy/demo/build.sh   # 构建前端镜像（4G 机器先 sudo swapon 2G swap）

docker compose --env-file backend/.env.docker \
  -f backend/docker-compose.yml -f deploy/demo/docker-compose.demo.yml up -d --build
```

> 首次启动需要构建 backend/worker 镜像（pip 安装依赖），4G 机器请先开 2G swap；
> 之后升级代码再执行 `up -d --build`，日常重启不需要 `--build`。

首次启动会先跑数据库迁移（`migrate` 服务），backend/worker 等它成功后才启动。
等待就绪：

```bash
docker compose --env-file backend/.env.docker \
  -f backend/docker-compose.yml -f deploy/demo/docker-compose.demo.yml ps
curl -s http://127.0.0.1:8000/health | python3 -m json.tool | head -40
```

### 3. 生成演示数据（真实链路，非 seed 假数据）

```bash
docker compose --env-file backend/.env.docker \
  -f backend/docker-compose.yml -f deploy/demo/docker-compose.demo.yml \
  exec -T -e DEMO_PASSWORD='你的演示密码' backend \
  python - --base-url http://frontend < deploy/demo/seed_demo.py
```

脚本会用**真实注册→上传→worker 解析/摘要/嵌入/索引→建案→分析→决策→导出→归档**
生成一个“已完成核验”的案件；访客用 `docmind_demo / DEMO_PASSWORD` 登录即可看到。

### 4. 接 Cloudflare Tunnel

1. Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel（选 cloudflared）。
2. Public hostname：`docmind` → service `http://frontend:80`。
3. 复制 connector token，填入 `backend/.env.docker` 的 `TUNNEL_TOKEN=`。
4. 启动隧道容器：

```bash
docker compose --env-file backend/.env.docker \
  -f backend/docker-compose.yml -f deploy/demo/docker-compose.demo.yml \
  --profile tunnel up -d cloudflared
```

### 5. 开 Cloudflare Access

Zero Trust → Access → Applications → Add self-hosted：
- Domain: `docmind.sterlingzeng.cn`
- Policy: `Allow` + `Emails`（你自己 + 要展示给的人）
- 登录方式：One-time PIN（邮箱验证码）即可。

### 6. 验收

```bash
DEMO_PASSWORD='...' ./deploy/demo/verify_demo.sh
EXTERNAL_URL=https://docmind.sterlingzeng.cn DEMO_PASSWORD='...' ./deploy/demo/verify_demo.sh
```

再人工过一遍：手机 4G 打开 → Access 邮箱验证 → 用演示账号登录 → 看到已归档的核验案件
→ 打开案件详情/导出。安全闸逐项对照见 [`SECURITY.md`](SECURITY.md)。

## 路线 B（临时）：本机 + Tunnel

零新增成本，适合“明天就要给人看”：

1. 本机按上面步骤 2/3 起栈（Windows 用 WSL2 + Docker；内存 ≥8GB 可用时）。
2. 同样接 Cloudflare Tunnel + Access。
3. **代价**：机器必须常开；家宽/休眠/内存压力都会让演示掉线。作品集场景不推荐长期用。

## 路线 C（不推荐）：公网 80/443

必须同时做到：防火墙只放行 Cloudflare IP 段（或直接不用这条路）、TLS 用 Cloudflare
代理/Origin 证书、Access 或 basic auth 开启。否则不要做。

## 与博客的集成

博客保持静态：新增一篇文章/导航项链接到 `https://docmind.sterlingzeng.cn` 即可
（例如“合同核验 Demo”）。不要把后端代码放进博客仓库，也不要试图用 Pages 托管 API。

## 运维

```bash
# 日志
docker compose ... logs -f --tail=200 backend worker frontend
# 升级（拉新代码后）
git pull && ./deploy/demo/build.sh && docker compose ... up -d --build
# 备份（MySQL + MinIO）
./deploy/demo/backup.sh
# 恢复 MySQL
gunzip -c deploy/demo/backups/<ts>/mysql-<ts>.sql.gz | \
  docker compose ... exec -T mysql sh -c 'exec mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"'
```

## 故障排查

| 症状 | 可能原因 | 处理 |
|------|---------|------|
| backend 一直 unhealthy | ES 内存不足 / 迁移未完成 | `docker compose logs migrate backend`；加到 8G 或降 ES 堆 |
| Access 之外直接能访问 | DNS 记录未走 Tunnel/代理 | 删除直连 A 记录，仅保留 Tunnel 的 CNAME |
| 上传 413/400 | 超过 10MB 限额 | 演示限额，属预期 |
| seed 403 document:update | 账号无 OrgAdmin（老账号） | 用脚本新建的 `docmind_demo` 账号；或跑 `backend/scripts/backfill_org_owner_admin.py` |
| 访客说打不开 | Access 策略未包含其邮箱 / 国内到 Cloudflare 波动 | 加邮箱；必要时改用国内直连 + basic auth（见 SECURITY.md） |

## 成本

- 云主机：以阿里云官网为准（4C8G 轻量/ECS 通常 ¥150–300/月）。
- Cloudflare Free + Access（≤50 用户）：¥0。
- LLM：DeepSeek 按量 + Embedding 按量；演示流量小，建议在平台侧设余额告警。
