"""S4 regression tests — execute_sql 租户防护加固。

(a) 列校验必须要求"每一个"标识符都在白名单内，且函数名必须来自函数白名单，
    否则 `SELECT id, CONCAT(hashed_password, id) FROM users` 这类表达式
    只要夹带一个白名单列（id）即可读出 hashed_password。
(c) 只涉及没有 organization_id 列的表（document_chunks 等）的查询直接拒绝
    （fail closed），不再尝试自动 JOIN；含 organization_id 的表参与 JOIN 时
    仍正常注入组织过滤。

S4(b)（自带 organization_id 条件不能禁用注入）的回归测试在
tests/unit/test_security_fixes.py 中同步更新。
"""

from app.agent.tools.code_execution import (
    _inject_org_filter,
    _is_safe_select_sql,
)


class TestSelectColumnWhitelist:
    """S4(a) — 每个标识符都要命中白名单，函数必须有白名单。"""

    def test_concat_with_one_whitelisted_column_rejected(self):
        # 已验证的绕过：旧实现只要 any(tok in whitelist) 成立就放行，
        # id 在白名单里，hashed_password 因此被 CONCAT 读出。
        ok, reason = _is_safe_select_sql("SELECT id, CONCAT(hashed_password, id) FROM users")
        assert not ok
        assert "not in the allowed whitelist" in reason

    def test_function_outside_allowlist_rejected(self):
        ok, reason = _is_safe_select_sql("SELECT id, group_concat(username) FROM users")
        assert not ok
        assert "Function" in reason

    def test_all_identifiers_must_be_whitelisted(self):
        ok, _ = _is_safe_select_sql("SELECT username, hashed_password FROM users")
        assert not ok

    def test_legit_aggregates_and_aliases_still_allowed(self):
        ok, reason = _is_safe_select_sql("SELECT COUNT(*) AS total, MAX(created_at) FROM documents")
        assert ok, reason

    def test_legit_string_and_cast_expressions_still_allowed(self):
        ok, reason = _is_safe_select_sql(
            "SELECT UPPER(filename) AS f, CAST(chunk_count AS CHAR) FROM documents"
        )
        assert ok, reason


class TestUnfilteredTablesFailClosed:
    """S4(c) — 无法附加组织过滤的查询一律拒绝；JOIN 过滤表仍然可用。"""

    def test_only_unfiltered_table_denied(self):
        ok, reason = _is_safe_select_sql("SELECT chunk_index FROM document_chunks")
        assert not ok
        assert "cannot be org-scoped" in reason

    def test_chat_messages_only_denied(self):
        ok, reason = _is_safe_select_sql("SELECT content FROM chat_messages")
        assert not ok

    def test_join_with_filterable_table_still_works(self):
        q = (
            "SELECT chunk_index, document_id FROM document_chunks "
            "JOIN documents ON document_chunks.document_id = documents.id"
        )
        ok, reason = _is_safe_select_sql(q)
        assert ok, reason
        scoped = _inject_org_filter(q, 7)
        assert "organization_id = 7" in scoped
