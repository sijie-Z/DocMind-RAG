"""
DocMind AI知识库系统 - API路由模块

issue #479：路由按 bundle 分组。``ROUTER_SPECS`` 是唯一的路由清单，
``build_router(bundles)`` 按 profile 声明的 bundle 集合挂载模块；
``api_router = build_router(ALL_BUNDLES)`` 保留为全量兼容导出（行为不变）。

``backend/scripts/check_doc_facts.py`` 的 ``endpoint_modules`` 派生口径优先
解析本文件的 ``ROUTER_SPECS`` 键（声明顺序），不再依赖 ``include_router``
调用数——新增/删除模块只改这一张表。
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agent,
    api_keys,
    auth,
    chat,
    commands,
    curated_skills,
    demo,
    documents,
    files,
    knowledge,
    llm_config,
    manuals,
    mcp,
    memory,
    monitoring,
    notifications,
    organization_settings,
    organizations,
    prompts,
    scheduled_tasks,
    sessions,
    token_usage,
    user_settings,
    users,
    workflow,
)
from app.rpc.http import router as rpc_http_router

# 模块名 → (router, prefix, tags, bundle)。顺序尽量保持历史 include 顺序，
# 保证 OpenAPI 输出稳定。
ROUTER_SPECS: dict[str, tuple[APIRouter, str, list[str], str]] = {
    "auth": (auth.router, "/auth", ["认证"], "core"),
    "users": (users.router, "/users", ["用户管理"], "core"),
    # issue #480：API Key 管理（core bundle，JWT 与 dm_ key 均可管理自己的 key）。
    "api_keys": (api_keys.router, "/api-keys", ["API Key"], "core"),
    "files": (files.router, "/files", ["文件管理"], "rag"),
    "documents": (documents.router, "/documents", ["文档管理(RAG)"], "rag"),
    "chat": (chat.router, "/chat", ["聊天"], "rag"),
    "knowledge": (knowledge.router, "/knowledge", ["知识库"], "rag"),
    "organizations": (organizations.router, "/organizations", ["组织管理"], "core"),
    "organization_settings": (
        organization_settings.router,
        "/organizations",
        ["组织设置"],
        "core",
    ),
    "monitoring": (monitoring.router, "/monitoring", ["系统监控"], "observability"),
    "notifications": (notifications.router, "/notifications", ["通知管理"], "core"),
    "prompts": (prompts.router, "/prompts", ["提示词管理"], "rag"),
    "token_usage": (token_usage.router, "/token-usage", ["Token用量"], "agent"),
    "manuals": (manuals.router, "/manuals", ["操作手册"], "rag"),
    "workflow": (workflow.router, "/workflows", ["Agent工作流"], "agent"),
    "llm_config": (llm_config.router, "/llm-config", ["智能Agent"], "agent"),
    "curated_skills": (curated_skills.router, "/curated-skills", ["智能Agent"], "agent"),
    "memory": (memory.router, "/memory", ["Agent记忆"], "rag"),
    "agent": (agent.router, "/agent", ["智能Agent"], "agent"),
    # issue #476：会话 fork / 谱系。与 /agent/sessions 共用同一批数据行与可见范围
    # （session_service），独立前缀是 issue 指定的 API 形态。
    "sessions": (sessions.router, "/sessions", ["会话分叉"], "agent"),
    "commands": (commands.router, "/commands", ["Agent命令"], "agent"),
    "mcp": (mcp.router, "/mcp", ["MCP"], "agent"),
    # issue #480：HTTP JSON-RPC（仅 web profile 的 agent bundle；端点内按
    # RPC_HTTP_ENABLED 门控，关闭时 404）。sdk profile 只提供 stdio，不跑 HTTP。
    "rpc": (rpc_http_router, "/rpc", ["JSON-RPC"], "agent"),
    "scheduled_tasks": (scheduled_tasks.router, "/scheduled-tasks", ["定时任务"], "agent"),
    "demo": (demo.router, "/demo", ["示例数据"], "agent"),
    "user_settings": (user_settings.router, "/user", ["用户设置"], "core"),
}

ALL_BUNDLES: list[str] = ["core", "rag", "agent", "observability"]


def build_router(bundles: list[str]) -> APIRouter:
    """按 bundle 集合挂载路由；未知 bundle 名抛 ``ValueError``。"""
    unknown = [name for name in bundles if name not in ALL_BUNDLES]
    if unknown:
        raise ValueError(f"未知 bundle: {', '.join(unknown)}（可选: {', '.join(ALL_BUNDLES)}）")
    selected = set(bundles)
    router = APIRouter()
    for module_router, prefix, tags, bundle in ROUTER_SPECS.values():
        if bundle not in selected:
            continue
        router.include_router(module_router, prefix=prefix, tags=tags)
    return router


# 兼容导出：全量路由（现有 `from app.api.v1.router import api_router` 不变）。
api_router = build_router(ALL_BUNDLES)
