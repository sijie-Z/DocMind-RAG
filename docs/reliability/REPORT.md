# DocMind 可靠性加固报告

- 版本：v1.0
- 日期：2026-10-24
- 代码基线：`origin/develop` @ `653715d2`（本报告所在分支 `docs/reliability-report` 与基线一致）
- 范围：2026-10-23 对抗测试确认的可靠性事故，以及 2026-10-23/24 合并的修复 PR
- 事故数：6（5 项已合并修复，1 项进行中）

## 0. 阅读说明与证据口径

1. **只写已合并的修复。** 本批 9 个 PR（#504/#503/#502/#506/#520/#517/#505/#515/#518）已于 2026-10-24 全部合并；Terminal 事故的修复 PR #489 于 2026-10-23 合并。唯一未合并项是密钥轮换，已标注「进行中 + issue 链接」。
2. **数字均有出处。** 报告中的测试数、压测数与耗时来自 issue/PR 记录或审计文档，并注明来源；作者没有重跑压测。作者复核过的只有 git/文件级事实：提交与 squash sha、密钥历史一致性（字符串比对）、行号、测试文件存在性。
3. **不写未验证的结论。** 例如：不写「50 并发 → 1」，因为真实 50 并发幂等压测未在本机跑过；不写「bcrypt 修复后 p95 < 2s」，因为修复 PR 明确未测得该数据。全部未验证项汇总在附录 B。
4. **不复述任何密钥值。** 本报告只引用审计已记录的哈希前缀与文件位置。

## 1. 结论摘要

| # | 事故 | 严重度 | 状态 | 修复 PR（squash） |
|---|---|---|---|---|
| 1 | 生产密钥泄露在公开 git 历史且仍在用 | P0 | **进行中**（issue #507 open） | 无（未合并） |
| 2 | 数据库迁移不在部署流程，登录 500 | P0 | 已修复 | #503（`a80ae306`） |
| 3 | bcrypt 同步阻塞事件循环 | P1 | 已修复（性能目标未复测） | #502（`ee30ae4c`） |
| 4 | fork 幂等竞态与契约漂移 | P1 | 已修复（真实并发压测未跑） | #518（`653715d2`） |
| 5 | ACP/SDK stdout 污染 + cancel 假取消 | P0 | 已修复（真栈未验） | #520（`0ff446e9`）、#506（`175006de`） |
| 6 | Terminal 无真实 PTY 验证 | P1 | 修复已合并但验证不完整 | #489（`2346bda6`，2026-10-23） |

共性根因（六起事故的共同模式）：**「代码合并 + 单测通过」被当成「能力可用」**。迁移有脚本但没进部署、cancel 有标志位但没等状态收敛、幂等有字段但没有约束、PTY 有实现但没有真 Docker 证据、密钥移出跟踪但没有轮换。

---

## 事故一 · 生产密钥泄露在公开 git 历史中，且仍在使用（P0，进行中）

### 时间线

| 时间 | 事件 |
|---|---|
| 2026-06-01 18:30 +0800 | `fd00b3cc`（`chore: initial stable version`）将 `backend/.env.docker` 提交进公开仓库，`SECRET_KEY` / `JWT_SECRET_KEY` 位于第 4-5 行 |
| 2026-06-05 16:07 +0800 | `37189490`（`security: remove .env.docker from tracking...`）把文件移出跟踪并修 `.gitignore`；历史提交仍然公开 |
| 2026-10-23 | 对抗审计确认：当前 `backend/.env` 与 `fd00b3cc` 中同名字段的哈希一致（SHA-256 前 16 位 `b6ef735f9d2ad6d4` / `019987b2c7d69aeb`）；另发现 `b0324c1:backend/.env.notification` 含 SMTP/飞书/钉钉凭据、`backend/demo.py:19` 硬编码第三方 API Key |
| 2026-10-23 16:51Z | 创建 issue [#507](https://github.com/sijie-Z/DocMind-RAG/issues/507)（P0） |
| 2026-10-24 | 本报告复核：`git show fd00b3cc:backend/.env.docker` 的 `SECRET_KEY=` / `JWT_SECRET_KEY=` 与当前 `backend/.env` **逐字符相同**；`.pre-commit-config.yaml:11` 只有 `detect-private-key`；仓库无 `SECURITY.md` / `.gitleaks.toml` / `docs/security/` |

**状态：进行中。** 本报告提交时没有可引用的修复 PR 与 squash sha。

### 影响

- 任何读过公开历史的人都可以伪造任意用户（含管理员）的 JWT，或解密依赖 `SECRET_KEY` 的产物。这不是「潜在风险」，是可复现的凭据泄露。
- `backend/.env.notification` 历史版本泄露 SMTP / 飞书 / 钉钉凭据；`backend/demo.py:19` 硬编码第三方 LLM API Key（本报告不复述值）。
- 对公开仓库而言，删除文件、修改 `.gitignore`、甚至 rewrite 历史都不能消除已经发生的暴露；**轮换是唯一止损**。
- 上线/投递阻断项：安全审查的第一道门就会被拦下。

### 根因（file:line）

- `backend/.env.docker:4-5`（@ `fd00b3cc`）——密钥值进入公开 git 历史。
- `backend/demo.py:19`——`os.environ.setdefault("DEEPSEEK_API_KEY", "<硬编码值>")`，密钥随源码分发。
- `b0324c1:backend/.env.notification`——通知类凭据进入历史（`SMTP_PASSWORD`、`FEISHU_SECRET`、`DINGTALK_SECRET` 等字段）。
- `.pre-commit-config.yaml:11`——只有 `detect-private-key`（只覆盖私钥文件），没有 gitleaks 之类的值模式扫描。
- 处置缺口：`37189490` 只做了「移出跟踪 + `.gitignore`」，**没有轮换**；泄露值继续在 `backend/.env` / `backend/.env.docker` 中使用。

### 修复（未合并，进行中）

- **进行中**：[#507 轮换公开历史泄露的 SECRET_KEY/JWT_SECRET_KEY + gitleaks 门禁](https://github.com/sijie-Z/DocMind-RAG/issues/507)。
- issue 定义的处置原则：
  1. 轮换所有曾进入 git 历史的凭据（JWT/SECRET、数据库、Redis、MinIO、SMTP、飞书/钉钉、LLM Key），并验证旧凭据失效；
  2. 加 gitleaks 门禁（pre-commit + CI），首次运行以 baseline 登记已轮换的旧指纹；
  3. 新增 `SECURITY.md` 与 `docs/security/CREDENTIAL_ROTATION.md`（只写流程与日期，不写值）；
  4. **不 rewrite 历史、不 force-push**（公开 fork 可能已存在，rewrite 不能消除暴露）。

### 验证命令（修复后验收，本报告未执行）

```bash
# 1) 历史值与当前值必须不同（当前状态应为「仍相同」，即轮换未完成）
cd backend
git show fd00b3cc:backend/.env.docker | grep '^SECRET_KEY=' > ../_scratch/hist.env
grep '^SECRET_KEY=' .env > ../_scratch/cur.env
diff ../_scratch/hist.env ../_scratch/cur.env && echo "仍相同：轮换未完成" || echo "已轮换"

# 2) 旧 JWT 必须失效（期望 401）
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer <旧 JWT>" \
  http://localhost:8000/api/v1/users/me

# 3) 新提交含密钥时 gitleaks 必须拦截
gitleaks detect --source . --no-git --redact   # 期望仅历史已登记指纹
```

上述命令依赖轮换操作与 gitleaks 安装；本报告未执行（轮换涉及部署环境，需用户授权）。

### 教训

1. 密钥进 git 就是永久泄露；「移出跟踪/改 `.gitignore`」不是修复，**轮换才是**。
2. 检测门禁必须覆盖值模式（gitleaks），`detect-private-key` 只防私钥文件，防不住 `.env` 里的 64 位密钥。
3. 第一次提交就要做密钥扫描；事后补门禁只能防新增，不能止损存量。
4. 安全事件要有 `SECURITY.md` + 轮换记录，才能把「知道泄露」变成「可审计的处置」。

---

## 事故二 · 数据库迁移不在部署流程，新环境登录 500（P0）

### 时间线

| 时间 | 事件 |
|---|---|
| 2026-10-23 12:43Z | 对抗验收 `develop @ 04e20e4d`：代码在 `014_api_keys`，真实 MySQL 停在 `011_organization_settings`；`POST /api/v1/auth/login/json` → 500，日志 `Unknown column 'chat_sessions.parent_session_id' in 'field list'`；创建 issue [#495](https://github.com/sijie-Z/DocMind-RAG/issues/495) |
| 2026-10-24 03:56:46Z | PR [#503](https://github.com/sijie-Z/DocMind-RAG/pull/503) 合并，squash `a80ae3063de26ac3558f981edf2f9796cae40602` |

### 影响

- 任何按 README / compose 标准方式部署的新环境，第一步登录就 500。这不是环境问题，是**发布工程缺失**：迁移脚本存在，但没有任何部署路径执行它。
- 对「企业级、可部署」的叙事是直接反证。

### 根因（file:line，fix 前 @ 04e20e4d）

- `backend/Dockerfile:92`——`CMD ["python", "-m", "uvicorn", "app.main:app", ...]`，没有 `alembic upgrade head`。
- `backend/docker-compose.yml:10,63`——backend / worker 直接 `python -m app --profile ...`，没有迁移步骤。
- `Makefile:111`——唯一迁移入口是手动 `make migrate`（`alembic upgrade head`），README/runbook 未把它写成必做步骤。
- 结果：合并了 3 个新迁移（012/013/014），却没有部署路径会自动应用。

### 修复（已合并）

- PR [#503](https://github.com/sijie-Z/DocMind-RAG/pull/503)，squash `a80ae3063de26ac3558f981edf2f9796cae40602`。
- **compose 一次性 migrate 服务**：`backend/docker-compose.yml:9-12`，`command: ["alembic", "upgrade", "head"]`；backend/worker 通过 `depends_on: migrate: condition: service_completed_successfully` 等待（`:51-52`、`:102-103`）。迁移只在一个服务里执行一次，避免多副本竞争。
- **统一入口**：`backend/Dockerfile:95` `CMD ["python", "-m", "app", "--profile", "web"]`。
- **启动 schema guard**：`backend/app/bootstrap.py:175` `enforce_schema_guard()`，在 lifespan 初始化前校验 `alembic_version` 是否在 heads 集合：
  - `SCHEMA_GUARD=true`（默认）：落后时 critical 日志 + exit 2，错误含 current/head/修复命令；
  - `AUTO_MIGRATE=true`：仅开发环境兜底执行一次 upgrade；
  - `SCHEMA_GUARD=false`：显式逃生门。
- **K8s**：新增 `deploy/k8s/09-migrate-job.yaml`；backend/worker 增加 `schema-check` initContainer。
- **文档**：README / SETUP / deploy README 写入迁移是启动前置步骤。

### 验证命令

```bash
cd backend
python -m pytest tests/unit/test_schema_guard.py -q      # PR #503 记录：14 passed
python -m alembic current && python -m alembic heads      # 期望 current 与 heads 一致
python scripts/check_schema.py
docker compose config | grep -A3 'migrate:'               # 期望 backend/worker 依赖 migrate

# 干净栈端到端 smoke（未在本机执行）：
docker compose up -d --build
curl -s -o /dev/null -w '%{http_code}\n' -X POST http://localhost:8000/api/v1/auth/login/json \
  -H 'Content-Type: application/json' -d '{"username":"<user>","password":"<pass>"}'   # 期望 200
```

**未验证（PR #503 如实记录）**：Docker daemon 未运行，未执行真实 `compose up` 的 migrate→backend/worker 链路；无真实 MySQL，011→014 与 downgrade 往返由 CI `migration-mysql` job 覆盖（本 PR 未修改该 job）；未在真实 K8s 集群 apply。

### 教训

1. 「迁移脚本存在」≠「部署会执行迁移」；迁移必须是发布流程的显式步骤。
2. 启动时 fail-closed 优于用户登录时 500：错误提前暴露，且错误信息直接给出修复命令。
3. 迁移只能有一个执行点（一次性 migrate 服务 / K8s Job）；多副本各自 `alembic upgrade head` 会竞争。
4. 部署类修复的最终验收是「干净环境 → 登录 200」的端到端 smoke；这条本机仍未跑过，是已知缺口。

---

## 事故三 · bcrypt 同步阻塞事件循环，登录 burst 拖死全站（P1）

### 时间线

| 时间 | 事件 |
|---|---|
| 2026-10-23 12:45Z | issue [#500](https://github.com/sijie-Z/DocMind-RAG/issues/500)：8010 单进程压测（50 并发正确登录）记录 p50 12421ms / p95 13432ms；同一时刻 `/health` p95 4692ms |
| 2026-10-23 16:07Z | 第一版 PR #501 关闭、**未合并**（`merged=false`，红队记录在案） |
| 2026-10-24 03:57:36Z | 重做版 PR [#502](https://github.com/sijie-Z/DocMind-RAG/pull/502) 合并，squash `ee30ae4cc95656f0c046d1a49f362d29893c510a` |

### 影响

- bcrypt 是 CPU 密集的同步调用，放在 async handler 里会阻塞整个事件循环；登录 burst 期间 `/health`、会话列表等所有请求一起排队。
- 攻击者只要并发打登录，就能低成本拖慢全站——这是可用性问题，不只是登录慢。

### 根因（file:line，fix 前 @ 04e20e4d）

- `backend/app/services/auth_service.py:293`——`async def authenticate_user` 内直接调用 `self.verify_password(...)`。
- `backend/app/services/auth_service.py:316-324`——`verify_password` 同步执行 `bcrypt.checkpw`。
- `backend/app/services/auth_service.py:287`——用户不存在时对 `_DUMMY_HASH` 再做一次 bcrypt（时序枚举防护），同样同步。
- `backend/app/services/auth_service.py:369-370`——注册路径 `bcrypt.gensalt()/hashpw()` 在 async 函数里同步执行；改密路径同理（`:538`）。

### 修复（已合并）

- PR [#502](https://github.com/sijie-Z/DocMind-RAG/pull/502)，squash `ee30ae4cc95656f0c046d1a49f362d29893c510a`。
- 四个调用点改为 `await asyncio.to_thread(...)`：
  - dummy 校验（现 `auth_service.py:289`）、登录校验（`:295`）、注册哈希（`:481`）、改密哈希（`:542`）。
- 不降低 bcrypt cost，不新建线程池（使用默认 `min(32, cpu+4)`），失败计数/锁定/时序枚举防护语义不变。
- 新增回归：`backend/tests/unit/test_auth_password_offload.py`。

### 验证命令

```bash
cd backend
python -m pytest tests/unit/test_auth_password_offload.py \
  tests/unit/test_auth_service.py tests/api/test_auth.py -q
# PR #502 记录：4 个文件 59 passed（执行方/监督方两次独立复跑一致）；新增文件单跑 6 passed
# 变异试验：把登录校验改回同步 → 事件循环 tick 用例按预期失败；还原后通过

# 真实压测脚本存在，但修复后未在本机跑过（见附录 B）：
python ../_scratch/audit/stress_round2.py   # 50 并发登录 + /health
```

### 未验证与遗留

- **修复后 50 并发 p95 < 2s、burst 期间 `/health` p95 < 1s 未测得。** PR #502 明确写明「本次开发环境未运行容器/Redis 栈，未测得真实压测数据；本 PR 不声称压测达标」。
- 4 处同类同步 bcrypt 调用仍在其它端点，本 PR 按 issue 范围未改（PR 已记录为后续跟进）：
  - `backend/app/api/v1/endpoints/auth.py:628`（改密时校验旧密码）
  - `backend/app/api/v1/endpoints/users.py:321`（设置新密码）
  - `backend/app/api/v1/endpoints/users.py:1388`（管理员/用户改密校验）
  - `backend/app/api/v1/endpoints/users.py:1392`（改密写入）
- `backend/app/services/api_key_service.py` 的 bcrypt（API Key 哈希/校验，`:43,61,107,123,128`）同样是同步路径，本次未纳入。

### 教训

1. async 处理器里的 CPU 密集调用是**全站延迟**问题，不是单端点问题；修一个登录端点也要检查同类路径。
2. 正确 offload 姿势：`asyncio.to_thread` + 默认线程池；不要在 `to_thread` 外层套短超时（bcrypt 是 CPU 工作，超时会留下线程）。
3. 性能修复必须有前后数字；没有完整依赖栈就不写「已达标」，把目标留到有栈环境验证。
4. 范围控制要写进 PR：明确「还有 4 处同类问题未改」，比假装修完更可信。

---

## 事故四 · Session Fork 幂等竞态与契约漂移（P1）

### 时间线

| 时间 | 事件 |
|---|---|
| 2026-10-23 12:45Z | issue [#499](https://github.com/sijie-Z/DocMind-RAG/issues/499)：同一 `idempotency_key` 串行两次得到两个子会话（实测 `30b78b9b` / `90e7d123`）；路由/响应/边界共 6 处契约偏离 |
| 2026-10-24 04:32:33Z | PR [#518](https://github.com/sijie-Z/DocMind-RAG/pull/518) 合并，squash `653715d2896fe00d09d15df618fdc64f681d30f2`；迁移 `016_fork_seq_idempotency` |

### 影响

- 客户端重试/并发创建重复子会话与重复消息；body 幂等键被静默忽略，接入方按契约写必挂。
- 契约路径 `/api/v1/agent/sessions/{id}/fork` 404，响应字段与契约不一致。
- 子会话历史只加载 20 条，长会话 fork 后上下文丢失。

### 根因（file:line，fix 前 @ 04e20e4d）

- `backend/app/services/session_fork_service.py:368-390`——`_find_idempotent_child` 先查后插（check-then-insert）：并发请求都查不到已有子会话，于是都插入；没有 DB 唯一约束兜底。
- `backend/app/services/session_fork_service.py:26-30`——幂等键只写进子会话 `settings.fork`，是「约定」而不是「约束」。
- `backend/app/api/v1/endpoints/sessions.py:40-57`——响应字段 `id` / `copied_message_count` / 嵌套 `checkpoint.task_id`，与契约 `session_id` / `messages_copied` / `checkpoint_task_id` 不一致。
- `backend/app/api/v1/router.py`（issue #499 记录为 `:76-78`）——fork 路由挂在 `/api/v1/sessions`，契约路径 `/api/v1/agent/sessions/...` 404。
- `backend/app/services/chat_seq.py:36-52`——`MAX(seq) + FOR UPDATE` 分配序号，`chat_sessions.next_message_seq` 列不存在。
- `backend/app/api/v1/endpoints/agent.py:1737-1742`（issue 记录）——`_load_session_messages` 固定 `.limit(20)`，`history_limit` 未实现。

### 修复（已合并）

- PR [#518](https://github.com/sijie-Z/DocMind-RAG/pull/518)，squash `653715d2896fe00d09d15df618fdc64f681d30f2`。
- **迁移 016**（`backend/alembic/versions/016_fork_seq_idempotency.py`）：
  - `chat_sessions.next_message_seq INTEGER NOT NULL DEFAULT 0`，按 `MAX(chat_messages.seq)` 回填；
  - `chat_sessions.idempotency_key` + 唯一约束 `uq_chat_sessions_user_idempotency_key (user_id, idempotency_key)`。
- **幂等双保险**（`session_fork_service.py:496-541,660-684`）：Redis `SET NX EX 86400` 抢占（值=子会话 id）+ DB 唯一约束最终兜底；冲突时读回已有子会话，无法读回时返回 409。
- **路由**：规范 `/api/v1/sessions/{id}/fork|lineage`；兼容别名 `/api/v1/agent/sessions/{id}/fork|lineage`（`include_in_schema=False` + `Deprecation: true` 头，`sessions.py:113`）。
- **响应**：新增 `session_id` / `messages_copied` / `checkpoint_task_id`（`sessions.py:88-104`），旧字段保留一版。
- **边界与状态**：`seq <= boundary`（含边界消息）、边界消息 pending meta → 400、配额超限 → 400、SYSTEM 边界允许、checkpoint 克隆任务状态 PENDING。
- **history_limit**：fork 子会话写 200，普通会话默认 20。

### 验证命令

```bash
cd backend
python -m pytest tests/unit/test_session_fork.py tests/api/test_session_fork_api.py -q
# PR #518 记录：定向 3 文件 68 passed；评审独立复跑 65 passed
python -m alembic upgrade head && python -m alembic downgrade -1 && python -m alembic upgrade head
python -m alembic heads    # 期望单 head：016_fork_seq_idempotency
# 变异试验（PR 记录）：去掉 DB 唯一约束 → 3 条幂等用例红；Redis nx=False → IN_PROGRESS 用例红
```

### 未验证与说明

- **真实并发同键压测未在本机执行。** 本报告不写「50 并发 → 1」这类结论；PR 验证是单测 + 故障注入（变异试验），不是真实并发压测。
- MySQL 真库迁移未执行（PR 如实记录：本机 MySQL 是旧 schema，未动以免干扰并行任务；需部署时先 `alembic upgrade head`，CI migration job 覆盖）。
- 全量回归中 `test_api_surface_no_5xx` 因本机 MySQL 旧 schema 缺列未通过，PR 注明由 CI 覆盖。

### 教训

1. 幂等必须有单一事实源：**DB 唯一约束是最终防线**，Redis 抢锁只是优化；只有应用层「先查」在并发下必然失效。
2. 契约修复要同时给规范路径、兼容别名与 Deprecation 窗口，不能两套都「正式」。
3. 新增序号类列必须回填历史值（`MAX(seq)`），否则默认 0 会重号。
4. 并发类修复的验收不能只看单测通过；真实并发压测是独立的、尚未完成的验证项。

---

## 事故五 · ACP/SDK stdout 被日志污染 + cancel 假取消（P0）

### 时间线

| 时间 | 事件 |
|---|---|
| 2026-10-23 12:36Z | issue [#493](https://github.com/sijie-Z/DocMind-RAG/issues/493)（SDK stdio）与 [#494](https://github.com/sijie-Z/DocMind-RAG/issues/494)（ACP）创建：真进程 stdout 出现 691/306/36 行非 JSON，首行 `INFO sqlalchemy.engine.Engine SELECT DATABASE()`；ACP 错误路径 stdout 有 12587 字节 SQL 日志；`session/cancel` 后 DB 任务最终 COMPLETED；`pytest tests/api/test_acp_stdio.py` 实测 20 passed / 2 failed |
| 2026-10-24 03:58:36Z | PR [#506](https://github.com/sijie-Z/DocMind-RAG/pull/506) 合并（SDK stdio），squash `175006ded1d0bef721c3f32e9a2274d71b422de5` |
| 2026-10-24 03:59:35Z | PR [#520](https://github.com/sijie-Z/DocMind-RAG/pull/520) 合并（ACP），squash `0ff446e91850d89cf40a0cb44e03df70e4006817` |

### 影响

- ACP/SDK 协议要求 stdout 只含协议帧（NDJSON/JSON）；SQL 日志混入会让客户端无法解析，协议入口不可用。
- cancel 假取消：任务继续执行到 COMPLETED，LLM 成本与副作用不可控；客户端以为已停止。
- 「CI 全绿」与实测 20 passed / 2 failed 矛盾，验收叙事失真。

### 根因（file:line，fix 前 @ 04e20e4d）

- `backend/app/core/database.py:40`——`echo=settings.DEBUG`；仓库 `.env` 的 `DEBUG=true`，SQLAlchemy engine 向 stdout 打印 SQL。
- `backend/app/entrypoints/acp.py:85-88`——重定向 stdout 之前 `from app.rpc.server import ...`（import 即创建 engine）；身份校验失败路径 stdout 写出 12587 字节 SQL 日志。
- `backend/app/acp/server.py:862-889`——`_cancel_session` 调用 `checkpoint.request_cancel(task_id)` 但不等待任务收敛；`_drive` 继续消费真实事件流，仅靠 `cancel_event` 丢弃 update → DB 任务最终 COMPLETED。
- `backend/app/entrypoints/sdk.py:91-99`——stdio 进程从未 `init_redis()`；`session/cancel` 返回 `{"cancelled": false}`；同一 `clientMessageId` 两次 prompt 产生两条任务。

### 修复（已合并）

**PR [#506](https://github.com/sijie-Z/DocMind-RAG/pull/506) / `175006ded1d0bef721c3f32e9a2274d71b422de5`（SDK stdio）**

- 新增 `backend/app/core/stdio.py:75` `configure_stdio_stdout()`：保存协议 writer、把 `sys.stdout` 指向 stderr、迁移全部 handler；入口在任何 app import 之前调用（`entrypoints/sdk.py:133-135`）。
- 新增 `SQL_ECHO` 设置（默认 false，`backend/app/core/config/base.py:44`），不再用 `DEBUG` 控制 SQL 输出。
- 严格初始化：stdio 启动先 `init_redis()` / `init_db()`，失败写 stderr 并 exit 2，stdout 保持 0 字节。
- cancel 等待 DB 收敛（≤5s），Redis 不可用时幂等返回 `-32603`，不再静默降级。
- shutdown 加固：Windows 下 detach + `CancelSynchronousIo`，连续 5 次 exit 0。

**PR [#520](https://github.com/sijie-Z/DocMind-RAG/pull/520) / `0ff446e91850d89cf40a0cb44e03df70e4006817`（ACP）**

- `cancel_task_and_wait`（`backend/app/acp/server.py:1102`）：JSON-RPC `session/cancel` 等待 DB 任务状态收敛为 `CANCELLED`（≤5s）；prompt 返回 `stopReason:"cancelled"`；取消后不再发送迟到 update。
- `backend/app/entrypoints/acp.py:112-114` 在任何 app import 前 `configure_stdio_stdout()`；所有启动/错误路径只写 stderr。
- 取消失败/超时/状态竞争时如实返回 `-32603`/`-32004`，不假报成功。

### 验证命令

```bash
cd backend
python -m pytest tests/api/test_acp_stdio.py tests/unit/test_acp_mapping.py -q
# PR #520 记录：72 passed（基线 58）；两条原失败用例均 PASSED
python -m pytest tests/api/test_sdk_stdio.py tests/unit/test_rpc_dispatcher.py -q
# PR #506 记录：定向 31 passed；ACP + api-key 回归 28 passed

# 错误路径 stdout 必须为 0 字节
python -m app --profile acp --org-id 2 --user-id 1 > ../_scratch/acp.out 2> ../_scratch/acp.err
echo "exit:$?"                 # 期望 2
wc -c < ../_scratch/acp.out    # 期望 0
# PR #520 记录：在 SQL_ECHO=true, DEBUG=true 下 stdout == b""，stderr 含 22 行 SQL 日志
```

### 未验证与已知边界

- 真实 Redis / MySQL / LLM 未运行：用 FakeRedis、sqlite、FakeAgentService 覆盖；真实 PER/LLM 取消检查点留待有栈环境。
- POSIX 无 `CancelSynchronousIo`，采用 detach + daemon 兜底；Windows 路径已验证，POSIX 未原生实测。
- 已知边界（PR #520 记录）：DB 读故障时保守报 cancelled（warning 留痕）；Redis `setex` 半途的可见性歧义；跨 prompt 旧 cancel handler 竞态，建议后续用世代号收紧。

### 教训

1. **stdout 是协议契约**：任何日志（包括 ORM echo）都不能进 stdout；重定向必须早于任何会产生副作用的 import。
2. cancel 必须是「等待状态收敛」的动作，不是置旗；假成功比失败更危险。
3. 负向测试（stdout 0 字节、DB 状态 CANCELLED）必须进 CI；否则「20 passed / 2 failed」会被流水账写成「全绿」。
4. 同一根因（stdout 污染）要跨入口统一修：抽到 `core/stdio.py`，而不是每个入口各打补丁。

---

## 事故六 · Terminal 宣称完成但从未在真 Docker 验证（P1）

### 时间线

| 时间 | 事件 |
|---|---|
| 2026-10-22 10:13Z | issue [#477](https://github.com/sijie-Z/DocMind-RAG/issues/477) 创建：Persistent Terminal（容器持久 PTY + WebSocket + xterm）；明确要求「Docker daemon 连不上时集成测试显式 skip，不得伪装通过」 |
| 2026-10-23 08:54:20Z | PR [#489](https://github.com/sijie-Z/DocMind-RAG/pull/489) 合并，squash `2346bda67a45081470d6156fdd0a7f9bb6158153`；默认 `TERMINAL_ENABLED=false` |
| 2026-10-23/24 | 对抗审计（`develop @ 04e20e4d`，已包含 #489）：Docker Desktop npipe 不可用（`docker version` 失败仍返回退出码 0）；Docker 集成测试 5 skipped；e2e 未执行；`TERMINAL_ENABLED=false` 时存量容器不清理 |

### 影响

- 「Terminal 完成」实际只等于「代码 + 单测完成」：容器 exec PTY、隔离参数、断线重连、多 worker owner 语义都没有真实环境证据。
- 若按「已完成」对外叙述，属于验收标准错位；默认关闭限制了风险面，但不能替代验证。

### 根因（file:line，证据缺口而非功能缺失）

- `backend/tests/integration/test_terminal_docker.py:17-21`——仅当 `DOCKER_AVAILABLE=1` 时运行，Docker 不可达时显式 skip。
- `backend/app/services/terminal_service.py:512-516`——`startup()` 在 `TERMINAL_ENABLED=false` 时直接 return，reaper 不启动，存量容器不会被清理（PR #489 已知风险 #2）。
- 配额 / owner 计数为进程内状态（审计 D11），多 worker 会放大为 3×N。
- 本机 Docker Desktop npipe 不可用，且 `docker version` 退出码不可信 → 无法在本机完成真实验证。

### 修复（已合并，验证不完整）

- PR [#489](https://github.com/sijie-Z/DocMind-RAG/pull/489)，squash `2346bda67a45081470d6156fdd0a7f9bb6158153`。
- 实现内容：每终端独立容器（非 root `65534:65534`、`cap-drop ALL`、`no-new-privileges`、`network none`、cpu/mem/pids 限额、无宿主挂载）+ `docker exec` PTY（`stdin=True, tty=True` + `exec_resize`）；WebSocket 一次性票据 + Origin 校验 + ring buffer 重放；六个 Agent 工具默认 high 风险并强制审批；默认关闭；`docs/decisions/ADR-006-terminal-pty.md`。
- **验证状态：单测覆盖、真 Docker 未验证。**
  - PR #489 记录：Terminal 测试 58 passed（manager 19 + API/WS/工具/审批 39）；
  - Docker 集成：**5 skipped**（daemon 不可用，按 issue #477 要求如实 skip，未伪装通过）；
  - Playwright e2e：仅收集、未执行（浏览器二进制不可下载）。

### 验证命令

```bash
cd backend
python -m pytest tests/unit/test_terminal_manager.py tests/api/test_terminal_api.py -q
# PR #489 记录：58 passed

# 真 Docker 验证（本机未执行；需 Docker daemon）：
DOCKER_AVAILABLE=1 python -m pytest tests/integration/test_terminal_docker.py -q
# 期望：真 Docker 下 echo/ls 可用、退出码正确、隔离参数生效；当前记录为 5 skipped

cd ../frontend
npx playwright test e2e/agent-terminal.spec.ts
# PR #489 未执行；期望：打开/输入/resize/重连/关闭
```

### 未验证项与已知风险

- 真 Docker exec PTY 路径、容器隔离参数（仅 skipped 集成测试覆盖）。
- 前端 e2e 未执行。
- 多 worker owner/配额语义（进程内计数）。
- `TERMINAL_ENABLED=false` 时存量容器需手动清理：`docker rm -f $(docker ps -aq --filter label=docmind.terminal)`。
- 票据轮换是 read/delete/write（非原子），并发签发票据存在 ≤60s 双有效窗口。

### 教训

1. 「合并 + 单测通过」≠「能力可用」；环境门控的集成测试必须如实报 skipped，并在有环境的主机上补跑。
2. 退出码也会撒谎（`docker version` 失败仍返回 0），不能只信退出码，必须看真实连接结果。
3. 默认关闭 + ADR + 已知风险清单是正确的风险控制，但不能替代真实验证。
4. 有 Docker 的 CI/主机应把 `DOCKER_AVAILABLE=1` 集成测试设为发布门禁，而不是永久 skip。

---

## 附录 A · 本批已合并 PR 一览

| PR | 主题 | squash sha | 合并时间（UTC） | 对应 issue | 本报告位置 |
|---|---|---|---|---|---|
| [#504](https://github.com/sijie-Z/DocMind-RAG/pull/504) | 登录校验/错误提示收敛 + MCP 异常类兼容 | `f67ef9f80b443b797cdff39a74172a41b5950d23` | 2026-10-24 03:55:47 | #492 | 范围外（UX） |
| [#503](https://github.com/sijie-Z/DocMind-RAG/pull/503) | 数据库迁移纳入启动与发布流程 | `a80ae3063de26ac3558f981edf2f9796cae40602` | 2026-10-24 03:56:46 | #495 | 事故二 |
| [#502](https://github.com/sijie-Z/DocMind-RAG/pull/502) | bcrypt 校验/哈希移入线程池 | `ee30ae4cc95656f0c046d1a49f362d29893c510a` | 2026-10-24 03:57:36 | #500 | 事故三 |
| [#506](https://github.com/sijie-Z/DocMind-RAG/pull/506) | SDK stdio 运行时收口 | `175006ded1d0bef721c3f32e9a2274d71b422de5` | 2026-10-24 03:58:36 | #493 | 事故五 |
| [#520](https://github.com/sijie-Z/DocMind-RAG/pull/520) | ACP cancel 真取消 + stdout 零泄漏 | `0ff446e91850d89cf40a0cb44e03df70e4006817` | 2026-10-24 03:59:35 | #494 | 事故五 |
| [#517](https://github.com/sijie-Z/DocMind-RAG/pull/517) | API Key scope 强制 | `00dbeaa75ce953256f39d6c73e83b62b2f368710` | 2026-10-24 04:00:33 | #496 | 范围外（安全） |
| [#505](https://github.com/sijie-Z/DocMind-RAG/pull/505) | Webhook 运行时加固 | `1d4d40607c26aafd9c35688ce54d08f39e054f5f` | 2026-10-24 04:01:30 | #497 | 范围外（Webhook） |
| [#515](https://github.com/sijie-Z/DocMind-RAG/pull/515) | Commands 契约/Redis 锁/幂等 | `963aeaa1bdfa2e805d6ff03711177c38cf6451d5` | 2026-10-24 04:02:31 | #498 | 范围外（Commands） |
| [#518](https://github.com/sijie-Z/DocMind-RAG/pull/518) | Session Fork 契约收口 | `653715d2896fe00d09d15df618fdc64f681d30f2` | 2026-10-24 04:32:33 | #499 | 事故四 |
| [#489](https://github.com/sijie-Z/DocMind-RAG/pull/489) | 容器持久 PTY + WebSocket 票据 + xterm 面板 | `2346bda67a45081470d6156fdd0a7f9bb6158153` | 2026-10-23 08:54:20 | #477 | 事故六（上一批） |

相关未合并/已关闭：PR #501（bcrypt 初版，2026-10-23 关闭未合并，`merged=false`）；issue #507（密钥轮换，open）。

## 附录 B · 未验证项汇总（不得写成已完成）

1. **密钥轮换**：未执行；issue #507 open。当前历史值与 `.env` 值仍逐字符相同（本报告复核）。
2. **bcrypt 修复后的 50 并发压测**：未在本机跑；PR #502 明确不声称 p95 < 2s / `/health` p95 < 1s 达标。
3. **fork 真实并发同键压测**：未跑；本报告不写「N 并发 → 1」。PR 验证为单测 + 变异试验。
4. **MySQL 真库迁移 011→016**：PR #518 未执行（本机旧 schema）；由 CI migration job 覆盖，但报告作者未复核该 job 的最新结果。
5. **干净 `docker compose up` 的 migrate→backend→登录 200 链路**：PR #503 未执行（Docker daemon 未运行）。
6. **真实 K8s apply**：未执行。
7. **真实 Redis/MySQL/LLM 下的 SDK/ACP cancel**：未执行（FakeRedis/sqlite/FakeAgentService）。
8. **Terminal 真 Docker exec PTY、隔离参数、e2e**：未执行（5 skipped / e2e 未跑）。
9. **bcrypt 其余 4 处同步调用与 API Key bcrypt**：本批未修，属于后续工作。
10. **POSIX 下 stdio shutdown**：仅 Windows 路径实测；POSIX 为 detach + daemon 兜底。

## 附录 C · 引用与复核记录

**审计输入**
- `_scratch/audit/DARK_ASSESSMENT.md`（D1 密钥、D5 迁移、D6 ACP/SDK、D7 bcrypt/fork、D11 Terminal）
- `_scratch/audit/REDTEAM_FINDINGS.md`（PR #501 closed 记录、简历口径修正）
- `_scratch/audit/STRATEGY.md`（事故到 issue 的映射：#492-#500、#507）

**Issue**
- [#492](https://github.com/sijie-Z/DocMind-RAG/issues/492) UX/MCP、[#493](https://github.com/sijie-Z/DocMind-RAG/issues/493) SDK stdio、[#494](https://github.com/sijie-Z/DocMind-RAG/issues/494) ACP、[#495](https://github.com/sijie-Z/DocMind-RAG/issues/495) 迁移、[#496](https://github.com/sijie-Z/DocMind-RAG/issues/496) API Key scope、[#497](https://github.com/sijie-Z/DocMind-RAG/issues/497) Webhook、[#498](https://github.com/sijie-Z/DocMind-RAG/issues/498) Commands、[#499](https://github.com/sijie-Z/DocMind-RAG/issues/499) Fork、[#500](https://github.com/sijie-Z/DocMind-RAG/issues/500) bcrypt
- [#507](https://github.com/sijie-Z/DocMind-RAG/issues/507) 密钥轮换（open）、[#477](https://github.com/sijie-Z/DocMind-RAG/issues/477) Terminal（closed by #489）

**关键提交**
- `fd00b3cc`（2026-06-01，泄露引入）、`37189490`（2026-06-05，移出跟踪但未轮换）
- `2346bda6`、`a80ae306`、`ee30ae4c`、`175006de`、`0ff446e9`、`00dbeaa7`、`1d4d4060`、`963aeaa1`、`653715d2`

**报告作者复核过的 git/文件级事实（2026-10-24，基线 `653715d2`）**
- `git show fd00b3cc:backend/.env.docker` 的 `SECRET_KEY` / `JWT_SECRET_KEY` 与当前 `backend/.env` 逐字符相同（输出 `SAME`）。
- `backend/demo.py:19` 存在硬编码第三方 API Key；`b0324c1:backend/.env.notification` 含 SMTP/飞书/钉钉字段。
- `.pre-commit-config.yaml:11` 仅 `detect-private-key`；无 `SECURITY.md` / `.gitleaks.toml` / `docs/security/`。
- 9 个 squash 提交均在 `653715d2` 祖先链上；PR #489 与 #501 状态通过只读 API 复核（#489 merged，#501 closed/merged=false）。
- 本报告引用的测试文件在 HEAD 均存在：`test_schema_guard.py`、`test_auth_password_offload.py`、`test_session_fork.py`、`test_session_fork_api.py`、`test_acp_stdio.py`、`test_acp_mapping.py`、`test_sdk_stdio.py`、`test_rpc_dispatcher.py`、`test_terminal_manager.py`、`test_terminal_api.py`、`test_terminal_docker.py`。
- 本报告未重跑任何压测或集成测试；测试数与耗时均转引 issue/PR 记录。

**修订记录**
- v1.0（2026-10-24）：首版；六个事故；密钥轮换标注进行中；Terminal 标注「单测覆盖、真 Docker 未验证」。
