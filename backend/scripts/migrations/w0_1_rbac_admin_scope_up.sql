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
--   2. 登记平台管理员（两种方式任选）：
--      a) 先执行 w0_1_rbac_admin_scope_prep.sql 建 allowlist 并 INSERT 用户名；
--      b) 或直接取消本文件下方 allowlist 示例 INSERT 的注释。
--   3. 执行本脚本：
--      mysql ... < w0_1_rbac_admin_scope_up.sql
--      安全阀：若存在 role='admin' 账号，却没有任何 platform_admin 候选
--      （is_superuser=1 或 allowlist 命中），脚本会在事务中 CHECK 失败并中止，
--      不会写入备份/降级任何账号。补登记后重跑即可。
--   4. 复核审计表：
--      SELECT * FROM w0_1_rbac_admin_backup ORDER BY user_id;
--
-- 回滚：w0_1_rbac_admin_scope_down.sql（依据备份表精确恢复；备份表保留备查）。
-- 补救（分类首次快照冻结）：若首次成功执行后才发现 allowlist 漏登记，
--   重放不会重分类——先执行 down 脚本恢复，再补登记并重跑 up；或依据
--   backup 表手工 UPDATE users SET is_superuser=1 后复核。
-- staging 表（admin_candidates / role_seed / guard）可保留（不影响业务）；
--   确认迁移完成后可手工 DROP。
-- 说明：脚本可安全重放——已迁移账号在重放时不再命中 role='admin'；
--       角色/权限/关联的写入均为「先删目标子集再插入」，不依赖
--       INSERT...SELECT 对目标表的子查询（规避 MySQL 错误 1093）。
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

-- 本次待处理账号（重放安全：先清空再按当前 role='admin' 取候选，
-- 再剔除已备份账号——备份表保留首次快照）。
CREATE TABLE IF NOT EXISTS w0_1_rbac_admin_candidates (
    user_id INTEGER NOT NULL PRIMARY KEY
);

-- 本次待种子的角色名（避免 INSERT...SELECT 子查询读取目标 roles 表）。
CREATE TABLE IF NOT EXISTS w0_1_rbac_role_seed (
    name VARCHAR(100) NOT NULL PRIMARY KEY
);

-- 安全阀：存在 role='admin' 账号但无任何 platform_admin 候选时，
-- INSERT 0 触发 CHECK 失败 → 整个事务中止（绝不把平台管理入口清零）。
CREATE TABLE IF NOT EXISTS w0_1_rbac_guard (
    ok INTEGER NOT NULL,
    CONSTRAINT ck_w0_1_rbac_guard CHECK (ok = 1)
);

-- 以上 DDL 在 MySQL 中会隐式提交；以下 DML 统一放进显式事务，
-- 任一语句失败时整段数据变更回滚（备份/分类/回填/降级要么全做，要么全不做）。
-- 用 BEGIN（MySQL/SQLite 共同支持的语法；START TRANSACTION 不被 SQLite 接受）。
BEGIN;

-- 安全阀校验（先于任何账号数据处理）：
--   - 没有 role='admin' 账号 → 无需迁移，放行（重放场景）；
--   - 已存在任一显式平台管理员（is_superuser=1）→ 放行（重放/增量场景）；
--   - 或存在 allowlist 命中的 role='admin' 候选 → 放行；
--   - 否则（有 admin 账号但无人可成为平台管理员）→ CHECK 失败，整段中止。
DELETE FROM w0_1_rbac_guard;
INSERT INTO w0_1_rbac_guard (ok)
SELECT CASE
    WHEN NOT EXISTS (SELECT 1 FROM users WHERE LOWER(role) = 'admin') THEN 1
    WHEN EXISTS (SELECT 1 FROM users WHERE is_superuser = 1) THEN 1
    WHEN EXISTS (
        SELECT 1
        FROM users u, w0_1_rbac_platform_allowlist a
        WHERE LOWER(u.role) = 'admin'
          AND LOWER(a.username) = LOWER(u.username)
    ) THEN 1
    ELSE 0
END;

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
DELETE FROM w0_1_rbac_role_seed;
INSERT INTO w0_1_rbac_role_seed (name) VALUES ('OrgAdmin');
-- 已存在则不再插入（目标 seed 表，子查询读 roles，非目标表）。
DELETE FROM w0_1_rbac_role_seed
WHERE name IN (SELECT name FROM roles);

INSERT INTO roles (name, description, is_system_role, created_at, updated_at)
SELECT s.name, '组织管理员（组织级，仅限本组织）', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM w0_1_rbac_role_seed s;

-- 权限关联：先删该角色的目标权限再全量插入（幂等；子查询不读目标表）。
DELETE FROM role_permission_association
WHERE role_id IN (SELECT id FROM roles WHERE name = 'OrgAdmin')
  AND permission_id IN (
      SELECT id FROM permissions WHERE name IN (
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
  );

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
  );

-- ---------------------------------------------------------------------------
-- 2. 备份 + 分类（首次快照；重放不覆盖）
-- ---------------------------------------------------------------------------

-- 2a. 清理历史遗留：系统级角色（SysAdmin/User）不得出现在组织角色关联表。
--     先输出审计清单，再删除；回滚脚本不恢复这些非法授权（fail closed）。
SELECT x.user_id, x.organization_id, r.name AS system_role
FROM user_organization_role_association x, roles r
WHERE x.role_id = r.id AND r.is_system_role = 1
ORDER BY x.user_id, x.organization_id;

DELETE FROM user_organization_role_association
WHERE role_id IN (SELECT id FROM roles WHERE is_system_role = 1);

-- 2b. 候选与备份（目标 candidates，子查询读 backup，非目标表）。
DELETE FROM w0_1_rbac_admin_candidates;
INSERT INTO w0_1_rbac_admin_candidates (user_id)
SELECT id FROM users WHERE LOWER(role) = 'admin';
-- 已备份账号不再覆盖首次快照（目标 candidates，子查询读 backup，非目标表）。
DELETE FROM w0_1_rbac_admin_candidates
WHERE user_id IN (SELECT user_id FROM w0_1_rbac_admin_backup);

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
            -- 大小写不敏感匹配：MySQL utf8mb4_unicode_ci 默认不区分，
            -- SQLite 区分——显式 LOWER 让两种方言行为一致。
            WHERE LOWER(a.username) = LOWER(u.username)
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
FROM users u, w0_1_rbac_admin_candidates c
WHERE u.id = c.user_id;

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
--    先删本次要写回的关联（含迁移前已存在的同关联），再统一插入；
--    回滚脚本依据 association_existed_before 决定是否删除。
-- ---------------------------------------------------------------------------
DELETE FROM user_organization_role_association
WHERE EXISTS (
    SELECT 1
    FROM w0_1_rbac_admin_backup b, roles r
    WHERE b.user_id = user_organization_role_association.user_id
      AND b.organization_id = user_organization_role_association.organization_id
      AND b.classification = 'org_admin'
      AND r.id = user_organization_role_association.role_id
      AND r.name = 'OrgAdmin'
);

INSERT INTO user_organization_role_association (user_id, organization_id, role_id)
SELECT b.user_id, b.organization_id, r.id
FROM w0_1_rbac_admin_backup b, roles r
WHERE b.classification = 'org_admin'
  AND r.name = 'OrgAdmin';

-- ---------------------------------------------------------------------------
-- 5. 降级 users.role（权限已由 is_superuser / OrgAdmin 关联承载）
-- ---------------------------------------------------------------------------
UPDATE users
SET role = 'user'
WHERE id IN (SELECT user_id FROM w0_1_rbac_admin_backup);

COMMIT;

-- ---------------------------------------------------------------------------
-- 6. 迁移后审计输出
-- ---------------------------------------------------------------------------
SELECT user_id, username, old_role, old_is_superuser, organization_id,
       classification, association_existed_before, backed_up_at
FROM w0_1_rbac_admin_backup
ORDER BY user_id;
