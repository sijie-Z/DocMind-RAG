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
from app.agent.tools import web_search  # noqa: F401
from app.agent.tools import code_execution  # noqa: F401
from app.agent.tools import data_analysis  # noqa: F401
from app.agent.tools import translation  # noqa: F401
from app.agent.tools import utility  # noqa: F401
