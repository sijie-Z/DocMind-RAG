from enum import Enum

from pydantic import BaseModel, ConfigDict


class PermissionType(str, Enum):
    """
    系统权限枚举定义
    """
    # === 提示词管理权限 (Prompts) ===
    VIEW_PROMPT = "view_prompt"
    CREATE_PROMPT = "create_prompt"
    UPDATE_PROMPT = "update_prompt"
    DELETE_PROMPT = "delete_prompt"
    MANAGE_SYSTEM_PROMPTS = "manage_system_prompts"

    # === 知识库权限 (Knowledge) - 预判你也需要这些 ===
    VIEW_KNOWLEDGE = "view_knowledge"
    CREATE_KNOWLEDGE = "create_knowledge"
    UPDATE_KNOWLEDGE = "update_knowledge"
    DELETE_KNOWLEDGE = "delete_knowledge"

    # === 文件权限 (Files) ===
    UPLOAD_FILE = "upload_file"
    DELETE_FILE = "delete_file"
    VIEW_FILE = "view_file"

    # === 聊天权限 (Chat) ===
    CHAT = "chat"

    # === 用户与系统管理 ===
    MANAGE_USERS = "manage_users"
    MANAGE_ROLES = "manage_roles"
    VIEW_DASHBOARD = "view_dashboard"

class RoleBase(BaseModel):
    name: str
    description: str | None = None
    permissions: list[PermissionType] = []

class RoleCreate(RoleBase):
    pass

class RoleUpdate(RoleBase):
    pass

class RoleResponse(RoleBase):
    id: int
    model_config = ConfigDict(from_attributes=True)

