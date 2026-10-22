# ADR-003：Profiles / Bundles / Patch 配置分层与多入口

- 状态：已采纳（issue #479）
- 日期：2026-10-22
- 关联：Epic #473；#480（SDK）、#481（ACP）以本 ADR 为前置

## 结论

1. **配置采用三层模型**：`profile`（具名组装：入口类型 + 有序 bundle 列表 + 覆盖项）、
   `bundle`（可复用的配置片段）、`patch`（按路径 replace/insert/delete）。
   配置项使用**扁平 `settings:` 映射**（键必须是 `Settings.model_fields` 的子集），
   不引入嵌套配置树。
2. **合并优先级**（低 → 高）：
   代码默认值 < `config/base.yaml` < profile 声明的 bundles（按序）
   < `config/profiles/<profile>.yaml` < `--patch`（按 CLI 顺序）< 环境层。
   环境层内部：`.env` 文件 < 真实环境变量。
3. **`.env` 属于环境层**（压过 YAML、低于真实 env）。这是本 issue 对未写死细节的裁定：
   现状部署把 100+ 变量放在 `backend/.env`，若发行版 YAML 压过 `.env`，部署方的显式
   配置会被静默覆盖——与 issue #232（`.env` 的 `EMBEDDING_MODEL` 被硬件档位静默改写）
   是同一类事故。
4. **模块级 `settings` 改为惰性代理**（`SettingsProxy`）。import 不读 `.env`、
   不探测硬件、不构造 `Settings`；首次属性访问才按 profile 加载并缓存。
   读写均转发到真实 `Settings`，现有 `monkeypatch.setattr(settings, "X", v)` 与
   47 个文件里的 `from app.core.config import settings`（实测 50 处 import 行）全部保持不变。
5. **统一入口**：`python -m app --profile web|worker|headless|sdk|acp [--patch FILE ...] [--dump-config]`。
   `--dump-config` 输出键排序 JSON 并对 secret 打码（`*_KEY/*_SECRET/*_PASSWORD/*_TOKEN`
   与非空 URL 密码段）。`uvicorn app.main:app`、`python -m app.main`、`python run.py`、
   `python start_worker.py` 全部保留为兼容入口。
6. **进程内调度器只在 `web` 入口启动**。issue §5.3 的原文是
   `entrypoint in ("web", "worker")`，本实现按裁定收窄为仅 `web`（见「与 issue 的偏离」）。

## 分层细节

### 文件布局

```text
backend/config/base.yaml                     # 公共层，自动叠加到所有 profile
backend/config/bundles/{core,rag,agent,observability}.yaml
backend/config/profiles/{web,worker,headless,sdk,acp}.yaml
```

profile 的 v1 结构：

```yaml
entrypoint: web
bundles: [core, rag, agent, observability]
settings:
  PORT: 8000
```

`--profile base` 报错：`base.yaml` 是公共层，不是可运行 profile。

### 合并与校验

- 映射深合并；list 与标量整体替换；合并函数返回新对象，不原地修改。
- 未知设置键在加载期失败，错误路径形如 `settings.NOPE`；
  `extra="ignore"` 的静默吞键（issue #259/#273 的同族问题）在此被显式拦截。
- 配置文件（base/bundle/profile/patch）中出现非空、不以 `${` 开头的
  `*_KEY/*_SECRET/*_PASSWORD/*_TOKEN` 值 → 拒绝启动；secret 只能来自环境变量。
- patch 路径必须以 `settings.` 开头，且不得含 `..`、`__`、绝对路径；
  `replace/delete` 要求路径存在，`insert` 要求不存在。
- 硬件档位（`_auto_tier_defaults`）从 import 期副作用移到 `load_settings()`，
  只对最终对象 `model_fields_set` 之外的字段生效，应用字段写日志
  `hardware_tier_applied=[...]`。
- 每次加载输出 `config profile=<p> bundles=<...> config_hash=<12位>`（secret 打码后计算）；
  CLI 写入 stderr，避免污染 `--dump-config` 的 stdout。

### 路由 bundle

`backend/app/api/v1/router.py` 的 `ROUTER_SPECS` 是唯一路由清单（24 个模块），
`build_router(bundles)` 按 profile 挂载；`api_router = build_router(ALL_BUNDLES)`
保留为全量兼容导出。`check_doc_facts.py` 的 `endpoint_modules` 派生口径改为优先解析
`ROUTER_SPECS` 键（数值仍为 24），旧形态仍可回退解析 `include_router(...)`。

## 与 issue 的偏离（须在合并前向用户确认）

- **worker 不启动进程内调度器**。现状 compose 只有 backend 跑 lifespan，worker 不跑；
  `ENABLE_SCHEDULER` 默认 `true`，若 worker 也启动，标准部署会双实例重复执行定时任务。
  因此调度器条件改为 `entrypoint == "web" and ENABLE_SCHEDULER`。
- **`app/main.py` 保留兼容壳而非纯 5 行**。原因：现有测试
  （`test_scheduler_wiring.py`、`test_issue242_settings_wiring.py`、
  `test_a3_local_recovery.py`、`test_backend_port_resolution.py`）直接引用
  `app.main.lifespan` / `init_db` / `es_client` / `kafka_producer` /
  `resolve_backend_port` 等符号并 monkeypatch 它们；这些文件不在本 issue 的改动白名单内。
  兼容壳在进入 lifespan 时按模块级当前绑定生成 `LifespanDeps` 快照，
  lifespan 实体仍在 `app/bootstrap.py`。新入口（`app/entrypoints/web.py`）不经过该壳。

## 被否决的方案

| 方案 | 否决理由 |
|---|---|
| **dynaconf** | 多源合并现成，但 schema 校验弱、错误路径不可控（本 issue 要求 `settings.<KEY>` 路径），且引入较大依赖面；现状 100+ 变量用 pydantic 校验已经够用。 |
| **kustomize** | 面向 K8s 清单 patch，不解决应用内配置合并；进程内入口/测试无法复用。 |
| **Cordis 式插件树** | Node 运行时概念，且本 issue 非目标明确排除「插件树 / 租户级 bundle 下发」。 |
| **嵌套配置树** | 可读性好但校验与 patch 路径歧义大（list 索引 patch、类型漂移）；v1 用扁平 `settings:` 换取「键必须存在、路径可执行」。 |
| **导入期单例 + 环境变量前缀（`DOCMIND_`）** | 与现状 100+ 变量冲突，且 import 期加载会被 CLI 参数顺序吞掉。 |

## 回滚

1. 运行时回滚不需要删除 profile 文件——新 loader 对缺失文件 fail-closed（`ConfigError`）。
   要回到 env-only 语义：把 profile 的 `settings:` 清空（与代码默认值等价），
   `.env` 与真实环境变量仍照常生效。
2. 代码级回滚：恢复 `app/core/config/__init__.py` 的 `settings = Settings()` 与
   import 期硬件档位，恢复 `main.py` 的完整实现，删除 `app/bootstrap.py` 与
   `app/entrypoints/`；`config/*.yaml` 可保留（旧代码不读取）。
3. 入口级回滚：compose 命令改回 `uvicorn app.main:app` / `python start_worker.py`
   （两个入口在本版本中仍可用），无需等待配置回滚。
4. `--patch` 是 CLI 级开关，不落盘，删除 patch 文件即回退。

## 已知边界与未验证项

- **web reload**：`python -m app --profile web` 用进程内 `uvicorn.run`，不支持 reload；
  需要 reload 的开发场景继续用 `python run.py`（子进程模型）。
- **`.env` 相对 CWD 读取**：沿用 pydantic-settings 行为，必须在 `backend/` 下启动；
  在其它目录启动不会读到 `.env`（与改动前一致）。
- **headless 真实运行**：需要 DB/Redis/LLM 可用；`--dry-run` 才是纯配置验收路径。
- **单进程单 profile**：加载结果缓存，同一进程内切换 profile 会报错（多入口 = 多进程）。
- 前端未做任何改动；路由/环境变量对前端的间接影响由主仓 `npm run build` 复核。
