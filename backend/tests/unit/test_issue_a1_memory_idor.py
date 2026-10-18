"""A1 安全回归：工作流记忆节点 IDOR —— 跨租户记忆读写必须失败。

攻击路径（修复前实测泄露）：org 2 / user 9 的用户把自己的工作流记忆节点
`agentId` 配成受害者（org 1 / user 77）的完整命名空间 `1:77:victim-agent`。
修复前 `MemoryNodeExecutor` 直接 `get_memory_system(agentId)`，整串即全局
记忆字典 key → 攻击者可读取（及写入）受害者的长期/短期记忆。

修复：
1. `get_memory_system(org_id, user_id, agent_id)` 在函数内部拼装
   `org:user:agent`，调用方必须显式传租户上下文，不再接受完整命名空间字符串；
2. 工作流记忆节点的 agentId 只接受普通作用域标签（拒绝冒号命名空间注入），
   租户上下文必须由引擎按认证用户注入，缺失即拒绝（fail-closed）；
3. `WorkflowEngine(..., user_id=...)` 把 (org, user) 注入记忆节点执行器。

同族入口（导入端点）：`import_data` 忽略载荷里的 `agent_id`——修复前它会
重写 `self.agent_id`，使 `agent:memory:<受害者命名空间>` 被请求体直接指定，
导入接口即可跨租户覆写 Redis 持久化记忆。对应
`TestImportCannotRekeyNamespace::test_import_payload_agent_id_cannot_override_persist_key`。

变异验证：把 `MemoryNodeExecutor` 的 agentId 校验与租户上下文回退为
`get_memory_system(agent_id)` → `test_cross_tenant_namespace_agent_id_rejected`
红（pytest.raises 处 DID NOT RAISE，且攻击者读到受害者记忆）；恢复
`import_data` 中的 `self.agent_id = data.get("agent_id", ...)` →
`test_import_payload_agent_id_cannot_override_persist_key` 红
（saved 出现 `agent:memory:1:77:victim-agent`）。
"""
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.schemas.workflow import WorkflowConfig, WorkflowEdge, WorkflowNode
from app.services.memory_service import (
    AgentMemorySystem,
    get_memory_system,
    memory_systems,
)
from app.services.workflow_engine import MemoryNodeExecutor, WorkflowEngine

SECRET = "SECRET-VICTIM-SALARY-42"


@pytest.fixture()
def isolated_cache():
    """隔离全局记忆系统缓存，避免用例间命名空间互相污染。"""
    original = dict(memory_systems)
    memory_systems.clear()
    try:
        yield
    finally:
        memory_systems.clear()
        memory_systems.update(original)


def _state(text: str = "hello", messages: list | None = None) -> dict:
    return {
        "input": {"text": text},
        "messages": messages or [],
        "current_node": None,
        "node_outputs": {},
        "context": {},
        "errors": [],
        "memory": {},
        "iteration_count": 0,
        "tool_results": [],
    }


def _memory_node(agent_id, action: str = "retrieve") -> WorkflowNode:
    return WorkflowNode(
        id="mem1",
        type="memory",
        data={
            "agentId": agent_id,
            "action": action,
            "memoryType": "short_term",
            "topK": 5,
        },
        position={"x": 0, "y": 0},
    )


def _executor(agent_id, action: str = "retrieve", org_id: int | None = 2, user_id: int | None = 9) -> MemoryNodeExecutor:
    executor = MemoryNodeExecutor(_memory_node(agent_id, action))
    executor.organization_id = org_id
    executor.user_id = user_id
    return executor


class TestCrossTenantAttackBlocked:
    @pytest.mark.asyncio
    async def test_cross_tenant_namespace_agent_id_rejected(self, isolated_cache):
        """攻击路径：节点 agentId=受害者完整命名空间 → 明确报错，绝不读到数据。"""
        from app.agent.memory_bridge import AgentMemoryBridge

        # 受害者（org 1 / user 77）自己写入的私有记忆
        victim = AgentMemoryBridge(agent_id="victim-agent", organization_id=1, user_id=77)
        await victim.system.remember(SECRET, memory_type="short_term")

        # 攻击者（org 2 / user 9）把节点 agentId 配成受害者的命名空间三元组
        attacker = _executor("1:77:victim-agent")

        with pytest.raises(ValueError, match="agentId"):
            await attacker.execute(_state(SECRET))

        # 受害者记忆必须原样保留（攻击者既未读走也未覆写）
        victim_reader = AgentMemoryBridge(
            agent_id="victim-agent", organization_id=1, user_id=77
        )
        assert any(
            item.content == SECRET
            for item in victim_reader.system.short_term.get_recent(20)
        ), "受害者记忆不得被跨租户节点改动"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "hostile",
        [
            "1:77:victim-agent",   # 完整命名空间三元组
            "session:abc",         # 会话作用域字面量
            "None:77:victim",      # 无组织命名空间字面量
            "a b",                 # 空白
            "x" * 65,              # 超长
            123,                   # 非字符串（节点数据可被配置成任意 JSON）
        ],
    )
    async def test_namespace_injection_shapes_all_rejected(self, isolated_cache, hostile):
        """任何试图携带命名空间结构的 agentId 都必须被明确拒绝。"""
        with pytest.raises(ValueError, match="agentId"):
            await _executor(hostile).execute(_state())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("empty", [None, "", "   "])
    async def test_empty_agent_id_falls_back_to_default_scope(self, isolated_cache, empty):
        """空 agentId 按 memory API 同口径回退 "default"，不得拼出怪 key。"""
        executor = _executor(empty, action="store")
        messages = [HumanMessage(content="默认作用域记忆"), AIMessage(content="收到")]
        await executor.execute(_state("默认作用域记忆", messages))

        default_system = get_memory_system(2, 9, "default")
        assert any(
            "默认作用域记忆" in item.content
            for item in default_system.short_term.get_recent(10)
        )

    @pytest.mark.asyncio
    async def test_missing_tenant_context_fails_closed(self, isolated_cache):
        """引擎未注入租户上下文时，记忆节点必须拒绝执行（绝不回落到全局 key）。"""
        with pytest.raises(ValueError, match="组织/用户上下文"):
            await _executor("assistant", org_id=None, user_id=None).execute(_state())

    def test_engine_injects_authenticated_tenant_context(self):
        """WorkflowEngine 把服务端推导的 (org, user) 注入记忆节点执行器。"""
        node = _memory_node("assistant")
        engine = WorkflowEngine(
            WorkflowConfig(nodes=[node], edges=[]),
            organization_id=2,
            user_id=9,
        )
        executor = engine.get_executor(node)
        assert isinstance(executor, MemoryNodeExecutor)
        assert executor.organization_id == 2
        assert executor.user_id == 9

    @pytest.mark.asyncio
    async def test_engine_reports_attack_node_as_failed(self, isolated_cache):
        """引擎边界：攻击节点不得静默通过——总体 success=False 且错误信息点明 agentId。"""
        entry = WorkflowNode(
            id="in1",
            type="input",
            data={"prompt": "{{input}}"},
            position={"x": 0, "y": 0},
        )
        engine = WorkflowEngine(
            WorkflowConfig(
                nodes=[entry, _memory_node("1:77:victim-agent")],
                edges=[WorkflowEdge(id="e1", source="in1", target="mem1")],
            ),
            organization_id=2,
            user_id=9,
        )

        result = await engine.execute({"text": SECRET})

        assert result["success"] is False, "攻击节点必须让本次执行如实失败"
        assert "agentId" in (result.get("error") or ""), result


class TestSameTenantNoRegression:
    @pytest.mark.asyncio
    async def test_store_then_retrieve_within_tenant(self, isolated_cache):
        """同 org 同 user 的正常记忆写入/检索零回归。"""
        store = _executor("assistant", action="store")
        messages = [
            HumanMessage(content="我的报销标准是每天300元"),
            AIMessage(content="已记录：每天300元"),
        ]
        await store.execute(_state("我的报销标准是每天300元", messages))

        retrieve = _executor("assistant", action="retrieve")
        result = await retrieve.execute(_state("报销标准"))
        memories = result["node_outputs"]["mem1"]["memories"]

        assert memories, "同租户同 agent 的记忆存储后必须可检索到（正常功能回归）"
        assert any("报销标准" in m["content"] for m in memories)

    @pytest.mark.asyncio
    async def test_same_agent_label_in_other_tenant_cannot_read(self, isolated_cache):
        """同一 agentId 标签在不同租户下互不可见（命名空间按 org+user 隔离）。"""
        store = _executor("assistant", action="store")
        await store.execute(_state("租户2的私有内容", [
            HumanMessage(content="租户2的私有内容"),
            AIMessage(content="收到"),
        ]))

        # 另一个组织（org 3）同名 agent 标签检索不到
        outsider = _executor("assistant", action="retrieve", org_id=3, user_id=9)
        result = await outsider.execute(_state("私有内容"))
        memories = result["node_outputs"]["mem1"]["memories"]
        assert memories == [], f"跨租户不得读到同名 agent 记忆：{memories}"


class TestFactoryTenantScoping:
    def test_namespace_format_and_isolation(self, isolated_cache):
        """工厂按 (org, user, agent) 拼命名空间；任一租户维度不同即不同实例。"""
        a = get_memory_system(1, 77, "agent")
        b = get_memory_system(2, 77, "agent")
        c = get_memory_system(1, 78, "agent")

        assert a.agent_id == "1:77:agent"
        assert b.agent_id == "2:77:agent"
        assert c.agent_id == "1:78:agent"
        assert a is not b
        assert a is not c

    @pytest.mark.asyncio
    async def test_memory_endpoint_wraps_hostile_agent_id_under_caller_namespace(
        self, isolated_cache
    ):
        """不变式（修复前后均成立）：URL 路径形态的 agent_id 只能嵌在调用者租户下。"""
        from app.api.v1.endpoints.memory import _get_user_memory_system

        user = SimpleNamespace(organization_id=2, id=9)
        system = await _get_user_memory_system(user, "1:77:victim-agent")
        assert system.agent_id == "2:9:1:77:victim-agent"


class TestImportCannotRekeyNamespace:
    @pytest.mark.asyncio
    async def test_import_payload_agent_id_cannot_override_persist_key(
        self, isolated_cache, monkeypatch
    ):
        """A1 同族入口：导入端点请求体里的 data.agent_id 不得改写系统命名空间。

        修复前 `import_data` 用 `data.get("agent_id")` 重写 `self.agent_id`，
        随后的 `_save_to_redis` 会把 `agent:memory:<攻击者指定的 key>`（可达
        他人完整命名空间）写进 Redis —— 请求体即可跨租户覆写持久化记忆。
        """
        from app.api.v1.endpoints.memory import import_memory
        from app.schemas.memory import ImportMemoriesRequest

        saved: list[str] = []

        async def fake_save(self):
            saved.append(self._persist_key)

        monkeypatch.setattr(AgentMemorySystem, "_save_to_redis", fake_save)

        body = ImportMemoriesRequest(
            data={"agent_id": "1:77:victim-agent", "long_term": {}}
        )
        result = await import_memory(
            agent_id="attacker",
            body=body,
            current_user=SimpleNamespace(organization_id=2, id=9),
        )

        assert result["success"] is True
        system = get_memory_system(2, 9, "attacker")
        assert system.agent_id == "2:9:attacker", "载荷不得改写服务端分配的命名空间"
        assert saved == ["agent:memory:2:9:attacker"], saved
        assert all("1:77:victim-agent" not in key for key in saved), (
            "导入不得写向受害者 Redis 记忆 key"
        )
