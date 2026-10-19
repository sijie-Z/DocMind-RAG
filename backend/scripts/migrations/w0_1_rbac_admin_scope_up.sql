-- ============================================================================
-- W0-1 · RBAC 全局旁路收口：users.role='admin' 账号迁移（升级脚本）
--
-- 路线依据：ADR-001 D3（per-org RBAC + 显式平台管理员）。
--
-- 语义：
--   1) 平台管理员只认显式 users.is_superuser；本脚本把运营登记的
--      w0_1_rbac_platform_allowlist 账号（以及已经是 is_superuser 的账号）
--      标记为 platform_admin 并保留/置 is_superuser=1；
--   2) 其余 role='admin' 且归属某组织的账号 → 回填该组织的 OrgAdmin
--      角色关联（user_organization_role_association），之后只在本组织内生效；
--   3) 所有被本脚本处理的账号 users.role 统一降为 'user'（该字段不再参与
--      任何鉴权，仅作展示标签）；
--   4) 无组织、又未被登记为平台管理员的 role='admin' 账号 → 'demoted'，
--      不获得任何管理员权限（fail closed）。
--
-- 使用步骤（MySQL）：
--   0. 前置：应用至少启动过一次（permissions / SysAdmin / User 角色已初始化）；
--      部署顺序 = 先发布本版本代码，再执行本脚本——旧代码不认识 OrgAdmin
--      组织角色，先迁数据会让组织管理员在旧代码下短暂失去管理能力。
--   1. 先看清单：
--      SELECT id, username, role, organization_id, is_superuser
--      FROM users WHERE LOWER(role) = 'admin';
--   2. 把平台管理员用户名登记到 w0_1_rbac_platform_allowlist（见下方示例），
--      再执行本脚本：
--      mysql ... < w0_1_rbac_admin_scope_up.sql
--   3. 复核审计表：
--      SELECT * FROM w0_1_rbac_admin_backup ORDER BY user_id;
--
-- 回滚：w0_1_rbac_admin_scope_down.sql（依据备份表精确恢复；备份表保留备查）。
-- 说明：脚本可安全重放——已迁移账号在重放时不再命中 role='admin'，
--       角色/权限/关联的写入均为 insert-if-missing。
-- ============================================================================

-- 平台管理员登记表：执行前按需插入用户名（可留空 = 不额外提升任何人）。
CREATE TABLE IF NOT EXISTS w0_1_rbac_platform_allowlist (
    username VARCHAR(50) NOT NULL PRIMARY KEY
);

-- 示例（取消注释并改成真实用户名后执行）：
-- INSERT INTO w0_1_rbac_platform_allowlist (username) VALUES ('platform-admin');

-- 迁移审计/回滚依据：每个被处理的账号一行（user_id 唯一，重放不覆盖首次快照）。
CREATE TABLE IF NOT EXISTS w0_1_rbac_admin_backup (
    user_id INTEGER NOT NULL PRIMARY KEY,
    username VARCHAR(50),
    old_role VARCHAR(20),
    old_is_superuser INTEGER,
    organization_id INTEGER,
    classification VARCHAR(20),
    association_existed_before INTEGER NOT NULL DEFAULT 0,
    backed_up_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- 0. 审计清单（执行前的现状快照，输出供复核）
-- ---------------------------------------------------------------------------
SELECT id, username, role, organization_id, is_superuser
FROM users
WHERE LOWER(role) = 'admin'
ORDER BY id;

-- ---------------------------------------------------------------------------
-- 1. 种子角色：OrgAdmin（组织级，is_system_role=0）+ 权限关联
-- ---------------------------------------------------------------------------
INSERT INTO roles (name, description, is_system_role, created_at, updated_at)
SELECT 'OrgAdmin', '组织管理员（组织级，仅限本组织）', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM roles WHERE name = 'OrgAdmin');

INSERT INTO role_permission_association (role_id, permission_id)
SELECT r.id, p.id
FROM roles r, permissions p
WHERE r.name = 'OrgAdmin'
  AND p.name IN (
      'user:read',
      'user:manage_roles',
      'organization:read',
      'organization:update',
      'organization:manage_members',
      'document:upload',
      'document:read',
      'document:update',
      'document:delete',
      'knowledge_base:read',
      'knowledge_base:search',
      'knowledge_base:manage',
      'knowledge_base:rebuild_index',
      'prompt:read',
      'prompt:create',
      'prompt:update',
      'prompt:delete'
  )
  AND NOT EXISTS (
      SELECT 1 FROM role_permission_association a
      WHERE a.role_id = r.id AND a.permission_id = p.id
  );

-- ---------------------------------------------------------------------------
-- 2. 备份 + 分类（首次快照；重放不覆盖）
-- ---------------------------------------------------------------------------
INSERT INTO w0_1_rbac_admin_backup (
    user_id, username, old_role, old_is_superuser, organization_id,
    classification, association_existed_before
)
SELECT
    u.id,
    u.username,
    u.role,
    u.is_superuser,
    u.organization_id,
    CASE
        WHEN u.is_superuser = 1 THEN 'platform_admin'
        WHEN EXISTS (
            SELECT 1 FROM w0_1_rbac_platform_allowlist a
            WHERE a.username = u.username
        ) THEN 'platform_admin'
        WHEN u.organization_id IS NOT NULL THEN 'org_admin'
        ELSE 'demoted'
    END,
    CASE
        WHEN u.organization_id IS NOT NULL AND EXISTS (
            SELECT 1
            FROM user_organization_role_association x, roles r2
            WHERE x.user_id = u.id
              AND x.organization_id = u.organization_id
              AND r2.id = x.role_id
              AND r2.name = 'OrgAdmin'
        ) THEN 1
        ELSE 0
    END
FROM users u
WHERE LOWER(u.role) = 'admin'
  AND NOT EXISTS (
      SELECT 1 FROM w0_1_rbac_admin_backup b WHERE b.user_id = u.id
  );

-- ---------------------------------------------------------------------------
-- 3. 平台管理员：显式 is_superuser
-- ---------------------------------------------------------------------------
UPDATE users
SET is_superuser = 1
WHERE id IN (
    SELECT user_id FROM w0_1_rbac_admin_backup
    WHERE classification = 'platform_admin'
);

-- ---------------------------------------------------------------------------
-- 4. 组织管理员：回填 OrgAdmin 角色关联（仅本组织生效）
-- ---------------------------------------------------------------------------
INSERT INTO user_organization_role_association (user_id, organization_id, role_id)
SELECT b.user_id, b.organization_id, r.id
FROM w0_1_rbac_admin_backup b, roles r
WHERE b.classification = 'org_admin'
  AND r.name = 'OrgAdmin'
  AND NOT EXISTS (
      SELECT 1 FROM user_organization_role_association x
      WHERE x.user_id = b.user_id
        AND x.organization_id = b.organization_id
        AND x.role_id = r.id
  );

-- ---------------------------------------------------------------------------
-- 5. 降级 users.role（权限已由 is_superuser / OrgAdmin 关联承载）
-- ---------------------------------------------------------------------------
UPDATE users
SET role = 'user'
WHERE id IN (SELECT user_id FROM w0_1_rbac_admin_backup);

-- ---------------------------------------------------------------------------
-- 6. 迁移后审计输出
-- ---------------------------------------------------------------------------
SELECT user_id, username, old_role, old_is_superuser, organization_id,
       classification, association_existed_before, backed_up_at
FROM w0_1_rbac_admin_backup
ORDER BY user_id;
