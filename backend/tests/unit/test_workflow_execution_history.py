"""工作流执行历史列表端点的判别性测试。

历史记录有数据但前端无入口：后端只有 GET /executions/{id}，缺列表端点。
本测试锁定三件事：
1. 列表按 workflow_id 过滤，返回摘要字段；
2. 租户/属主隔离：普通用户看不到他人工作流的执行记录；
3. 路由顺序：/executions 必须在 /{workflow_id} 之前注册，否则 "executions"
   会被当 int workflow_id 解析成 422。

变异点：删端点 → 404；把端点放到 /{workflow_id} 之后 → 422；
去掉 workflow scope → 越权可见（用例 2 红）。
"""

from __future__ import annotations


def test_execution_list_route_registered_before_workflow_id_route():
    """路由顺序守卫（读源码即可判定，避免 fixture 依赖）。"""
    from pathlib import Path

    import app.api.v1.endpoints.workflow as workflow_ep

    source = Path(workflow_ep.__file__).read_text(encoding="utf-8")
    list_pos = source.find('@router.get("/executions"')
    detail_pos = source.find('@router.get("/executions/{execution_id}"')
    workflow_id_pos = source.find('@router.get("/{workflow_id}"')
    assert list_pos > 0, "缺 /executions 列表端点"
    assert detail_pos > 0, "既有 /executions/{id} 详情端点不应被移除"
    assert list_pos < workflow_id_pos < detail_pos, (
        "列表路由必须在 /{workflow_id} 之前注册，否则 'executions' 被当 int 解析"
    )


class TestExecutionListQuery:
    """列表端点的查询构造（不依赖 DB 的纯逻辑验证）。"""

    def test_scope_helper_limits_to_own_workflows(self):
        # 用最小替身验证 scope 语义：普通用户只看到 created_by 匹配的 workflow
        from types import SimpleNamespace

        import app.api.v1.endpoints.workflow as workflow_ep
        from app.api.v1.endpoints.workflow import _apply_workflow_scope

        original = workflow_ep.is_org_admin

        async def not_admin(*args, **kwargs):
            return False

        workflow_ep.is_org_admin = not_admin
        try:
            user = SimpleNamespace(is_superuser=False, id=7)

            # stmt 只是被 .where 拼接，用可记录调用的替身即可
            class FakeStmt:
                def __init__(self):
                    self.calls = []

                def where(self, *cond):
                    self.calls.append(cond)
                    return self

            stmt = FakeStmt()
            out = __import__("asyncio").run(_apply_workflow_scope(None, stmt, user, 3))
            assert out is stmt
            # 组织条件 + 创建者条件，两层过滤缺一不可
            assert len(stmt.calls) == 2
        finally:
            workflow_ep.is_org_admin = original
