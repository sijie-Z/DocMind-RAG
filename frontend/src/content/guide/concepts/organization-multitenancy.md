DocMind 是组织级多租户系统：数据、检索、会话、Agent 任务与缓存都按组织隔离。

## 组织与成员

- 组织有名称、描述、颜色、层级（父组织/层级/排序）与所有者。
- 用户与组织是多对多关系；每个用户还有一个「当前组织」（`users.organization_id`）。
- 注册时系统会自动创建一个个人组织并把用户加入为成员；但注册流程不会自动授予该用户组织管理员角色。
- 切换组织会更新当前组织，并校验成员关系；非成员会被拒绝（403），平台超级管理员例外。

## 数据隔离范围

- 文档与文件下载：按组织校验。
- 知识库检索：Elasticsearch 查询带组织过滤。
- 会话：个人会话，且只显示当前组织（以及历史无组织）的会话；平台超级管理员可读取本人跨组织的会话。
- Agent 任务与检查点：按用户 + 组织严格匹配。
- RAG 缓存：按组织命名空间，缺少组织 ID 直接拒绝。
- 提示词：系统模板全局可见；组织模板本组织可见；个人模板仅创建者可见。
- 例外：系统监控与平台统计是全局视角，仅平台超级管理员可用。

## 角色与权限

- `SysAdmin`：平台超级管理员，拥有全部权限（`users.is_superuser`）。
- `User`：普通成员，可上传/查看文档、使用知识库与提示词。
- `OrgAdmin`：组织管理员，按「用户 + 组织 + 角色」绑定，只在该组织内生效。

「管理员」在界面上可能指组织管理员，也可能指平台超级管理员；需要全局配置或跨组织操作时，通常只有平台超级管理员可做。

## 依据（代码事实）

- `backend/app/models/organization.py`（组织与成员关系）
- `backend/app/models/user.py`（当前组织字段）
- `backend/app/core/security.py`（角色判定与组织校验）
- `backend/app/services/session_service.py`（会话可见范围）
- `backend/app/models/rbac.py`、`backend/app/services/permission_service.py`（角色与权限）
- `backend/app/api/v1/endpoints/users.py`（切换组织）
- `backend/app/api/v1/endpoints/auth.py`（注册创建个人组织）
