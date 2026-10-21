"""M3 修复验证：agent_id 校验必须允许会话命名空间（`session:<uuid>`）。

修复前：`_AGENT_ID_PATTERN = ^[a-zA-Z0-9_-]{1,64}$` 拒绝冒号，
而 agent 的会话命名空间字面量就是 `session:<session_id>`（service.py 约定），
操作员因此无法用清空接口清理单个会话的记忆。
"""

import uuid
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.memory import _normalize_agent_id, clear_memory
from app.exceptions import ValidationError
from app.services.memory_service import get_memory_system


class TestAgentIdValidation:
    def test_session_scoped_id_validates(self):
        agent_id = f"session:{uuid.uuid4()}"
        assert _normalize_agent_id(agent_id) == agent_id

    def test_plain_ids_still_validate(self):
        assert _normalize_agent_id("default") == "default"
        assert _normalize_agent_id("my-agent_01") == "my-agent_01"
        assert _normalize_agent_id("") == "default"

    @pytest.mark.parametrize(
        "hostile",
        [
            "../../etc",  # 路径穿越
            "..\\..\\windows\\system32",
            "..",
            "a/b",
            "a\\b",
            "foo bar",  # 空白
            "session:ab cd",  # 冒号不能成为空白后门
            "a\x00b",  # NUL
            "x" * 65,  # 超长
        ],
    )
    def test_hostile_ids_rejected(self, hostile):
        with pytest.raises(ValidationError):
            _normalize_agent_id(hostile)


class TestClearMemoryEndpoint:
    @pytest.mark.asyncio
    async def test_clear_working_memory_accepts_session_scoped_id(self):
        """操作员能用 `session:<uuid>` 调用清空接口（修复前会抛 ValidationError）。"""
        user = SimpleNamespace(organization_id=1, id=99)
        result = await clear_memory(
            agent_id=f"session:{uuid.uuid4()}",
            memory_type="working",
            current_user=user,
        )
        assert result["success"] is True
        assert "working" in result["message"]

    @pytest.mark.asyncio
    async def test_clear_removes_session_working_state(self):
        """接口清理的必须是 agent 实际写入的会话命名空间。"""
        user = SimpleNamespace(organization_id=2, id=7)
        session_id = str(uuid.uuid4())
        system = get_memory_system(2, 7, f"session:{session_id}")
        system.working.set_result("s1", {"status": "completed", "result": "x"})

        result = await clear_memory(
            agent_id=f"session:{session_id}",
            memory_type="working",
            current_user=user,
        )

        assert result["success"] is True
        assert system.working.get_results() == {}
