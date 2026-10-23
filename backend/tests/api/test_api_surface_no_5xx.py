"""API 表面 5xx 扫描（issue #169）。

**为什么有这条测试**：issue #169 的两个端点（`GET /organizations/{org_id}`、
`GET /memory/{agent_id}/lessons`）必然 500，却能长期存活 —— 因为**没有任何测试
碰过这两个端点**。单元测试覆盖不到「路由 + 依赖 + 序列化」这条完整链路，
本用例把「OpenAPI 里声明的每个 GET 都不许 5xx」固化成进程内的回归门禁：
GET 路径从 `app.openapi()` 动态获取（新端点自动纳入），不是手写清单。

**鉴权**（三个 override key，缺一不可 —— 仓库里三种鉴权风格并存）：
- `app.core.security.get_current_user`：绝大多数端点；
- `auth_service.get_current_user`（绑定方法）：`users.py` 里 6 个端点直接
  `Depends(auth_service.get_current_user)` —— 只 override 前一个是覆盖不到的
  （实测这些端点会 401，处理器根本不被执行，扫描面静默缩水）；
- `optional_current_user`：`/files/{file_path}` 专用（其函数体内**直接调用**
  auth_service.get_current_user，依赖注入的 override 拦不住它，必须换依赖本身）。
注入的是真表里的超管行（不是 MagicMock）—— `/users/me` 等端点会序列化其字段。
不用真实 Bearer token 的原因：token 验签在 Redis 不可达时要为黑名单检查付出
客户端库的重试退避（实测 ~25s/次 × 7 端点，直接毁掉扫描的可用性）；override
是 tests/api/ 的既有惯例（见 conftest.py 的目录说明）。

**外部服务钉在「不可用」**（`_pin_external_services`）：CI 的两个 job 都没有
ES/MinIO/Kafka（backend-tests 无 services，后端只看 sqlite 文件；backend-integration
只有 MySQL+Redis），第三方 LLM/Embedding 端点更不该成为测试的依赖（测试 key 对
真实端点毫无意义，真实调用还受网络环境影响：本机实测 `/knowledge/suggestions`
因此空等 12s）。本 fixture 把 ES 初始化、MinIO 取对象、RAG pipeline 的
LLM/Embedding/rerank 客户端显式钉成「立即失败」，端点代码走的就是它们各自的
失败分支（ES → RuntimeError → 降级返回；MinIO → ConnectionError → 503 如实暴露
存储故障（#301 起；此前一律假 404「文件不存在」，见 `_EXPECTED_DEPENDENCY_5XX`）；
Embedding/LLM → ConnectionError → retriever/reranker 内已有的 except 兜底为 []），
只是不再空等重试。这也让扫描结果与开发机上有没有起这些服务、有没有外网**无关**（确定性）。

**路径参数替换**：org/user 用本测试亲手种子的真实行 id —— ① 的 500 只在
「资源真实存在」时出现，用不存在 id 只能拿到 404、抓不到它；其余参数用
「看起来合法但大概率不存在」的值（见 `_PATH_PARAM_VALUES`）。

**必填 query 参数**：也从 OpenAPI 动态取（`required: true` 且 `in: query`），
按 schema 类型注入合法值 —— 不注入的话，`/api/v1/knowledge/suggestions` 与
`/api/v1/memory/{agent_id}/context` 会恒 422、**处理器根本没被执行**，
「无 5xx」断言对它们形同虚设（对抗评审 B1：把 suggestions 处理器改坏，扫描仍绿）。
值的选择：命中 `_PATH_PARAM_VALUES` 的名字（如 `organization_id`）用同名值，
整型兜底 "1"，字符串兜底 "issue169probe"。

**跳过清单**：只有两条，理由见 `_SKIP_PATHS`；清单本身有防呆断言
（跳过的路径必须仍存在于 OpenAPI、总数 ≤ 5 —— 防止有人用跳过把问题绕过去）。
"""

import re
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete

from app.core.database import AsyncSessionLocal, Base, engine
from app.core.redis import close_redis
from app.core.security import get_current_user, optional_current_user
from app.main import app
from app.models.organization import Organization
from app.models.user import User
from app.services.auth_service import auth_service

# ── 跳过清单 ──────────────────────────────────────────────────────
# 已实测（issue #169 扫描原型，无 ES/Kafka/MinIO 环境下）这两条分别是 200 / 404，
# 没有藏着 5xx；跳过是因为它们不属于「只读业务 GET」契约，而非因为它们会红。
_SKIP_PATHS: dict[str, str] = {
    "/metrics": (
        "Prometheus 文本抓取端点（运维通道，非业务 JSON 契约）；响应体随指标"
        "注册表规模增长，对 API 客户端的契约意义为零"
    ),
    "/api/v1/auth/ensure-demo": (
        "GET 带写副作用：启用时创建/重置**全局可见**的演示账号（默认关闭，返回 404）。"
        "扫描的写副作用应限于可清理的探测用户自身，故跳过"
    ),
}
_MAX_SKIPS = 5  # 跳过清单的预算上限：超过就说明有人在用跳过绕过问题，必须重新评审

# ── 依赖故障的显式 5xx 豁免（issue #301 / #223）──────────────────────
# /files 代理在 MinIO 故障（本 fixture 钉死不可达）时**如实**返回 503 —— 此前
# 一律假 404「文件不存在」，监控面永远只有 4xx。豁免只放行「该路径恰好返回
# 登记的状态码」；其余任何 5xx 仍判失败，且豁免必须真的命中（防条目空转）。
# /readyz（issue #223）在 ES 被本 fixture 钉成不可用时按设计返回 503 ——
# 这正是就绪探针存在的意义（依赖故障 → 503 摘流），不是缺陷。
_EXPECTED_DEPENDENCY_5XX: dict[str, int] = {
    "/files/{file_path}": 503,
    "/readyz": 503,
    # issue #477：持久终端默认关闭（TERMINAL_ENABLED=false）时，终端 API 按
    # issue 契约返回 503 SERVICE_UNAVAILABLE —— 这是「功能未启用/依赖不可达」
    # 的如实信号（与 /readyz 同类），不是 5xx 缺陷；显式启用后返回 200。
    "/api/v1/terminal/sessions": 503,
}

# ── 路径参数替换表 ─────────────────────────────────────────────────
_ZERO_UUID = "00000000-0000-0000-0000-000000000000"
# org_id / organization_id / user_id 在执行时被替换为**本次种子的真实行 id**（见下）。
_PATH_PARAM_VALUES: dict[str, str] = {
    "document_id": _ZERO_UUID,
    "conversation_id": _ZERO_UUID,
    "message_id": _ZERO_UUID,
    "session_id": _ZERO_UUID,
    "prompt_id": "1",
    "manual_id": "1",
    "job_id": "1",
    "notification_id": "1",
    "workflow_id": "1",
    "execution_id": "1",
    "file_path": "nonexistent_issue169_probe.txt",
    "version_id": "1",
    "version": "1",
    "tag_id": "1",
    "role_id": "1",
    "permission_id": "1",
    "skill_name": "nonexistent_skill_issue169",
    "ref_name": "nonexistent_ref",
    "provider": "nonexistent_provider_issue169",
    "config_id": "1",
    "agent_id": "issue169probe",
    "metric_type": "cpu",
}

# 依赖非 security.get_current_user 的鉴权风格 —— 它们必须真的通过鉴权
# （否则处理器没被执行，本扫描就是空转）。见模块 docstring 的三个 override key。
_AUTH_DEPENDENT_PATHS = (
    "/api/v1/users/me",
    "/api/v1/users/me/sessions",
    "/api/v1/users/me/activity-logs",
    "/api/v1/users/activities",
    "/api/v1/users/db-health",
    "/api/v1/users/stats",
    "/files/{file_path}",
)

_MIN_SCANNED_ENDPOINTS = 75  # 表面规模防呆：低于此值说明路由收集出了问题（当前 84）


def _concrete_path(template: str, param_values: dict[str, str]) -> str:
    """把 /a/{x}/b/{y} 替换成具体路径；未知参数一律 '1'（合法整型形态）。"""

    def _sub(match: re.Match) -> str:
        return param_values.get(match.group(1), "1")

    return re.sub(r"\{([^}]+)\}", _sub, template)


def _required_query_params(get_op: dict, param_values: dict[str, str]) -> dict[str, str]:
    """从 OpenAPI 取该 GET 的必填 query 参数并给出合法值。

    不注入的话这类端点会恒 422，处理器根本不被执行 —— 扫描对它们空转
    （对抗评审 B1 的实测：把处理器改坏仍绿）。
    """
    params: dict[str, str] = {}
    for spec_param in get_op.get("parameters", []):
        if spec_param.get("in") != "query" or not spec_param.get("required"):
            continue
        name = spec_param["name"]
        if name in param_values:
            params[name] = param_values[name]
        elif (spec_param.get("schema") or {}).get("type") in ("integer", "number"):
            params[name] = "1"
        else:
            params[name] = "issue169probe"
    return params


@pytest.fixture(autouse=True)
def _pin_external_services(monkeypatch):
    """把 ES/MinIO/LLM 与 Embedding 客户端钉在「不可用」：立即失败，不等客户端库的重试退避。

    与 CI 契约一致（两个 job 都没有 ES/MinIO；第三方 LLM/Embedding 端点更
    不该是测试的依赖），并让本测试结果与开发机本地服务/网络的有无无关。
    端点走的仍是各自的失败分支（ES→RuntimeError→降级；MinIO→ConnectionError
    →503（#301：存储故障如实暴露，不再假 404）；Embedding/LLM→ConnectionError
    →retriever/reranker 里本来就有 except 兜底为 []），不是被绕过。
    """
    from app.core import elasticsearch as es_module
    from app.core.minio_client import minio_client
    from app.dependencies import get_rag_pipeline

    async def _noop_initialize():
        return None  # 不再真的去连 ES；get_elasticsearch() 随后抛 RuntimeError（端点已处理）

    monkeypatch.setattr(es_module._holder, "initialize", _noop_initialize)
    monkeypatch.setattr(es_module._holder, "_client", None)

    class _UnreachableMinio:
        """等价于「MinIO 后端不可达」，但不含 urllib3 的 20s 级重试退避。

        /health 的 bucket_exists 在 executor 线程里跑，wait_for 超时放弃后
        线程仍在重试 —— 事件循环关闭会等它，实测拖出 ~15s teardown。
        """

        def get_object(self, *args, **kwargs):
            raise ConnectionError("MinIO unreachable — pinned by API surface scan")

        def bucket_exists(self, *args, **kwargs):
            raise ConnectionError("MinIO unreachable — pinned by API surface scan")

        def put_object(self, *args, **kwargs):
            raise ConnectionError("MinIO unreachable — pinned by API surface scan")

    monkeypatch.setattr(minio_client, "_client", _UnreachableMinio())

    class _UnreachableChatCompletions:
        async def create(self, *args, **kwargs):
            raise ConnectionError("LLM API unreachable — pinned by API surface scan")

    class _UnreachableEmbeddings:
        async def create(self, *args, **kwargs):
            raise ConnectionError("Embedding API unreachable — pinned by API surface scan")

    class _UnreachableChat:
        def __init__(self):
            self.completions = _UnreachableChatCompletions()

    class _UnreachableOpenAI:
        """等价于第三方 LLM/Embedding 端点不可达（测试 key 对真实端点毫无意义，
        真实调用还受网络环境影响：本机实测 /knowledge/suggestions 因此空等 12s）。"""

        def __init__(self):
            self.chat = _UnreachableChat()
            self.embeddings = _UnreachableEmbeddings()

    class _UnreachableEmbeddingProvider:
        """issue #196：检索期 embedding 走 provider（索引期同一 embedding_service）。"""

        async def embed_query(self, *args, **kwargs):
            raise ConnectionError("Embedding API unreachable — pinned by API surface scan")

    # 单例 pipeline 对客户端的引用有两处：pipeline 自身（含 rerank 调用点）与
    # retriever（openai_client / embedding_provider）。
    pipeline = get_rag_pipeline()
    unreachable_llm = _UnreachableOpenAI()
    monkeypatch.setattr(pipeline, "openai_client", unreachable_llm)
    monkeypatch.setattr(pipeline, "rerank_client", unreachable_llm)
    monkeypatch.setattr(pipeline.retriever, "openai_client", unreachable_llm)
    monkeypatch.setattr(pipeline.retriever, "embedding_provider", _UnreachableEmbeddingProvider())


@pytest_asyncio.fixture(autouse=True)
async def _fresh_pools():
    """每个用例前后释放连接池 —— 与 tests/integration/test_auth_db_authority.py 同理：
    pytest-asyncio 每用例一个新事件循环，池里指向上一个循环的连接被复用时，
    报错与真实症状毫无关系。只在 teardown 释放不够，之前也要清。
    """
    await engine.dispose()
    try:
        await close_redis()
    except Exception:
        pass
    yield
    await engine.dispose()
    try:
        await close_redis()
    except Exception:
        pass


@pytest_asyncio.fixture
async def seeded_identity():
    """建一个真实超管 + 组织行（扫描后连副作用行一起清理）。

    用真行而不是 mock 对象：① 类缺陷（ORM 未被序列化）只在「真实存在的行」
    路径上触发；被扫描的 GET 也可能产生副作用行（实测 /user/settings 会
    get-or-create），清理按 metadata 反查所有引用列，保证测试无残留。
    """
    try:
        async with engine.connect() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # 数据库不可达（如本机未设 DATABASE_URL 指向真实库）
        pytest.skip(f"数据库不可达，跳过 API 表面扫描：{type(exc).__name__}: {exc}")

    suffix = uuid.uuid4().hex[:10]
    async with AsyncSessionLocal() as db:
        user = User(
            username=f"issue169_scan_{suffix}",
            email=f"issue169_scan_{suffix}@example.com",
            hashed_password="not-a-real-bcrypt-hash",
            role="admin",
            is_active=True,
            is_superuser=True,
        )
        db.add(user)
        await db.flush()
        org = Organization(
            name=f"issue169_scan_org_{suffix}",
            description="issue #169 API surface scan",
            color="#18a058",
            is_private=False,
            parent_id=None,
            level=1,
            sort_order=0,
            owner_id=user.id,
        )
        db.add(org)
        await db.commit()
        await db.refresh(user)
        await db.refresh(org)
        user_id, org_id = user.id, org.id

    yield user_id, org_id

    import app.models  # noqa: F401  — 确保全部模型已注册进 Base.metadata

    async with AsyncSessionLocal() as db:
        # 不用 sorted_tables：organizations/users 互为外键（owner_id / organization_id），
        # 排序会告警且本就不需要顺序 —— 先把所有引用行删空，最后删两个主体即可。
        for table in Base.metadata.tables.values():
            if table.name == "users":
                continue
            if "user_id" in table.columns:
                await db.execute(table.delete().where(table.c.user_id == user_id))
            if "organization_id" in table.columns:
                await db.execute(table.delete().where(table.c.organization_id == org_id))
        await db.execute(delete(Organization).where(Organization.id == org_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


def _user_override(user_id: int):
    """返回真表里的超管行（不是 MagicMock）—— /users/me 等端点会序列化它的字段。"""

    async def _get_user():
        async with AsyncSessionLocal() as db:
            user = await db.get(User, user_id)
        assert user is not None, "种子用户被并发清理？"
        return user

    return _get_user


@pytest.mark.asyncio
async def test_all_get_endpoints_do_not_return_5xx(seeded_identity):
    user_id, org_id = seeded_identity

    override = _user_override(user_id)
    app.dependency_overrides[get_current_user] = override
    app.dependency_overrides[auth_service.get_current_user] = override
    app.dependency_overrides[optional_current_user] = override
    results: list[tuple[str, str, int | str, str]] = []  # (raw, concrete, status, body)
    try:
        spec = app.openapi()
        all_get_paths = sorted(p for p, ops in spec["paths"].items() if "get" in ops)

        # 防呆 1：跳过清单不得有已失效/拼错的条目；规模必须在预算内。
        for skipped in _SKIP_PATHS:
            assert skipped in all_get_paths, f"跳过清单里的 {skipped} 已不存在，请清理"
        assert len(_SKIP_PATHS) <= _MAX_SKIPS, (
            f"跳过清单膨胀到 {len(_SKIP_PATHS)} 条：用跳过绕过问题前先重新评审"
        )
        # 防呆 1b：依赖故障豁免条目不得随路径改名而静默失效（#301）。
        for exempted_path in _EXPECTED_DEPENDENCY_5XX:
            assert exempted_path in all_get_paths, (
                f"依赖故障豁免清单里的 {exempted_path} 已不存在，请清理/同步"
            )

        param_values = dict(_PATH_PARAM_VALUES)
        param_values.update(
            {
                "org_id": str(org_id),
                "organization_id": str(org_id),
                "user_id": str(user_id),
            }
        )

        scanned = [p for p in all_get_paths if p not in _SKIP_PATHS]
        # 防呆 2：表面规模不得静默缩水（路由收集异常会让"全绿"变得毫无意义）。
        assert len(scanned) >= _MIN_SCANNED_ENDPOINTS, (
            f"只扫到 {len(scanned)} 个 GET 端点（预期 ≥ {_MIN_SCANNED_ENDPOINTS}），"
            "路由收集出了问题？"
        )
        # 防呆 3：鉴权防呆清单自身不得随路径改名而静默失效（评审 m1）。
        for guarded in _AUTH_DEPENDENT_PATHS:
            assert guarded in scanned, (
                f"鉴权防呆清单里的 {guarded} 不在扫描集合：路径被改名/移出？请同步清单"
            )

        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", timeout=60
        ) as client:
            for raw in scanned:
                concrete = _concrete_path(raw, param_values)
                query_params = _required_query_params(spec["paths"][raw]["get"], param_values)
                try:
                    response = await client.get(concrete, params=query_params)
                    results.append((raw, concrete, response.status_code, response.text[:400]))
                except Exception as exc:  # 连接层失败（超时/传输异常）同样算 5xx 级别的失败
                    results.append((raw, concrete, f"{type(exc).__name__}: {exc}", ""))
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(auth_service.get_current_user, None)
        app.dependency_overrides.pop(optional_current_user, None)

    # 断言 1（本用例的本体）：没有任何 GET 返回 5xx —— 仅 `_EXPECTED_DEPENDENCY_5XX`
    # 登记过的「依赖故障如实 5xx」（路径 + 状态码双匹配）例外。
    def _exempt(raw: str, code: int | str) -> bool:
        return isinstance(code, int) and _EXPECTED_DEPENDENCY_5XX.get(raw) == code

    failures = [
        r for r in results if (not isinstance(r[2], int) or r[2] >= 500) and not _exempt(r[0], r[2])
    ]
    if failures:
        lines = "\n".join(
            f"  [{status}] {raw}\n      请求: {concrete}\n      响应: {body}"
            for raw, concrete, status, body in failures
        )
        pytest.fail(f"API 表面扫描发现 {len(failures)} 个 5xx/连接层失败的 GET 端点：\n{lines}")

    # 断言 1b：豁免不得空转 —— /files 在 MinIO 不可达时必须真的走 503 分支
    # （没命中 = #301 的故障分流被回退/改形，先重审豁免再放行）。
    exempt_hits = [r for r in results if _exempt(r[0], r[2])]
    assert exempt_hits, (
        f"依赖故障豁免未命中：{_EXPECTED_DEPENDENCY_5XX} —— /files 未在 MinIO 不可达时"
        "返回 503（#301 的对象缺失/存储故障分流被回退？）"
    )

    # 断言 2：鉴权必须真的打通（防「静默 401 缩水」——处理器没被执行时扫描是空转）。
    by_raw = {raw: status for raw, _, status, _ in results}
    unauthenticated = [p for p in _AUTH_DEPENDENT_PATHS if p in by_raw and by_raw[p] in (401, 403)]
    assert not unauthenticated, (
        f"以下端点未通过鉴权（处理器未被执行，扫描面静默缩水）：{unauthenticated}\n"
        "检查三个鉴权 override key 是否仍覆盖仓库里的全部鉴权风格"
    )
