"""Knowledge 模板：知识库类问句必须绑定工具，且「有哪些文档」走清单。"""

from __future__ import annotations

from app.agent.config import AgentConfig
from app.agent.planner import Planner


def _plan(query: str):
    planner = Planner(openai_client=None, config=AgentConfig())  # type: ignore[arg-type]
    plan = planner._try_structured_planning("p1", query)
    assert plan is not None, f"未命中结构化模板: {query}"
    return plan


class TestKnowledgeTemplate:
    def test_which_documents_binds_list_tool(self) -> None:
        plan = _plan("知识库里有哪些文档？")
        assert plan.steps[0].tool_hint == "list_documents"
        assert plan.steps[0].dependencies == []

    def test_content_question_binds_retrieval_tool(self) -> None:
        plan = _plan("采购合同的付款期限是多少？")
        assert plan.steps[0].tool_hint == "search_knowledge_base"

    def test_every_plan_has_a_tool_step(self) -> None:
        for q in ["知识库里有哪些文档？", "采购合同的付款期限是多少？", "这份资料讲了什么"]:
            step = _plan(q).steps[0]
            assert step.tool_hint, f"{q} 的首步没有绑定工具"

    def test_conclusion_step_waits_for_tool(self) -> None:
        plan = _plan("采购合同的付款期限是多少？")
        assert plan.steps[1].dependencies == ["s1"]
        assert plan.steps[1].tool_hint is None

    def test_summary_queries_keep_research_template(self) -> None:
        plan = _plan("总结一下这几份文档的要点")
        assert plan.steps[0].tool_hint == "search_knowledge_base"
