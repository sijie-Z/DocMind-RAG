# DocMind — 待拍板清单(2026-09-22)

> 本文只列**仍需你/审阅者裁定**的项。已裁定的(401 语义、测试不搞全量真集成化、5.2+5.3 同轮)**不再重复**，只在 §5 留一行备查。
>
> 仓库:`sijie-Z/DocMind-RAG` · 分支 `develop` @ `101d1a4`+PR#93

---

## 0. 裁定结果(2026-09-22 已拍板)

| # | 事项 | **裁定** |
|---|---|---|
| **D1** | 认证缓存架构 | ✅ **路线 A** —— HTTP 请求**彻底取消当前用户对象缓存**,以 DB 为身份权威;WS 单独补 handshake + 存活检查。**不引入 ValidationInterval**(路线 D / Security Stamp 存在但本轮不选)。**拆 3 个 PR**。详见 §1.6 |
| **D2** | #91 的 `query` 字段 | ✅ **`"query": q`**(原始查询串) |
| **D3** | 8 个 dependabot PR | ✅ **逐个 rebase/update 到当前 develop → fresh CI → 绿后逐个合**;每合一个,下一个重新基于新 develop 验证。大版本升级另加针对性 build/smoke 验证 |
| **D4** | 要不要发 release | ✅ **现在不发**。等 #82/#91/5.2/5.3/剩余测试收口 + main 的 Node runtime 基线整理后再发 **1.22.0** |

**核心不变量(冻结)**:

> **`current_user` 永远来自当前 DB 状态,而不是 Redis 中的历史 User 快照。**

**认证测试矩阵(冻结)**:

| 状态 | HTTP | WS |
|---|---|---|
| 有效用户 | 正常 | 可连接 |
| disabled | **401** | 拒绝连接/认证失败 |
| deleted | **401** | 拒绝连接/认证失败 |
| 有效但无权限 | **403** | 保持现有授权语义 |
| JWT 无效 | **401** | 拒绝连接 |

`deleted` / `disabled` 的**公开响应契约必须完全一致**(状态码 / body / `WWW-Authenticate` 头),仅服务端日志可区分。

---

## 0b. 执行顺序(裁定后的计划)

```
PR A  统一 401 契约          ← 先做,它定义 API contract
      user missing  → 401
      user inactive → 401
      generic public response(两者完全一致)
      WWW-Authenticate: Bearer
      main.py 不再丢 headers
        ↓
PR B  HTTP auth cache 架构
      current_user 不再使用 User 对象缓存,DB 为权威
      删除只服务该用途的 auth-cache 写入/失效逻辑
        ↓
PR C  WebSocket auth lifecycle
      chat WS / notification WS 的 handshake 存在性 + is_active
      消息时的用户消失处理 → close/reject
        ↓
#91  "query": q
        ↓
5.2  organization_service 同类 None 防护
        ↓
剩余 7 个测试失败(4 A 类 + 3 B 类)
        ↓
5.3  ci-fast 覆盖这两个文件
        ↓
(拆出)5.4 pytest 配置残余 / 5.5 RUN_INTEGRATION 语义 / 测试三分类重构
        ↓
main 的 Node runtime 基线整理 → 1.22.0 release
```

---

# 1. 【D1】认证缓存架构 —— 我认为这是当前最重要的一项

## 1.1 已确认的事实(全部有 `文件:行号` 证据,可直接复核)

**缓存的两条写入路径,写的形状不一样:**

| 写入路径 | 位置 | 缓存内容 |
|---|---|---|
| DB 路径回源 | `auth_service.py:194-203` | 完整(经 `UserInfoResponse` 序列化)→ **有 `is_active`、有 `is_superuser`** |
| **登录** | `auth.py:150-157` / `:267-274` | **精简字典**:只有 `id/username/email/full_name/organization_id/role/preferences` → **没有 `is_active`,没有 `is_superuser`** |

**缓存读取路径只信缓存,不回查 DB:**

```python
# app/services/auth_service.py:139-165
cached_user = await RedisTools.get_cache(cache_key)
if cached_user:
    user_data = json.loads(cached_user)
    ...
    if not user_data.get("is_active", True):   # ← 登录写的缓存没有这个键 → 默认 True 放行
        raise HTTPException(401, "账号已被禁用")
    user = User(**filtered_data)               # ← 不验证 DB 里还在不在
    return user
```

**TTL 是 24 小时**:`auth_service.py:204-208` 用 `settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60`,默认 `1440` 分钟(`app/core/config/security.py:25`)。缓存命中不延长 TTL,但每次回源或登录都会重写满 24h。

**失效动作全部在 `db.commit()` 之前**:例如删除用户 `users.py:906` 失效 vs `:915` 提交。

## 1.2 三个由此产生的问题

**(a) 失效竞态 —— 普通 API 可达,不需要任何硬删除**

窗口内并发的认证请求:缓存未命中 → 回源 DB(**读到事务提交前的旧 `is_active=True` / 旧 `role`**)→ **以 24h TTL 回填**。commit 后缓存仍是旧状态。

窗口包含 `_log_user_activity`(`users.py:98-116`)与 commit 往返,负载下可达数十毫秒到秒级。**管理员禁用/降权一个用户的同一时刻,该用户用并发请求即可把旧状态钉住最长 24 小时。**

**(b) 「认证主体存活」没有统一裁定点**

「已删除用户 + 缓存命中」场景下,**仅依赖 `get_current_user` 的约 100 处受保护端点全部放行** —— 缓存路径压根没有「用户不存在」这个分支。而 49 处走 `permission_required` 的会 **500**(`security.py:82` → `permission_service.py:30`)。

**同一件事,两种结果,取决于该端点有没有挂 `permission_required`。** 这正是上一轮裁定里那条不变量要消除的。

**(c) WebSocket 完全不查 DB**

聊天 WS(`chat.py:435-474`)与通知 WS(`notifications.py:53-104`)只校验 `verify_token` / `type == "access"` / jti 黑名单 —— **没有 `is_active`、没有用户存在性检查**。连上后每轮消息虽查一次 DB(`chat.py:494-503`),但 `db_user` 为 None 时**不拒绝**,只把组织回退为 1 继续跑 RAG。

→ **已删除/已禁用用户持有效 JWT 可持续消耗 LLM 与检索资源直到 token 过期(默认 24h)。**

> 另:`require_role` / `require_admin`(`auth_service.py:315-341`)定义了但**全仓无调用点**,是死代码。

## 1.3 一个必须先说清楚的判断:**这个缓存的价值主张和「存活」要求是冲突的**

如果要保证「每个请求都验证主体存活」,那就要**每请求一次 DB 查询**。而一旦每请求都要查库,缓存省下的就只剩「JSON 反序列化 + 构造 ORM 对象」——**而如果查库,你本来就能直接拿到真正的 ORM 对象**。

**换句话说:「要求存活」⇒「每请求查库」⇒「用户对象缓存基本没有意义」。**

所以真正要选的不是"用哪个方案",而是:

> **要么接受「每请求一次主键查询」,要么接受「一个有界但非零的陈旧窗口」。**

**不能既要缓存、又要零陈旧窗口。**

一个容易被忽略的补充:即便只验证「存在性」,**授权属性(`is_superuser` / `role`)仍然来自缓存**,可以陈旧 24h。也就是说「存在性验证」这个半措施**闭不了降权场景** —— 被降权的超管,其 `current_user.is_superuser` 仍为 True(缓存值),而 `get_document_for_user` 这类端点正是直接用它判定的。

## 1.4 三条可选路线

| 路线 | 做法 | 陈旧窗口 | 代价 |
|---|---|---|---|
| **A. 每请求 DB 为准** | 缓存路径改为回查 DB;或直接删除用户对象缓存,`get_current_user` 一律走 DB 路径 | **0** | 每请求一次主键查询(有索引,通常是亚毫秒级) |
| **B. 有界陈旧** | 保留缓存,但把 TTL 从 24h 压到一个**显式写下并经过风险分析**的数字;同时把失效动作移到 commit 之后 | = TTL | 需要定一个数字,并接受该窗口内撤销不生效 |
| **C. 存在性 + 属性分离** | 每请求验证存在性(1 次轻量查询),授权属性仍信缓存 | 存在性 0,**属性仍 24h** | 复杂,且**半措施**:闭不了降权场景 |

## 1.5 我的建议

**选 A** —— 理由是它同时解决 (a)(b)(c) 三个问题,且**对那 49 处 `permission_required` 路由不增加任何成本**(它们本来就已经每请求 `db.get(User, id)` 一次);甚至可以让 `check_permissions` 那次查询变成冗余从而省掉。

配套(无论选哪条都要做):

1. **修 `WWW-Authenticate` 被全局丢弃**(`main.py:240-250` 构造 `JSONResponse` 时没传 `headers=exc.headers`)。RFC 9110 规定 401 **MUST** 带该头,而目前四处显式设置的全部到不了客户端 —— **这直接卡住已裁定的契约**。
2. **失效动作移到 `commit()` 之后**,并让 `delete_cache` 失败至少记 error(`core/redis.py:141-146` 目前静默吞异常)。
3. **两个 WebSocket 补一次存在性 + `is_active` 校验。**
4. **统一登录与 DB 路径的缓存形状**(或干脆不再写缓存,见路线 A)。
5. **`permission_service.py:30` / `organization_service.py:35` 的 `None` 解引用补防护** —— 作为纵深防御,而不是唯一防线。

## 1.6 裁定(已拍板)

**选 A。** 且**彻底删除** HTTP 路径上的 User 对象缓存 —— 不是换一种缓存形状继续保留。

理由(审阅者改写后的更严谨表述):

> 只要系统要求每个 HTTP 请求都确认当前认证主体仍然有效,那么「缓存整个 User 对象并直接作为 `current_user` 返回」这个缓存就**失去了核心价值**。因为要求确认存活就必须查库,而一旦查库,本来就能直接拿到真正的 ORM 对象。

**最终 HTTP 架构**:

```
HTTP request
    ↓
JWT signature / exp / type / jti
    ↓
DB.get(User, subject)
    ↓
不存在 / inactive → 统一 401
    ↓
得到当前真实 User
    ↓
后续 authorization → 403 只用于「已认证但权限不足」
```

**WebSocket 不照搬 HTTP**(长连接语义不同):

```
WS handshake → verify JWT → DB load User → 不存在/inactive → 拒绝连接
连接建立后   → 每条需要用户状态的消息 → DB load / 必要的实时状态检查
```

**至少握手阶段不能只信 JWT。**

### 路线 D(Security Stamp / Revocation Version)—— 存在,但本轮不选

审阅者确认这是我漏掉的第四条路线:JWT 带 `auth_version`,`user 123 -> auth_version=17`,禁用/删除/降权时 `auth_version++`,请求只比对版本号,把昂贵的 User 查询降级为轻量的 revocation-state 查询。

**可行的正式架构,但不是小优化** —— 它引入一串新问题:DB commit 与 version bump 的顺序、Redis 故障、**删除用户需要 tombstone**(否则「没有记录 = 默认允许」会让旧 token 重新生效)、tombstone 寿命须覆盖所有可能有效的 token、多实例一致性。

**本轮不引入。** 将来 DB lookup 真成为热点时再单独设计。

### 不引入 ValidationInterval(维持上一轮结论)

现在没有性能证据证明 `db.get(User, id)` 是瓶颈。凭空选一个 30s / 5min / 30min 的数字,本质是在定义一个「权限撤销最多延迟 N」的安全参数,应由**实际性能测量 + 安全需求**驱动。

> **0 陈旧窗口 > 未证明的性能优化。**

### PR 拆分(已定)

| PR | 范围 | 目标 |
|---|---|---|
| **A** | user missing → 401;user inactive → 401;generic public response(两者完全一致);`WWW-Authenticate: Bearer`;`main.py` 不再丢 headers | **确定 API contract** |
| **B** | HTTP auth cache 架构:`current_user` 不再使用 User 对象缓存;DB 为权威;删除只服务该用途的缓存写入/失效逻辑 | 落地不变量 |
| **C** | WebSocket auth lifecycle:两个 WS 的 handshake 存在性 + `is_active`;消息时用户消失 → close/reject | 独立安全边界 |

拆开的原因:每个 PR 都容易验证,且不会把「100+ 端点的认证大改」和 WS 混在一起。

---

# 2. 【D2】#91:`query` 字段装什么

`GET /api/v1/knowledge/suggestions` 的响应模型要求必填 `query`(`schemas/knowledge.py:50`,描述是「原始查询」),但端点返回里没有(`knowledge.py:212-215`)。

**实测确认**:`SearchSuggestionResponse.model_validate({"success": True, "suggestions": [...]})` → `ValidationError: missing ('query',) Field required`。**该端点按现在的代码不可能返回 200。**

端点签名里有 `q` 参数(`knowledge.py:206` 传给 service)。**我倾向 `"query": q`** —— 但这是产品语义,我不替你定。

**需要你一句话:是 `q`(原始查询串),还是别的?**

---

# 3. 【D3】8 个 Dependabot PR —— 已拍板

| PR | 内容 | 实际 CI |
|---|---|---|
| #75 | `aiokafka >=0.10 → >=0.14` | ✅ 绿 |
| #76 | `pillow >=10 → >=12` | ✅ 绿 |
| #78 | **`langchain >=0.1.0 → >=1.4.0`** | ✅ 绿 |
| #79 | `alembic >=1.13.1 → >=1.20.0` | ✅ 绿 |
| #80 | **`mcp >=1.0.0 → >=2.2.0`** | ✅ 绿 |
| #73 | **`vite 7.3.6 → 8.3.0`** | ⚠️ 状态未知 |
| **#74** | **`typescript 5.9.3 → 7.0.2`(跨两个大版本)** | ❌ **CI Pass: fail、Frontend Tests: fail** |
| #77 | **`@iconify/vue 4.3.0 → 5.0.1`** | ⚠️ 状态未知 |

> ⚠️ **本文档早先版本把 #74 写成"状态未知"是不准确的** —— 实测 `gh pr checks 74` 显示 **`CI Pass: fail` + `Frontend Tests (22): fail`**。它的旧 CI 本身就是红的,**不能和那几个绿的一起看待**。

**裁定**:

> **逐个将 Dependabot PR 更新/rebase 到当前 `develop`,在新的 base 上重新执行 CI;fresh CI 全绿后再逐个合入。每合入一个后,下一个 PR 再基于新的 `develop` 重新验证。**

而不是"让旧的 CI 重跑一遍"—— 那 5 个"绿"是 **5 天前**的绿(全部落后 develop 8 个提交),CI 跑在 PR #81/#87/#88/#89 合入**之前**;**旧 commit 的绿全部作废**。

**大版本升级另加针对性验证**:`#73` Vite、`#74` TypeScript、`#77` Iconify、`#78` LangChain、`#80` MCP —— 不能只看"CI green"四个字,至少确认对应应用的 build / import / 关键测试真的覆盖到了变更面。**#74 / #78 / #80 跨度明显较大,不因一个 CI Fast green 就视为无风险。**

`base` 均为 `develop`,符合约定(版本更新走 develop、安全更新走 main)。

---

# 4. 【D4】Release —— 已拍板:**现在不发**

## 4.1 依据已按真实失败集重写

之前本文档写「release 一次就能把 main 的红 CI 修掉」—— **这个判断是错的**。实测 2026-09-22 的 main nightly(run `35696897585`,`57c47aa`):

```
Backend Integration Tests (3.11): 11 failed, 143 passed
Backend Integration Tests (3.12): 11 failed, 143 passed
Security Scan (audits advisory):  failure
Docker Build:                     success
```

**那 11 个失败里,只有 1 个是 main 独有的**(`test_document_pipeline.py::test_missing_document_returns_false`,即 `documents.md5_hash` 缺列)。**另外 10 个在 develop 上也存在**:

```
test_api_documents.py: test_upload_no_permission / test_upload_no_file /
                       test_get_document_not_found / test_get_document_unauthorized_org /
                       test_get_content_success / test_delete_document_success /
                       test_delete_document_not_found
test_api_knowledge.py: test_get_stats_success / test_get_suggestions / test_rebuild_success
```

**所以 release 只带走已修的那部分,main 仍然会红。** 此外 `Security Scan` 在 main 上失败是因为 PR #81 的 pip-audit 修复也只到了 develop。

> 自查:我犯这个错,是因为只看了 main nightly 的**第一个**错误(`md5_hash`)就归因了 —— 和这次 CI 排查里反复出现的同一个模式(红 CI 的第一个错误往往不是全部根因)。

## 4.2 发布 gate

不要「develop 有一批修复 → 直接 release」,而是:

```
#82 D1 认证边界收口(PR A/B/C)
        ↓
#91 query 修复
        ↓
5.2 + 5.3
        ↓
剩余 7 个测试失败清理
        ↓
新的 develop 集成验证
        ↓
main release candidate
        ↓
修 main 的 Node runtime / nightly 基线
        ↓
release
```

**版本号:`1.22.0`** —— 但定义是「本轮认证边界 + API 错误语义 + 测试/CI 收口后的下一版本」,不是「今天必须发」。

这样版本历史是一次**有明确主题**的 release,而不是把一堆半成品从 develop 搬到 main。

> `main` 上仍是 `setup-node@v4` + `node-version: '20'`,**Node 20 已 EOL(2026-04)**;这一项属于 4.2 里「main 的 Node runtime 基线」那一步。

---

# 5. 已裁定/已完成的(备查,不用再判)

| 项 | 结论 | 状态 |
|---|---|---|
| Q1 用户不存在语义 | **401**,与「账号被禁用」公开响应完全一致,禁止 claims 回退,403 只留给「已认证但权限不足」 | 待 D1 落地 |
| Q2 测试怎么改 | **不做全量真集成化**;保留 mock + 修正 mock + **重新分类**(unit / api / integration 三层) | 待办 |
| Q3 相邻缺陷 | **5.2(`organization_service` 同类 None)+ 5.3(ci-fast 覆盖)本轮一起**;5.4/5.5 拆出 | 5.1 已完成 |
| #90 资源不存在 500 | **已修**(PR #93),实测 `9 failed → 7 failed`,反向验证通过 | PR #93 |
| #85 HTTPException 被吞 | 已修(PR #87),issue 已关闭 | ✅ |
| #86 markers 失效 | markers 部分已修(PR #89);`RUN_INTEGRATION` 语义拆出另开 | 部分 |

**修完 #90 + #91 后,那 9 个集成测试仍会有 7 个失败**(4 个 A 类 + 3 个 B 类,全部是测试侧问题)。详见 issue #83 的最新评论。

---

## 附:本文档的证据可信度

- §1.1 / §1.2 的每条事实都有 `文件:行号`,可直接复核。
- §1.3 的「缓存价值主张与存活要求冲突」是**推理**,不是实测 —— 如果这个推理有漏洞,我列的三条路线和推荐都会跟着变,**请重点审这一条**。
- **未实测**:实际 `ACCESS_TOKEN_EXPIRE_MINUTES` 的环境变量值(默认 1440,仓库无 `.env`);`db.merge(current_user)` 对已删除行的行为;运行时 FastAPI 版本(影响「未带 token」时是 401 还是 403)。
