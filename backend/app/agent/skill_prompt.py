"""技能索引进提示词的构造（issue #200）：`skills/*/SKILL.md` 此前从不进任何提示词。

只注入**索引**（名称 / 描述 / 参考文档数）——完整指南与参考文档按需经
`load_skill_reference` 工具渐进加载。这与 curated_skills 模块的设计意图一致：
避免把全部技能正文一次性塞进 prompt 造成 token 爆炸。
"""
from app.agent.curated_skills import curated_skill_registry


def build_skills_index(*, max_skills: int = 10) -> str:
    """构造可注入系统提示词的技能索引；无技能时返回空串（调用方按需追加）。"""
    curated_skill_registry.load_all()
    summaries = curated_skill_registry.list_summaries()[:max_skills]
    if not summaries:
        return ""
    lines = [
        "## 可用技能",
        (
            "以下技能提供特定任务的完整作业指南。需要时用 load_skill_reference 工具"
            "渐进加载：先给 skill_name 取指南，再给 skill_name + reference 取参考文档。"
        ),
    ]
    for s in summaries:
        lines.append(f"- {s['name']}: {s['description']}（{s['reference_count']} 篇参考文档）")
    return "\n".join(lines)
