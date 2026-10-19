-- ============================================================================
-- W0-1 · RBAC 全局旁路收口：平台管理员登记表（可选的前置步骤）
--
-- 用途：在正式执行 w0_1_rbac_admin_scope_up.sql 之前，先建好 allowlist 并
-- 登记平台管理员用户名，避免「为了建表先跑主脚本」的鸡生蛋问题。
--
-- 用法（MySQL）：
--   mysql ... < w0_1_rbac_admin_scope_prep.sql
--   INSERT INTO w0_1_rbac_platform_allowlist (username) VALUES ('alice');
--   mysql ... < w0_1_rbac_admin_scope_up.sql
--
-- 也可跳过本文件：直接编辑主脚本中注释掉的示例 INSERT 后执行。
-- 主脚本自带安全阀：存在 role='admin' 账号却没有任何平台管理员候选时，
-- 会在事务中中止，不写入任何迁移数据。
-- ============================================================================

CREATE TABLE IF NOT EXISTS w0_1_rbac_platform_allowlist (
    username VARCHAR(50) NOT NULL PRIMARY KEY
);

-- 示例（按需执行）：
-- INSERT INTO w0_1_rbac_platform_allowlist (username) VALUES ('platform-admin');

SELECT username FROM w0_1_rbac_platform_allowlist ORDER BY username;
