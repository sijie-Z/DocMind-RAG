# ADR-006：持久终端（容器 exec PTY + 一次性票据 WebSocket）

- 状态：已采纳（issue #477，2026-10-23）
- 依赖：issue #479（配置分层 / bundle 路由）已合入；本 ADR 编号 006（003/004/005 已被 #479/#474/#478 占用）

## 结论（先读这段）

1. **每终端一个专用容器 + `docker exec` PTY**，而不是一次性 `docker run -it` + attach。
2. backend 只在**显式 overlay**（`backend/docker-compose.terminal.yml`）下挂载
   `/var/run/docker.sock`；默认 compose 不挂、`TERMINAL_ENABLED=false`。
3. WebSocket 鉴权用 **一次性票据**（Redis `GETDEL`），票据只走
   `Sec-WebSocket-Protocol: auth.<ticket>` 子协议，绝不进 URL；握手失败统一
   accept 后 `close(1008, "policy_violation")`，不发送任何终端数据。
4. 配额：每用户 3、每组织 10；空闲 900s、最长 3600s；ring buffer 256KB
   （丢最旧字节 + 累计 `dropped_total`）。
5. Agent 六工具（`terminal_open/send/read/signal/close/list`）全部
   `risk_level="high"`；`TERMINAL_REQUIRE_APPROVAL=true`（默认）时 executor 对
   `terminal_*` **强制审批**（即使 `approval_mode="never"`），无审批通道时
   fail-closed（物化为 `approval_denied`，绝不静默执行）。
6. 同一终端同一时刻只允许一个 writer：并发写入立即 `terminal_busy`，不排队。

## 容器模型（issue §5.2）

```text
docker run -d --rm \
  --name docmind-term-<terminal_id> \
  --label docmind.terminal=<terminal_id> --label docmind.user=<user_id> \
  --network none --cpus 1 --memory 512m --pids-limit 128 \
  --cap-drop ALL --security-opt no-new-privileges \
  --user 65534:65534 -w /tmp <IMAGE> sleep infinity
# 之后：
exec_id = client.api.exec_create(container.id, cmd=["/bin/sh"], stdin=True, tty=True, user="65534:65534")
sock    = client.api.exec_start(exec_id, socket=True)
```

- 容器只承载 PTY 生命周期（`sleep infinity`），命令都跑在 exec 的 shell 里；
  关闭 = `kill` + `remove(force=True)`（清理失败只记日志）。
- 镜像：`TERMINAL_CONTAINER_IMAGE` 覆盖，缺省沿用 `SANDBOX_DOCKER_IMAGE`
  （默认 `python:3.11-slim`）。覆盖时必须固定 tag/digest（禁止 `latest`）。
- **实测注记（docker SDK 7.2.0）**：高层 `Container.exec_create/exec_start/
  exec_resize/exec_inspect` 已被 SDK 移除，必须走 `client.api.*`；
  `exec_start(socket=True)` 在 POSIX 返回 `socket.SocketIO`、Windows npipe 返回
  `NpipeSocket`，读写分别用 `docker.utils.socket.read` 与 `sendall`/`write` 兼容。
  所有阻塞调用包在 `asyncio.to_thread` 里。

## 鉴权：一次性票据 + Origin

1. `POST /api/v1/terminal/sessions/{id}/ticket` 签发
   `secrets.token_urlsafe(32)`；Redis `SET terminal:ticket:<ticket> = "{user_id}:{terminal_id}"
   EX 60`，并用 `terminal:ticket:by_terminal:<id>` 记录当前票据——签发新票时先
   删除旧票（旧票据立即失效）。
2. WS 握手：`Sec-WebSocket-Protocol: auth.<ticket>` → Redis **GETDEL**（一次性）
   → 值必须与路径中的 terminal_id 一致 → Origin 必须在 `ALLOWED_ORIGINS` →
   终端必须属于票据中的 user_id 且未被回收。
3. 任一失败：`accept()` 后 `close(code=1008, reason="policy_violation")`。
   先校验 Origin（无副作用）再 GETDEL，避免「非法 Origin 也烧掉票据」。
4. 为什么不用 JWT：票据 TTL 60s、单次使用、绑定 (user, terminal)；即使泄露，
   攻击窗口极小且无法重放。票据走子协议的原因与 #258 相同：URL 会进访问日志。
5. 重连：客户端每次重连都重新领票；服务端补发 ring buffer（最近 256KB）后
   进入实时流。前端在重连成功时先清屏再接收补发，避免旧内容重复堆叠。

## 配额、生命周期与多 worker

| 项 | 默认 | 说明 |
|---|---|---|
| `TERMINAL_MAX_PER_USER` | 3 | 超限 → HTTP 409 `CONFLICT`；工具 → `rate_limited` |
| `TERMINAL_MAX_PER_ORG` | 10 | 组织维度同上 |
| `TERMINAL_IDLE_TIMEOUT_SECONDS` | 900 | 无任何 I/O 15 分钟后回收 |
| `TERMINAL_MAX_LIFETIME_SECONDS` | 3600 | 无论活动与否，最长 1 小时 |
| `TERMINAL_RING_BUFFER_BYTES` | 262144 | 溢出丢最旧字节，`dropped_total` 累计 |
| `TERMINAL_TICKET_TTL_SECONDS` | 60 | 一次性票据有效期 |
| `TERMINAL_REQUIRE_APPROVAL` | true | terminal_* 强制人工审批 |

- **owner 记录**：创建时 `SET terminal:owner:<id> = <worker_id> EX 3600`；
  HTTP/WS 访问若会话不在本进程且 owner 是其他 worker → 409
  `terminal_owned_elsewhere`（WS 走 1008）。生产多 worker 需 sticky 路由。
- **worker 心跳**：每个 worker 每 30s 刷新 `terminal:worker:<id>`（TTL 90s）。
  启动时与每 30s 的回收循环按 label `docmind.terminal` 扫描容器：
  - 本 worker 已无该会话（进程重启）→ 删除；
  - owner 心跳过期 → owner 已死，删除；
  - owner 键不存在（如 Redis 丢数据）→ 仅在容器年龄 ≥ 最大寿命时删除
    （宁可多留一个容器，也不误杀其他 worker 的活会话）；
  - Redis 不可用 → 本轮不删除任何容器。
- 回收循环只挂在 **web** 入口（`bootstrap.py` lifespan，`get_active_entrypoint()=="web"`），
  worker 入口不跑。

## Docker socket 威胁模型（必须显式接受）

挂载 `/var/run/docker.sock` = 把宿主 root 等价权限交给 backend 容器内的任何代码
（可创建特权容器、挂载宿主路径、读取宿主文件系统）。本设计的缓解：

1. 默认关闭：基础 compose 不挂 socket，`TERMINAL_ENABLED=false`；
2. 只有显式叠加 `docker-compose.terminal.yml` 才启用（文档里写明威胁）；
3. 终端容器自身最小权限（非 root、cap-drop ALL、no-new-privileges、network none、
   pids/内存/CPU 限额、无宿主挂载）；
4. 终端 API 只对已认证用户开放，配额与 TTL 限制资源占用；
5. 已知边界：backend 一旦被攻破，上述隔离无法阻止宿主逃逸——需要专用
   docker-socket-proxy 或把终端服务拆到独立主机才能进一步收敛（未在本 issue 内实现）。

## 被否决的方案

1. **`docker run -it` + attach（issue §6 备选）**——否决。attach 要求容器主进程
   就是 shell，容器退出与 PTY 退出耦合；resize 需 attach 流带 TTY 控制消息，
   清理/多路复用都更复杂，且 `--restart` 语义会与终端生命周期冲突。
   exec PTY 把「容器生命周期」与「shell 会话」解耦：容器固定的 `sleep infinity`
   只做沙箱边界，shell 由 exec 管理，关闭时 kill+remove 一步到位。
2. **WS 用 JWT 直接鉴权（复用 `authenticate_ws`）**——否决。终端 WS 可能长连
   1 小时，JWT 一旦泄露窗口过长；一次性 60s 票据 + GETDEL 更贴合「建连一次」。
3. **票据放 URL 查询参数**——否决。会进访问日志/浏览器历史（与 #258 同源问题）。
4. **禁用功能时静默返回空会话列表**——否决。禁用/依赖不可用一律 503
   （`terminal_disabled` / `docker_unavailable`），避免调用方把「没功能」误判成
   「没终端」。
5. **同一终端写输入排队**——否决。排队会让两次输入的先后顺序依赖调度而非调用
   顺序；直接 `terminal_busy` 让调用方显式重试。

## 运维

- 开关：`TERMINAL_ENABLED=true` + `SANDBOX_MODE∈{auto,docker}` + Docker 可达；
  任一不满足时创建返回 503（detail `terminal_disabled` / `docker_unavailable`）。
- 部署：`docker compose -f docker-compose.yml -f docker-compose.terminal.yml up -d --build`
  （overlay 内容与威胁提示见文件头注释）。
- 清理残留容器：
  ```bash
  docker ps -a --filter label=docmind.terminal
  docker rm -f $(docker ps -aq --filter label=docmind.terminal)   # 确认后执行
  ```
- 指标（`/metrics`）：`terminal_sessions_active`（Gauge）、
  `terminal_session_lifetime_seconds`（Histogram）、
  `terminal_bytes_total{direction}`（Counter）、`terminal_exits_total{reason}`、
  `terminal_abnormal_exits_total`。

## 证据与不确定项

**实测**

- 本机 Docker CLI 29.1.3 存在但 daemon 未运行（`docker info` 失败），
  `DOCKER_AVAILABLE=1` 集成测试 5 例显式 skip；单元/API/工具测试全部使用
  FakeBackend + 假 Redis，49 例通过。
- docker SDK 7.2.0 移除高层 `Container.exec_*`（`dir(Container)` 实测），
  低层 API 签名 `exec_create(container, cmd, stdin, tty, user)` /
  `exec_start(exec_id, socket=True)` / `exec_resize(exec_id, height, width)`。
- `str.lstrip("SIG")` 的字符集合语义会把 `"INT"` 削成 `"NT"`（单测抓到的真实
  缺陷，已改 `normalize_signal` 并加回归用例）。

**推导 / 未实测**

- 真实 Docker daemon 下的 echo/ls、退出码、resize 行为由集成测试覆盖，但本机
  无法运行；CI/有 Docker 的环境需以 `DOCKER_AVAILABLE=1` 复跑。
- Windows Docker Desktop 的命名管道路径（`npipe:////./pipe/docker_engine`）与
  Linux unix socket 的行为差异未实机验证；后端镜像基于 Linux，生产按 Linux 评估。
- 多 worker sticky 路由未在本仓库部署层实现（仅以 409 显式拒绝跨 worker 访问）。
