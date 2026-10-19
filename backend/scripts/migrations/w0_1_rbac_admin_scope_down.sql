-- ============================================================================
-- W0-1 · RBAC 全局旁路收口：users.role='admin' 账号迁移（回滚脚本）
--
-- 依据 w0_1_rbac_admin_backup 精确恢复：
--   1) users.is_superuser ← old_is_superuser；
--   2) users.role ← old_role（'admin'）；
--   3) 删除迁移回填的 OrgAdmin 组织角色关联（仅删除
--      association_existed_before=0 的行；迁移前已存在的关联原样保留）；
--   4) OrgAdmin 角色与权限种子保留（无角色关联时不授予任何权限）；
--      allowlist / backup 表保留备查。
--
-- 幂等：可重复执行；重复执行不会改变结果。
-- 用法：mysql ... < w0_1_rbac_admin_scope_down.sql
-- ============================================================================

-- MySQL/SQLite 共同支持的事务起始语法（SQLite 不接受 START TRANSACTION）。
BEGIN;

-- 1. 恢复平台管理员位
UPDATE users
SET is_superuser = (
    SELECT b.old_is_superuser
    FROM w0_1_rbac_admin_backup b
    WHERE b.user_id = users.id
)
WHERE id IN (SELECT user_id FROM w0_1_rbac_admin_backup);

-- 2. 恢复 users.role 展示字段
UPDATE users
SET role = (
    SELECT b.old_role
    FROM w0_1_rbac_admin_backup b
    WHERE b.user_id = users.id
)
WHERE id IN (SELECT user_id FROM w0_1_rbac_admin_backup);

-- 3. 撤销迁移回填的 OrgAdmin 角色关联（保留迁移前已存在的关联）
DELETE FROM user_organization_role_association
WHERE EXISTS (
    SELECT 1
    FROM w0_1_rbac_admin_backup b, roles r
    WHERE b.user_id = user_organization_role_association.user_id
      AND b.organization_id = user_organization_role_association.organization_id
      AND b.classification = 'org_admin'
      AND b.association_existed_before = 0
      AND r.id = user_organization_role_association.role_id
      AND r.name = 'OrgAdmin'
);

COMMIT;

-- 4. 回滚后审计输出
SELECT user_id, username, old_role, old_is_superuser, organization_id,
       classification, association_existed_before, backed_up_at
FROM w0_1_rbac_admin_backup
ORDER BY user_id;
