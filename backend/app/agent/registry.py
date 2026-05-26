"""Tool registry — self-registering tool system with JSON schemas.

Each tool is a function decorated with @register_tool that declares its
name, description, and parameter schema (JSON Schema format). The registry
discovers tools at import time and provides them to the LLM as function
definitions.

Usage:
    from app.agent.registry import register_tool, tool_registry

    @register_tool(
        name="search_docs",
        description="Search the knowledge base for relevant documents.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    )
    async def search_docs(query: str, **ctx) -> str:
        ...
"""
import inspect
import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ToolEntry:
    """A registered tool with its metadata and handler."""
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Coroutine[Any, Any, str]]
    tags: list[str] = field(default_factory=list)
    requires_auth: bool = False

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Central registry of all available tools."""

    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable[..., Coroutine[Any, Any, str]],
        tags: list[str] | None = None,
        requires_auth: bool = False,
    ) -> ToolEntry:
        entry = ToolEntry(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            tags=tags or [],
            requires_auth=requires_auth,
        )
        self._tools[name] = entry
        logger.debug(f"Registered tool: {name}")
        return entry

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def list_tools(self, tags: list[str] | None = None) -> list[ToolEntry]:
        if not tags:
            return list(self._tools.values())
        return [t for t in self._tools.values() if any(tag in t.tags for tag in tags)]

    def to_openai_tools(self, tags: list[str] | None = None) -> list[dict[str, Any]]:
        """Export tools as OpenAI function-calling definitions."""
        return [t.to_openai_schema() for t in self.list_tools(tags)]

    async def execute(self, name: str, arguments: dict[str, Any], **context) -> str:
        """Execute a tool by name with the given arguments."""
        entry = self._tools.get(name)
        if not entry:
            return f"Error: unknown tool '{name}'. Available: {', '.join(self._tools.keys())}"
        try:
            sig = inspect.signature(entry.handler)
            params = list(sig.parameters.keys())
            # Pass context kwargs that the handler accepts
            ctx_kwargs = {k: v for k, v in context.items() if k in params}
            result = await entry.handler(**arguments, **ctx_kwargs)
            return result if isinstance(result, str) else str(result)
        except Exception as e:
            logger.error(f"Tool '{name}' failed: {e}", exc_info=True)
            return f"Error executing {name}: {type(e).__name__}: {e}"


# Global singleton
tool_registry = ToolRegistry()


def register_tool(
    name: str,
    description: str,
    parameters: dict[str, Any],
    tags: list[str] | None = None,
    requires_auth: bool = False,
):
    """Decorator to register a function as a tool."""
    def decorator(func: Callable[..., Coroutine[Any, Any, str]]):
        tool_registry.register(
            name=name,
            description=description,
            parameters=parameters,
            handler=func,
            tags=tags,
            requires_auth=requires_auth,
        )
        return func
    return decorator
