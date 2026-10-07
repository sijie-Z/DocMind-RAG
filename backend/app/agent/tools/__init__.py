"""Agent tools package — imports all tool modules to trigger registration.

Importing this package automatically registers all tools with the
global tool_registry via the @register_tool decorator.

Tool categories:
    web_search.py    — Web search, webpage fetch
    code_execution.py — Python sandbox, SQL query
    data_analysis.py — Statistical analysis, document comparison
    translation.py   — Language translation, detection
    utility.py       — Math, format conversion, system status
"""

# Import triggers @register_tool for each tool function
# v2.1: 企业协同工具集
from app.agent.tools import (
    code_execution,  # noqa: F401
    curated_skill_tools,  # noqa: F401  — 技能工具（issue #200 接线）
    data_analysis,  # noqa: F401
    delegation,  # noqa: F401  — 子代理委派（issue #199 接线）
    feishu,  # noqa: F401
    mcp_bridge,  # noqa: F401
    skills,  # noqa: F401
    translation,  # noqa: F401
    utility,  # noqa: F401
    web_search,  # noqa: F401
)
