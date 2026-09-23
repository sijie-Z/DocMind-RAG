# DocMind — 集成测试 10 处失败 与 认证边界「用户不存在」语义

**决策请求 · 2026-09-20**
仓库:`github.com/sijie-Z/DocMind-RAG` · 分支 `develop` @ `dcac482`

---

## 0. 需要你回答的三个问题

| # | 问题 | 我的倾向 |
|---|---|---|
| **Q1** | JWT 有效但 DB 里查不到该用户,应该返回 401 / 403 / 404 / 回退用 token 身份? | **401,且与「账号被禁用」走完全相同的响应体**(OWASP 要求两者不可区分,否则构成用户枚举 oracle)—— 见 §3.6 |
| **Q2** | 测试侧要不要大改?改到什么程度? | 加 `conftest.py` + seed fixture,把这 6 个"装成集成测试的单元测试"改成真集成测试(见 §4) |
| **Q3** | §5 列的 5 个相邻缺陷,哪些应该在这轮一起修? | 只修 5.1 和 5.3,其余另开 |

**重要:Q1 和 Q2 是独立的。** 我实测过——**只修产品侧,测试一个都不会变绿**(见 §3.4 的实测数据)。所以别把它们当成一个问题。

---

## 1. 事实基础(全部实测,可复现)

### 1.1 CI 现状

**发现时**(develop `dcac482`):

```
Backend Integration Tests (MySQL + Redis) (3.11): 10 failed, 144 passed
Backend Integration Tests (MySQL + Redis) (3.12): 10 failed, 144 passed
```

**当前**(develop `12c966f`,PR #87/#88 已合并后本地实测):

```
tests/integration/test_api_documents.py tests/integration/test_api_knowledge.py
→ 9 failed, 17 passed
```

已立的 issue 与 PR:

| # | 标题 | 状态 |
|---|---|---|
| [#82](https://github.com/sijie-Z/DocMind-RAG/issues/82) | [auth] 用户不存在时 500 + 与「账号被禁用」可区分(用户枚举) | **待本文档的决策** |
| [#83](https://github.com/sijie-Z/DocMind-RAG/issues/83) | [test] 集成测试失败,三类根因 | 部分修;**实测结论见 issue 评论** |
| [#84](https://github.com/sijie-Z/DocMind-RAG/issues/84) | [ci] ci-fast 只覆盖 11 个集成文件中的 2 个 | 待修(依赖 #83) |
| [#85](https://github.com/sijie-Z/DocMind-RAG/issues/85) | [auth] HTTPException 被自己的 except 吞掉 | **已修**(PR #87 已合) |
| [#86](https://github.com/sijie-Z/DocMind-RAG/issues/86) | [test] pytest.ini 覆盖 pyproject → markers 全失效 | **PR #89** |
| [#90](https://github.com/sijie-Z/DocMind-RAG/issues/90) | **[bug] 资源不存在/无权限返回 500 而非 404/403** | **新发现,真实生产 bug** |
| [#91](https://github.com/sijie-Z/DocMind-RAG/issues/91) | **[bug] `GET /knowledge/suggestions` 不可能返回 200** | **新发现,真实生产 bug** |
| [#92](https://github.com/sijie-Z/DocMind-RAG/issues/92) | [test] `test_auth_api.py` 的 `@patch(get_db)` 对 FastAPI DI 无效 | 新发现 |

**⚠️ 下面 §4 的原始分析已被实测大幅修正,请一并读 §4.4。** 简短版:这 9 个测试失败里**有 6 个的根因是两个真实生产 bug(#90 ×5、#91 ×1)**,不是测试质量问题。

原始失败的 10 个:

| 文件 | 数量 |
|---|---|
| `tests/integration/test_api_documents.py` | 7(现 6) |
| `tests/integration/test_api_knowledge.py` | 3 |

### 1.2 这不是回归,是长期状态

同样的 10 个测试在 **2026-08-17** 就已经失败(我用 `gh run view 31989160581` 核过),早于本次所有改动。

### 1.3 为什么到现在才暴露:四层故障,每修一层露出下一层

| 层 | 故障 | 是什么掩盖了它 |
|---|---|---|
| 1 | workflow 里 `python -c "... \"SELECT ...\" ..."` 引号 bug | — |
| 2 | Alembic 迁移链在真实 MySQL 上从不可用(4 处独立缺陷) | 第 1 层让它跑不到 |
| 3 | main 缺 `documents.md5_hash` 列 | 只影响 main |
| 4 | **集成测试 10 处失败** | 前三层让它从没跑到结束 |

**教训(已在仓库 CHANGELOG 记录):红 CI 的「第一个错误」往往不是真根因。每修好一层必须重跑,直到顶层不再出现新的失败。**

### 1.4 本地可复现(约 6 秒)

```bash
docker run -d --rm --name docmind-test-mysql -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=test_db -p 3306:3306 mysql:8.0 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci
docker run -d --rm --name docmind-test-redis -p 6379:6379 redis:7-alpine

export DATABASE_URL="mysql+aiomysql://root:root@localhost:3306/test_db" \
  SECRET_KEY="test-secret-key-for-pytest-only-32chars!" \
  JWT_SECRET_KEY="test-jwt-secret-key-for-pytest-only!" \
  DEEPSEEK_API_KEY=test-key EMBEDDING_API_KEY=test-key \
  ENABLE_RATE_LIMIT=false ENABLE_MONITORING=false RUN_INTEGRATION=1
python -m alembic upgrade head          # 必须!否则报 Table doesn't exist,与 CI 不符
python -m pytest tests/integration/test_api_documents.py \
  tests/integration/test_api_knowledge.py -q
# → 10 failed, 16 passed   (与 CI 完全一致)
```

**collation 必须是 `utf8mb4_unicode_ci`** —— 这是仓库的 canonical 值,CI 用 `services.mysql.command` 强制,不一致会出现 MySQL 3780 错误。

---

## 2. 直接根因

```
app/core/security.py:82      user = await db.get(User, current_user.id)   # 可能返回 None
app/core/security.py:83      if user and user.is_superuser:               # 这里防了 None
app/core/security.py:109     await permission_service.get_user_permissions(db, user, ...)  # 这里没防
app/services/permission_service.py:30    if user.is_superuser:            # ← AttributeError
```

`user=None` → `AttributeError` → `app/main.py:292-309` 的 catch-all `Exception` handler 兜成 **500**。

`permission_service.get_user_permissions` 里还有**第二处**无保护解引用:`permission_service.py:53` 的 `user.id`。
另有 `permission_service.py:24-25` 的兼容分支 `if isinstance(user, int): user = await db.get(User, user)` —— **该分支后 user 同样可能为 None,且无任何检查**。

---

## 3. Q1:「用户不存在」应该返回什么

### 3.1 三条可达路径(**生产环境真的会走到**)

> ⚠️ **本节于 2026-09-22 大幅修正** —— 架构调查证明我原先对「路径 A」的描述**前提有误**。
> 修正后的结论是:**我原先说的那条路径更弱,但换来了三条更严重的发现**。逐条标注如下。

**路径 A —— Redis 缓存未过期 + 用户行已从 DB 消失**

原描述（**前提有误**）：我写「用户被删 → 缓存还在 → 500」。
**修正**：本代码库的「删除用户」（`users.py:903-904`）是**软删除** —— 只设 `is_active = False`，
**不删行**。所以 `db.get(User, id)` 仍能取到对象，**不会**触发 None。

要让「路径 A」成立，必须有人**绕过 API 手工硬删**（SQL / 运维脚本 / 外部系统）。
**所以它的生产可达性比我原先写的低。**

**路径 A′ —— 缓存失效的竞态（新发现，且 API 可达，比路径 A 严重）**

所有缓存失效动作都在 `db.commit()` **之前**执行（`users.py:906` vs `:915`）。

窗口内并发的认证请求会：缓存未命中 → 回源 DB（事务未提交，读到**旧的** `is_active=True`）
→ **以 24h TTL 回填缓存**。commit 生效后，缓存里仍是旧状态，而缓存路径只信缓存。

**这不需要任何硬删除，普通 API 调用即可触发**：管理员禁用/降权一个用户的同时，
该用户用并发请求即可把旧状态钉在缓存里最长 24 小时。
窗口包含 `_log_user_activity`（`users.py:98-116`）与 commit 往返，负载下可达数十毫秒到秒级。

**路径 A″ —— 登录写入的缓存缺安全字段（新发现）**

`auth.py:150-157` / `:267-274` 登录时写入的缓存是**精简字典**，只有
`id/username/email/full_name/organization_id/role/preferences` ——
**没有 `is_active`，也没有 `is_superuser`**。

而缓存路径用 `user_data.get("is_active", True)` 兜底（`auth_service.py:155`）→
**该形状的缓存天然通过 is_active 检查**。且 `User(**filtered_data)` 构造出的对象
`is_superuser` 为 `None`，会让直接用 `current_user.is_superuser` 的端点（如
`get_document_for_user`）把超管当普通用户。

**路径 B —— DB 路径 TOCTOU**
`auth_service.py:172-179` 查到用户，到 `check_permissions:82` 再查时已消失。

**路径 C —— 测试环境**（当前 9 个失败的主因，但不是唯一）
测试 override 了 `get_current_user` 却没 override `get_db`，于是 `db.get` 打到真库。

### 3.1b 架构调查确认了 ChatGPT 指出的那个空洞（**比 3.1 的任何一条都重要**）

「已删除用户 + 缓存命中」场景下，**仅依赖 `get_current_user` 的端点全部放行** ——
因为缓存路径根本不存在「用户不存在」这个分支（`auth_service.py:161-165` 直接 return）。

受影响的受保护端点**约 100 处**（`knowledge.py` 16 处、`chat.py` 11 处、`organizations.py` 13 处、
`agent.py` 10 处、`monitoring.py` … 全清单见调查记录），它们在硬删场景下会正常返回 200，
端点内部只用缓存对象的 `id/organization_id` 做查询条件，行不存在只是返回空集。

**两个额外的独立缺口：**

- **WebSocket 完全不查 DB。** 聊天 WS（`chat.py:435-474`）与通知 WS（`notifications.py:53-104`）
  都只校验 `verify_token` / `type == "access"` / jti 黑名单，**没有 `is_active`、没有用户存在性检查**。
  连上之后每轮消息虽会查一次 DB（`chat.py:494-503`），但 `db_user` 为 None 时**不拒绝**，
  只是把组织回退为 1 继续跑 RAG。→ 已删除/已禁用用户持有效 JWT **可持续消耗 LLM 与检索资源直到 token 过期（默认 24h）**。

- **`require_role` / `require_admin` 是死代码** —— `auth_service.py:315-341` 定义了但**全仓无调用点**。
  它内部用缓存对象的 `role`，将来若被启用会继承同一问题。

### 3.1c 契约缺口：`WWW-Authenticate` 被全局丢弃

`app/main.py:236-250` 的 `HTTPException` handler 构造 `JSONResponse` 时**没有传 `headers=exc.headers`**：

```python
return JSONResponse(
    status_code=exc.status_code,
    content={...},
)          # ← 没有 headers=exc.headers
```

因此 `auth_service.py:107/115/123/131` 显式设置的 `WWW-Authenticate: Bearer`
**一个都到不了客户端**。而 RFC 9110 规定 401 **MUST** 带该头。

**这直接卡住 §3.6 定的契约** —— 「401 + `WWW-Authenticate: Bearer`」的后半句目前根本不生效，
必须先修这个 handler。另外缓存路径的「账号已被禁用」401（`auth_service.py:157-160`）本身也没设该头。

### 3.1d 缓存 TTL：默认 24 小时

`auth_service.py:204-208` 用 `settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60`，
默认值 `1440` 分钟（`app/core/config/security.py:25`）= **24 小时**。
缓存命中不会延长 TTL，但每次回源或登录都会重写为完整 24h。
**所以 3.1 里任何一条「陈旧状态」的最长存活窗口都是 24 小时。**

### 3.2 现状是**自相矛盾**的

| 位置 | 用户不存在时 |
|---|---|
| `auth_service.py:175-179`(DB 路径) | **404** `"用户不存在"` |
| `auth_service.py:157`(缓存路径,账号禁用) | **401** `"账号已被禁用"` |
| `auth_service.py:184`(DB 路径,账号禁用) | **401** `"账号已被禁用"` |
| `check_permissions:109` | **500**(未处理) |

**同一件事,仓库里有三种不同的答案。** 这本身就是需要定的信号。

### 3.3 候选方案

| | 做法 | 优点 | 代价 |
|---|---|---|---|
| **A** | `if user is None: raise HTTPException(401, "用户不存在")` | 语义正确;与"账号被禁用"一致;不泄露用户是否存在 | 与现有 `auth_service` 的 404 不一致,需一并改 |
| **B** | `if user is None: user = current_user`(回退用 token 身份) | 改动最小;现有 1 个测试直接翻绿 | **权限撤销失效**——token 里的 `is_superuser`/`role` claims 在过期前一直有效(见 §3.5(3),这是安全漏洞不是性能取舍) |
| **C** | 缓存路径补一次 DB 存在性校验 | 从源头堵住路径 A | 每个请求多一次 DB 查询,削弱缓存的意义;不解决 B/C 路径 |
| **D** | 什么都不改,只让 `get_user_permissions` 容忍 None(返回空权限集) | 不 500 | 语义最差:**用户不存在 → 变成 403"无权限"**,把认证失败伪装成授权失败 |

### 3.4 实测:产品侧修复**不能**让测试变绿(这是关键)

我在本地对 `develop` 真跑了两遍(临时改动,已还原):

| 方案 | 结果 |
|---|---|
| 基线(未改) | **10 failed, 16 passed** |
| 方案 A(401) | **10 failed, 16 passed** ← 一个都没翻绿 |
| 方案 B(回退) | **9 failed, 17 passed** ← **只翻绿 1 个**(`test_upload_no_permission`) |

**含义:**
- 方案 A 下 500 变成了 401,但测试期望的是 403/404 → 仍然红。
- 方案 B 下唯一翻绿的那个,是因为回退后 mock user 的 `is_superuser=False`,走到了权限检查并得到 403——**这恰好说明 B 让测试"配合"了产品,而不是产品对了**。
- 剩下的 9 个是**测试自身的缺陷**(§4),与 Q1 选什么无关。

**所以 Q1 和 Q2 必须分开决策。**

### 3.5 外部依据:规范与主流实现怎么说

**(1) 401 而非 403 —— 三条 RFC 收敛到同一结论**

RFC 6750 §3.1 对 `invalid_token` 的定义**明确包含"因其他原因无效"**:

> The access token provided is **expired, revoked, malformed, or invalid for other reasons**. The resource **SHOULD respond with the HTTP 401 (Unauthorized) status code**.

403 在 RFC 6750 里对应的是另一件事——`insufficient_scope`:"The request requires **higher privileges** than provided by the access token"。

RFC 9110 §15.5.2 / §15.5.4 给出判据:401 = "lacks **valid authentication credentials**";403 = 服务端"**understood the request** but refuses to fulfill it"、"credentials were provided... considers them **insufficient**"。**用户不存在时连认证主体都建立不起来,属于前者。**

最强的一条是 RFC 8725 §3.8(JWT 的 IETF Best Current Practice):

> when the JWT contains a "sub" (subject) claim, the application **MUST validate that the subject value corresponds to a valid subject**... **If the issuer, subject, or the pair are invalid, the application MUST reject the JWT.**

`MUST reject` —— 拒绝整个 token,即认证失败。

> 诚实标注:**没有任何 RFC/OWASP 逐字覆盖"密码学有效 + 未过期 + 指向已不存在用户"这一具体场景。** 401 是从上面三条**推导**的,不是直接引用。

**(2) 一个我原先没想到的硬要求:「用户不存在」与「用户被禁用」必须**不可区分****

OWASP Authentication Cheat Sheet 原文直接覆盖了这两种情况:

> an application **must respond with a generic error message regardless of whether**: The user ID or password was incorrect / **The account does not exist** / **The account is locked or disabled**.
> The objective is to prevent the creation of a **discrepancy factor**, allowing an attacker to mount a **user enumeration** action.

同一段还警告**时序侧信道**("time-based attack")。

**这条直接影响实现,而且当前代码违反了它:**

| 位置 | 响应体 `detail` |
|---|---|
| `auth_service.py:178`(用户不存在) | `"用户不存在"` |
| `auth_service.py:159,186`(账号禁用) | `"账号已被禁用"` |

**两者 detail 不同 = 枚举 oracle。** 攻击者拿一个过期 token 轮询就能区分"这个 user id 存在过但被封了"和"从未存在"。

正面样例 —— DRF simplejwt 的做法是**异常类型和响应码完全相同,只在 `code` 字段区分,而 `code` 不返回给客户端**:

```python
except self.user_model.DoesNotExist as e:
    raise AuthenticationFailed(_("User not found"), code="user_not_found") from e
if api_settings.CHECK_USER_IS_ACTIVE and not user.is_active:
    raise AuthenticationFailed(_("User is inactive"), code="user_inactive")
```

**推论:Q1 的正确形态不是"选 401",而是"两者走完全相同的分支和响应体,只在服务端日志里区分"。** 这比单纯选状态码要求更高。

**(3) 回退用 token claims:这不是性能取舍,是安全漏洞**

- RFC 8725 §3.8 的 `MUST reject the JWT` 直接禁止。
- OWASP JWT Cheat Sheet 把 `roles`/`groups` 标为 "Authorization, **risk of spoofed** cross-issuer authorization"。
- RFC 7662 §4 把风险写透了(token 里的 claim 本质是**签发时刻的、不可更新的缓存快照**):

  > at the risk of **stale information about the token**. For example, the token may be revoked while the protected resource is relying on the value of the cached response... **This creates a window during which a revoked token could be used.**

- **最强的产业界背书**:Microsoft Entra 的 Continuous Access Evaluation 把 **"User Account is deleted or disabled"** 列为必须近实时生效的 critical event,并明确否定"缩短 token 寿命"这条路:

  > Microsoft experimented with the "blunt object" approach of reduced token lifetimes but found they **degrade user experiences and reliability without eliminating risks**.

**对方案 B 的直接含义:删用户通常正是在做一次权限撤销(离职/被盗/合规)。回退用 claims 等于让被撤销的凭据继续生效到 `exp`——恰好在最需要撤销的时刻把撤销关掉。**

**(4) 权威项目在这里的表现(含两个反例)**

| 项目 | 用户不存在 | 用户禁用 | 评价 |
|---|---|---|---|
| **FastAPI 官方教程** `oauth2-jwt` | **401** + `WWW-Authenticate: Bearer`,复用 `credentials_exception` | — | **正面样例,照抄这个** |
| FastAPI 官方教程 `get-current-user` | — | **400** `"Inactive user"` | **反例,别抄** —— 非 401/403 语义,且显式泄露存在性 |
| **full-stack-fastapi-template** | **404** | **400** | **反例** —— 404/400 构成完备枚举 oracle,同一函数里 `jwt.decode` 失败又是 403,三种失败三个码 |
| **DRF + simplejwt** | `AuthenticationFailed`(401) | `AuthenticationFailed`(401) | **最干净** |
| Spring Security | 未找到权威来源 | 同左 | 走的是 token introspection 路线 |

DRF 的这段注释是本调研最有价值的工程洞察之一 —— 它把"401 vs 403"**绑定到"认证器能否给出 `WWW-Authenticate` 挑战"**:

```python
if isinstance(exc, (exceptions.NotAuthenticated, exceptions.AuthenticationFailed)):
    auth_header = self.get_authenticate_header(self.request)
    if auth_header:
        exc.auth_header = auth_header
    else:
        exc.status_code = status.HTTP_403_FORBIDDEN   # 给不出挑战 → 降级为 403
```

**自检信号:如果你打算返回的 401 没有 `WWW-Authenticate: Bearer`,那它很可能不该是 401。**(RFC 9110 规定 401 **MUST** 带该头,403 无此要求)

**(5) 「每请求查库」这个模式的定位**

- **没有找到统一术语。** 与它最接近的标准化概念是 **token introspection**(RFC 7662):资源服务器不信任 token 自身,而是去问"这个 token 现在还有效吗"。`db.get(User, id)` 在结构上是它的**本地简化版**。
- RFC 7662 §2:`If the token can be revoked after it was issued, the authorization server MUST determine whether or not such a revocation has taken place.`
- RFC 7662 §4 有一条与"不可区分"要求同源的建议:`an introspection response for an inactive token SHOULD NOT contain any additional claims beyond the required "active" claim` —— **说"不活跃",不说"为什么不活跃"。**
- **按主键查一行通常极便宜**,SQLAlchemy `Session.get` 还有 identity map 语义可天然去重。**不要过早优化它**——它买到的是精确的撤销语义。
- 要优化的话,**正确的位置是"短 TTL 缓存 + 显式陈旧窗口"**,不是降级信任 token。成品范例是 ASP.NET Core Identity 的 **Security Stamp**:`ValidationInterval` **默认 30 分钟**,即权限变更后最多 30 分钟内所有旧 token 失效。**连微软也不认为"每请求查库"是唯一选项,但它把折衷做成了一个显式的、有名字的、有默认值的参数**——而不是隐式的"反正 token 还没过期"。

### 3.6 我的建议(依据上面)

**Q1 答案:返回 401,且与"账号被禁用"走完全相同的分支和响应体,只加 `WWW-Authenticate: Bearer` 头,只在服务端日志区分 `user_not_found` / `user_inactive`。**

理由:
1. **认证失败 ≠ 授权失败。** 用户不存在是"你是谁"的问题——连认证主体都建立不起来。
2. **不能信任 token claims 作为降级路径。** 这套代码存在的全部意义就是 `security.py:81` 那句原注释:"**必须先从数据库 reload 一下,确保 `is_superuser` 状态是最新的**"。回退用 token 身份 = **在该防线失效时退回那道防线想防的东西**——这正是 Microsoft CAE 列为 critical event 要避免的情形。
3. **不可区分性是硬要求**(OWASP),不是锦上添花。

**连带要改的:**
- `auth_service.py:175-179` 的 **404 → 401**,并把 detail 统一成 generic(否则保留枚举 oracle)。
- `auth_service.py:159,186` 的 detail 也要一并 generic 化 —— 否则和 178 仍然可区分。
- 401 响应补 `WWW-Authenticate: Bearer` 头(`auth_service.py:131-132` 已有这个模式可参照)。

**我拿不准、想听你的:**
- "每请求查库"当前是正确默认值,**但要不要现在就引入 `ValidationInterval` 式的有界陈旧窗口**?我倾向**不引入**——当前没有任何性能证据表明它是热点,引入等于凭空增加一个需要调参的安全窗口。

### 3.7 前端影响(已核查,**不再是未知**)

原先这条被我标为"本方案最大的未验证假设"。现已对 `frontend/src` 做完整审计,**结论:功能上不会被改坏,但行为会比现在激进得多。**

**(1) 不存在会被破坏的 404 业务语义** ✅

全前端 `src/` 下 404 的**全部**出现只有三处,且两处是死代码:

| 位置 | 用途 | 状态 |
|---|---|---|
| `views/error/404.vue` | 404 页面组件 | **死代码** —— 未在 `router/index.ts` 注册;通配路由 `/:pathMatch(.*)*` 直接 redirect 到 `/firsthome`(`router/index.ts:225-228`) |
| `composables/useErrorHandler.ts:38-40` | `status===404 → {message:'请求的资源不存在'}` | **死代码** —— 全项目只被它自己的测试引用 |
| `utils/request.ts` 的 `switch (status)` | —— | **根本没有 `case 404`** |

**所以「404 → 用户不存在 → 引导注册」这种前端逻辑不存在,改 401 不会破坏任何东西。**

**(2) 401 会走一条激进得多的全局流水线**

`utils/request.ts:276-321`:

1. 先尝试静默刷新 `/auth/refresh`(`request.ts:280-292`)—— 404 时从不发生
2. 刷新失败 → `rejectQueue` + `userStore.logout()` + `router.push({name:'Login'})`(`:293-298`),**且原请求的 detail toast 被跳过**(提前 return,早于 `:344`)
3. 结果是:**被删用户打任何 API 都会被全局强制登出跳登录**,哪怕那个调用方本来静默容忍错误

**(3) 一个我一度以为会造成死循环的风险 —— 已闭环排除** ✅

审计发现 `request.ts:179-191` 的 `processQueueWithToken` 重放排队请求时**没有设置 `_retry`**,而 `:280` 用 `_retry` 判断是否再次进入刷新分支。若 `/auth/refresh` 对已删用户**持续成功**,而数据接口持续 401,理论上是 **refresh → replay → 再 refresh** 的循环。

**已核查后端,不成立**:`app/api/v1/endpoints/auth.py:433-436` 的刷新端点**查库**:

```python
user = await auth_service.get_user_by_id(db, user_id)
if not user or user.username != username:
    raise AuthenticationError("用户不存在")
```

而 `AuthenticationError.status_code = 401`(`app/exceptions.py:45`)。**所以已删用户的刷新必然失败 → 走登出分支,不循环。**

> 注意这个结论的边界:它依赖「刷新端点查库」这一事实。**如果未来有人把刷新改成纯签名校验,这个循环风险就会重新出现。** 建议在 `request.ts` 的 `processQueueWithToken` 里补 `_retry` 标记作为纵深防御(那是前端侧的事,不在本次后端改动范围)。

**(4) 一条硬约束:「无权限」必须继续是 403**

`views/organizations/index.vue:662` 把 403 当**业务语义**用 —— 命中就渲染整页 `n-result status="403"`「当前账号无组织架构权限」(`:61-66`)。

**所以 #82 的修复绝不能把权限检查的 403 一起改成 401**,否则那个页面会失效并触发登出。当前提议的改法("用户不存在"→401、"无权限"→保持 403)符合这条约束。

**(5) 其余影响(轻微)**

- 每次 401 多一次 `/auth/refresh` 网络往返
- 拦截器的 `router.push({name:'Login'})`(**不带 `redirect`**)与路由守卫的 `next({name:'Login', query:{redirect}})` 并发,`?redirect=` 回跳参数**可能丢失**
- WebSocket 不解析 HTTP 状态码,不受直接影响;但 HTTP 401 触发的 `logout()` 清掉 token 后,WS 重连会因取不到 token 而放弃

---

## 4. Q2:测试侧怎么改

### 4.1 9 个剩余失败**不是同一个原因**,分三类

| 类 | 数量 | 机理 | 证据 |
|---|---|---|---|
| **A. 缺 `get_db` override** | 6 | 只 override 了 `get_current_user`,`db.get(User, ...)` 打到真库 | `test_api_documents.py:147,167`;`test_api_knowledge.py:216,347`;`test_api_auth.py:343,368` |
| **B. mock 装配错位** | 3 | `mock_db.get` 被设成返回**用户** mock,但端点用 `db.get` 取的是 **Document** → 拿到用户 mock → 组织不匹配 → 403 | `test_api_documents.py:326-328,356-358`;`test_api_knowledge.py:386-388` |
| **C. mock 字段名过时** | 1 | mock 给 `_source.content`,而端点读 `_source.chunk_text` → `"\n".join(["",""])` = `"\n"` | mock `test_api_documents.py:294` vs 生产 `documents.py:351` |

**C 类最值得注意:它证明这些测试的 mock 已经和生产代码脱节了。** 光修 A 类不会让它变绿。

### 4.2 结构性根因:`tests/integration/` **没有 `conftest.py`**

- 该目录下**没有任何 fixture**。
- **没有任何测试往真库创建用户/组织/角色**——全仓库 grep `AsyncSessionLocal|create_all|sessionmaker|.add(User|.add(Organization` 在 `tests/` 下 **0 命中**。
- 每个测试文件各自定义 `client` fixture 和 `_override_get_db()` 辅助函数,用户是内存里的 `MagicMock`。
- 于是这 6 个文件(共 ~38-47 处 mock/文件的密度)**真实参与的基础设施只有 FastAPI 路由/依赖解析本身** —— DB、ES、Kafka、MinIO、Redis 全被 mock 掉。

**它们是"穿着集成测试外衣的单元测试"。** 一个有 `conftest.py` + seed fixture 的套件,这 6 个问题里的 A 类会自然消失。

### 4.3 我要问你的

1. 应该**加 `conftest.py` + seed fixture**(真建用户/组织,测完清理)把这 6 个改成真集成测试?
2. 还是**承认它们是单元测试**,移到 `tests/unit/` 并把断言改成正确期望?
3. 还是**折中**:保留 mock 风格,但补齐 `get_db` override 和字段名,只求绿?

我原本倾向 1,但代价最大(要设计 fixture 的隔离与清理策略,还要处理 MySQL 下并发测试的数据隔离)。**2 最快,但等于承认"集成测试"这个目录名是假的。**

### 4.4 【实测修正】方案 1 的假设已被证伪 —— 请以本节为准

在独立 worktree + 独立数据库上做了受控实验,逐档加深 seed 深度(`off` / `bare` / `full` / `superuser`),**全程未改动 `app/` 下任何文件**。

**结果:只靠 conftest + seed,9 个里只能修好 1 个**(理论上限 3,且其中两个互斥)。

| mode | seed 内容 | 结果 |
|---|---|---|
| `off`(对照) | 无 | 9 failed / 17 passed |
| `bare` | 只建 user + org | 8 failed / 18 passed |
| `full` | + `initialize_default_permissions_and_roles()` | 8 failed / 18 passed |
| `superuser` | `is_superuser=True` | 8 failed / 18 passed |

**(a) 先更正本文档的数字错误。** §4.1 我写的"6 failed / 16 passed"是错的,实测 **9 failed / 17 passed**(26 个测试)。我把「只跑 `test_api_documents.py` 的 6 failed / 7 passed」和两文件总数混在了一起。§4.1 的 A 类计数 6 也需要澄清:**全仓 6 个,被测的两个文件里是 4 个**。

**(b) 剩下的 8 个里,6 个的根因是两个真实生产 bug**(见 #90、#91),**必须动 `app/`,seed 够不着**。

**(c) 最硬的证据:`test_upload_no_permission` 与 `test_upload_no_file` 语义互斥。**
同一个 mock user、同一个端点,前者断言 403(要求**无**权限),后者断言 422(要求**有**权限)。**任何静态 seed 都不可能同时满足。** 这一条足以说明方案 1 修不好全部。

**(d) seed 方案自身的代价被低估了**,实验踩到三个真实的坑:

1. **`initialize_default_permissions_and_roles()` 写全局数据。** 第一版 teardown 只删 user/org,留下 23 条 permission + 2 个 role,导致 `bare` 那一次**跑出来和 `full` 一样** —— **假绿**,实验被污染。
2. **环形外键**(`users.organization_id` ↔ `organizations.owner_id`)要求插删都拆三步。
3. **并发互相拆台**:teardown 无条件删 `user id=1`,开 `pytest-xdist` 多 worker 会直接撞车。

**而且代价收益比很差**:为修 1/9 个测试,让整个套件**永久依赖库里存在一条 id=1 的记录**。

**修正后的建议顺序:**

1. **先修 #90、#91** —— 真实生产 bug,修完顺带解决 6 个测试失败。**比任何 seed 方案都优先。**
2. **#1/#2 的语义冲突只能改测试或改产品**,seed 无解。
3. **方案 1 降级** —— 不是"最正确但代价大",而是"代价大且收益只有 1/9"。若仍要做分层,倾向**方案 2**(把 3 个纯单元测试文件移入 `tests/unit/`,共 80 个用例),再对剩余 API 测试逐个修正 mock。
4. ⚠️ **修 #82 之后 #91 会立刻浮现** —— 它现在被权限层的 500 挡在后面,两件事要一起排期。

---

## 5. Q3:相邻缺陷(要不要一起修)

### 5.1 `HTTPException` 被自己的 `except` 吞掉(**真 bug,建议修**)

```python
# app/services/auth_service.py:139-169
try:
    ...
    if not user_data.get("is_active", True):
        logger.warning(f"用户 {user_id} 已被禁用，拒绝缓存恢复")
        raise HTTPException(401, "账号已被禁用")     # ← 157 行,在 try 内
    user = User(**filtered_data)
    return user
except Exception as e:                                # ← 166 行,把它捕获了
    logger.warning(f"从缓存恢复用户对象失败: {e}，将回退到数据库查询")
    await RedisTools.delete_cache(cache_key)
```

那句"安全加固"的注释说"禁用账号不得通过缓存路径恢复身份",**但它抛出的 401 被自己的 `except Exception` 吃掉,实际走的是静默回退分支**。结果对(DB 路径也会拦),但控制流与注释不符,且任何未来加在这个 try 里的 `HTTPException` 都会被吞。

### 5.2 `organization_service.py` 同类无保护解引用(**建议一并查**)

`app/services/organization_service.py:21` `get_organization_tree(self, db, user: User)`,行 35 `if user.is_superuser:` —— 无 None 保护,与 `permission_service.py:30` 同一模式。

### 5.3 `ci-fast.yml` 不跑这两个文件(**建议修,否则下次还会这样**)

```yaml
# .github/workflows/ci-fast.yml:84
python -m pytest tests/unit/ tests/behavior/ \
  tests/integration/test_security_regressions.py tests/integration/test_api_auth.py
```

11 个集成文件里**只有 2 个**在 PR 时执行。`test_api_documents.py` 和 `test_api_knowledge.py` **只在 nightly 跑**。

**这就是为什么 PR CI 一直是绿的、而 nightly 是红的**，也是这 10 个失败能潜伏一个月的原因。**修测试之前如果不修这个,同样的潜伏会再发生一次。**

### 5.4 `pytest.ini` 与 `pyproject.toml` 配置冲突(**建议修**)

- `backend/pytest.ini`(生效)只有 `testpaths` / `asyncio_mode`,**没有 `markers`**。
- `backend/pyproject.toml:58-65` 声明了 `unit` / `integration` / `slow` 三个 marker,但**因 pytest 配置优先级 `pytest.ini` > `pyproject.toml` 而不生效**。
- 实测:全 `tests/` 下**没有任何** `@pytest.mark.integration` 使用。

marker 体系是坏的 —— 这也是为什么没法用 `-m "not integration"` 来区分。

### 5.5 `RUN_INTEGRATION` 只门控了 1 个文件(**建议修**)

全仓库 `RUN_INTEGRATION` 只有 3 处:`ci-nightly.yml:22`、`test_document_pipeline.py:11-14`(唯一的 `skipif`)、`CHANGELOG.md:287`。

**其余 153 个集成测试无论本机有没有 MySQL/Redis 都会被收集执行** —— 在开发者机器上会静默地打到不存在的服务或真库。

---

## 6. 需要你判断的取舍点(我拿不准的)

1. ~~**前端会不会被改坏?**~~ **已核查完毕,见 §3.7 —— 不会被改坏。** 剩下的只是"行为比现在激进"这个体验取舍,不是技术风险。
2. **`auth_service.py:175` 的 404 是"有意为之"还是"随手写的"?** 我查过:仓库没有 ADR 目录,没有设计文档,`git log -S` 显示这段代码自 **2026-04-29 初始提交**就存在,没有记录任何理由。**无法从仓库判断意图**——所以我不敢直接断言"它是错的",只能说它与规范不符。
3. **测试侧选 §4.3 的哪个方案?** 三个选项代价差别很大。**注意:有一个未验证的核心假设** —— 如果只靠 conftest + seed 数据(完全不动产品代码)就能让 A 类那 6 个测试变绿,方案 1 的代价会低很多;如果不行,说明产品侧也必须改。**这个假设正在实测中**,结果出来后我会补进本文档。
4. **§5 的相邻缺陷哪些一起修?** 5.1(HTTPException 被吞)已修(PR #87);剩 5.2(organization_service 同类问题)、5.3(ci-fast 覆盖)、5.4(pytest 配置)、5.5(RUN_INTEGRATION)。
5. **要不要现在发一次 release 把 develop 带到 main?** main 的 nightly 仍然红(缺 `md5_hash`,且 main 上的 `setup-node@v4` / `node-version: '20'` 已 EOL)。这两件事只有 release 能修。

### 6.1 调研中明确"未找到权威来源"的两点(不要在引用时当成有依据)

1. **没有任何 RFC/OWASP 文档逐字覆盖"密码学有效 + 未过期 + 指向已不存在用户"这一场景。** §3.5(1) 的 401 结论是**从三条规范推导**的,不是直接引用。如果有人质疑 401,这是最可能被攻击的点。
2. **Spring Security 在 JWT 路径下对"用户已不存在"的具体行为,没有官方文档佐证。** 网上流传的说法(`JwtAuthenticationProvider` 默认不调 `UserDetailsService`)只见于 Google/Stack Overflow 讨论,**非官方**,我没有采信。

### 6.2 一个与决策无关但值得知道的发现

调研时抓取 `full-stack-fastapi-template` 的 `backend/app/api/deps.py`(2026-09-20),其第 36 行是:

```python
except InvalidTokenError, ValidationError:
```

**这是 Python 2 的旧式语法,在 Python 3 下是 `SyntaxError`**(正确写法应为 `except (InvalidTokenError, ValidationError):`)。调研 agent 用三个独立渠道(raw.githubusercontent.com、cdn.jsdelivr.net、api.github.com)交叉验证过字节一致。

提这一点只是**提醒你:如果你去打开这个文件核对,不要以为是你的环境或编辑器出了问题**。这不影响 §3.5(4) 里对它的 404/400 两行的引用准确性。

---

## 7. 调研来源(供你核查)

| 主题 | 来源 |
|---|---|
| `invalid_token` 定义 | [RFC 6750 §3.1](https://www.rfc-editor.org/rfc/rfc6750.html#section-3.1) |
| 401 / 403 权威定义 | [RFC 9110 §15.5.2](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.2) · [§15.5.4](https://www.rfc-editor.org/rfc/rfc9110.html#section-15.5.4) |
| JWT `sub` 校验 MUST reject | [RFC 8725 §3.8](https://www.rfc-editor.org/rfc/rfc8725.html#section-3.8) |
| 陈旧缓存窗口风险 | [RFC 7662 §4](https://www.rfc-editor.org/rfc/rfc7662.html#section-4) |
| 短 TTL 建议 | [RFC 6750 §5.3](https://www.rfc-editor.org/rfc/rfc6750.html#section-5.3) |
| 撤销端点 | [RFC 7009](https://www.rfc-editor.org/rfc/rfc7009.html#section-2) |
| 不可区分 / 枚举防护 | [OWASP Authentication Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html) |
| claims 欺骗风险 / denylist / TSL | [OWASP JWT Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/JSON_Web_Token_Cheat_Sheet.html) |
| 正面样例:401 + WWW-Authenticate | [FastAPI OAuth2+JWT 教程](https://fastapi.tiangolo.com/tutorial/security/oauth2-jwt/) |
| 反例:400 Inactive user | [FastAPI get-current-user](https://fastapi.tiangolo.com/tutorial/security/get-current-user/) |
| 反例:404 + 400 枚举 oracle | [full-stack-fastapi-template deps.py](https://github.com/fastapi/full-stack-fastapi-template/blob/master/backend/app/api/deps.py) |
| 正面样例:两者同为 AuthenticationFailed | [DRF simplejwt](https://github.com/jazzband/djangorestframework-simplejwt/blob/master/rest_framework_simplejwt/authentication.py) |
| 401/403 绑定 WWW-Authenticate 的机制 | [DRF views.py](https://github.com/encode/django-rest-framework/blob/master/rest_framework/views.py) |
| "删除/禁用必须近实时生效" | [Microsoft CAE](https://learn.microsoft.com/en-us/entra/identity/conditional-access/concept-continuous-access-evaluation) |
| 有界陈旧窗口的成品范例 | [SecurityStampValidatorOptions.ValidationInterval](https://learn.microsoft.com/en-us/dotnet/api/microsoft.aspnetcore.identity.securitystampvalidatoroptions.validationinterval) |
| token introspection | [Spring Security Opaque Token](https://docs.spring.io/spring-security/reference/servlet/oauth2/resource-server/opaque-token.html) |

> **引用提醒**:网上常被引用的 RFC 8725 §3.10 标题是 "Do Not Trust Received Claims",看着非常贴题,但**其正文讲的是 `kid`/`jku`/`x5u` 头部注入(SQL/LDAP injection、SSRF),不是陈旧授权 claim**。**不要拿它支持本节的论点,那是误引。** 真正贴题的是 §3.8。

---

## 8. 附录:证据清单

| 事实 | 证据位置 |
|---|---|
| 直接根因 | `app/core/security.py:82,83,109` → `app/services/permission_service.py:24-25,30,53` |
| 500 的来源 | `app/main.py:292-309` catch-all `Exception` handler |
| 缓存路径不验证 DB | `app/services/auth_service.py:136-165` |
| DB 路径用 404 | `app/services/auth_service.py:175-179` |
| 禁用用 401 | `app/services/auth_service.py:157,184` |
| HTTPException 被吞 | `app/services/auth_service.py:157` 在 `139-169` 的 try 内 |
| 同类无保护解引用 | `app/services/organization_service.py:21,35` |
| 54 个路由依赖 `permission_required` | documents 1 / files 2 / manuals 3 / users 10 / monitoring 11 / organizations 13 / knowledge 14 |
| `get_db` 不 commit | `app/core/database.py:96-106`,提交责任在调用方 |
| ci-fast 只跑 2 个集成文件 | `.github/workflows/ci-fast.yml:84` |
| pytest 配置冲突 | `backend/pytest.ini` vs `backend/pyproject.toml:58-65` |
| C 类 mock 漂移 | mock `tests/integration/test_api_documents.py:294` vs 生产 `app/api/v1/endpoints/documents.py:351` |

**未核查/不确定:**
- 前端对 401 / 404 的处理(§6.1)
- `auth_service.py:175` 用 404 的原始意图(§6.2,无文档可查)
- 每个请求查库的行业做法(§6.3,需要外部来源)
- `develop` 之外的分支是否有同类问题未查

---

## 9. 我已经做过的、与本文档无关的改动(避免混淆)

同一天还修了 nightly 的 pip-audit 结构校验误判(PR #81,已全绿待合)。那与本文档的 10 个失败**完全无关**,只是它让我有机会手动触发 develop 的 nightly,才发现了这 10 个失败。
