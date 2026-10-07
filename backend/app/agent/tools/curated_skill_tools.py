"""技能工具（issue #200）：兑现 SKILL.md 里"调用 load_skill_reference 函数"的承诺。

背景：`curated_skills.py` 生成的技能摘要里写着"调用 load_skill_reference 函数获取"
参考文档，但**该函数在全仓根本不存在**——技能文件在引导模型调用一个不存在的工具。
本模块提供两个真工具（渐进加载，与索引注入配套）：

- `list_skills`：列出全部技能（名称/描述/参考数）；
- `load_skill_reference`：按名加载——只给 skill_name 取技能指南（不含参考正文），
  再给 reference 取具体参考文档。

tag 为 "skill"：主代理可见；子代理（search/analysis 工具集）看不到 ✓。
"""
import logging

from app.agent.curated_skills import curated_skill_registry
from app.agent.registry import ToolResult, register_tool

logger = logging.getLogger(__name__)


@register_tool(
    name="list_skills",
    description=(
        "列出全部可用技能（名称、描述、参考文档数）。技能是特定任务的完整作业指南，"
        "详情用 load_skill_reference 工具按名加载。"
    ),
    parameters={"type": "object", "properties": {}},
    tags=["skill"],
    timeout=15.0,
)
async def list_skills() -> str:
    curated_skill_registry.load_all()
    summaries = curated_skill_registry.list_summaries()
    if not summaries:
        return "当前没有任何可用技能。"
    lines = ["可用技能："]
    for s in summaries:
        lines.append(f"- {s['name']}: {s['description']}（{s['reference_count']} 篇参考文档）")
    return "\n".join(lines)


@register_tool(
    name="load_skill_reference",
    description=(
        "加载技能的指南或参考文档。参数 skill_name 必填（可用 list_skills 查询）；"
        "只给 skill_name 返回技能指南；再给 reference 返回对应的参考文档全文。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "skill_name": {"type": "string", "description": "技能名称"},
            "reference": {
                "type": "string",
                "description": "参考文档名（可选；不传则返回技能指南）",
                "default": "",
            },
        },
        "required": ["skill_name"],
    },
    tags=["skill"],
    timeout=15.0,
)
async def load_skill_reference(skill_name: str, reference: str = "") -> str | ToolResult:
    curated_skill_registry.load_all()
    skill = curated_skill_registry.get(skill_name)
    if skill is None:
        return ToolResult.fail(
            code="not_found",
            message=(
                f"技能不存在：{skill_name}。可用技能："
                f"{', '.join(curated_skill_registry.skill_names) or '（无）'}"
            ),
        )
    if not reference:
        # 技能指南（正文，不含参考文档内容——渐进加载的第一步）
        return skill.get_full_prompt(None)
    content = curated_skill_registry.load_reference(skill_name, reference)
    if content is None:
        return ToolResult.fail(
            code="not_found",
            message=(
                f"技能 {skill_name} 没有名为 {reference} 的参考文档。"
                f"可用：{', '.join(skill.references) or '（无）'}"
            ),
        )
    return content
