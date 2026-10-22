"""
DocMind AI知识库系统 - API路由模块
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    agent,
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

# 创建API路由
api_router = APIRouter()

# 注册各个模块的路由
api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(users.router, prefix="/users", tags=["用户管理"])
api_router.include_router(files.router, prefix="/files", tags=["文件管理"])
api_router.include_router(documents.router, prefix="/documents", tags=["文档管理(RAG)"])
api_router.include_router(chat.router, prefix="/chat", tags=["聊天"])
api_router.include_router(knowledge.router, prefix="/knowledge", tags=["知识库"])
api_router.include_router(organizations.router, prefix="/organizations", tags=["组织管理"])
api_router.include_router(organization_settings.router, prefix="/organizations", tags=["组织设置"])
api_router.include_router(monitoring.router, prefix="/monitoring", tags=["系统监控"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["通知管理"])
api_router.include_router(prompts.router, prefix="/prompts", tags=["提示词管理"])
api_router.include_router(token_usage.router, prefix="/token-usage", tags=["Token用量"])
api_router.include_router(manuals.router, prefix="/manuals", tags=["操作手册"])
api_router.include_router(workflow.router, prefix="/workflows", tags=["Agent工作流"])
api_router.include_router(llm_config.router, prefix="/llm-config", tags=["智能Agent"])
api_router.include_router(curated_skills.router, prefix="/curated-skills", tags=["智能Agent"])
api_router.include_router(memory.router, prefix="/memory", tags=["Agent记忆"])
api_router.include_router(agent.router, prefix="/agent", tags=["智能Agent"])
# issue #476：会话 fork / 谱系。与 /agent/sessions 共用同一批数据行与可见范围
# （session_service），独立前缀是 issue 指定的 API 形态。
api_router.include_router(sessions.router, prefix="/sessions", tags=["会话分叉"])
api_router.include_router(commands.router, prefix="/commands", tags=["Agent命令"])
api_router.include_router(mcp.router, prefix="/mcp", tags=["MCP"])
api_router.include_router(scheduled_tasks.router, prefix="/scheduled-tasks", tags=["定时任务"])
api_router.include_router(demo.router, prefix="/demo", tags=["示例数据"])
api_router.include_router(user_settings.router, prefix="/user", tags=["用户设置"])
