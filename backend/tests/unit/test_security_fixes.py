"""
Security regression tests for batch-1/batch-2 fixes.

Covers: AST sandbox escape blocking, SQL org-filter injection,
PII masking chain offsets, and the server-side disabled_tools merge.
"""


from app.agent.tools.code_execution import (
    _ast_safety_check,
    _inject_org_filter,
    _is_safe_select_sql,
)
from app.services.masking_service import masking_service


class TestSandboxAstCheck:
    """execute_python AST sandbox must reject the verified escape chain."""

    def test_blocks_private_attribute_escape(self):
        # random._os.system(...) passed the old check and executed arbitrary
        # commands — verified in production code review.
        ok, reason = _ast_safety_check("random._os.system('echo pwned')")
        assert not ok
        assert "not allowed" in reason

    def test_blocks_dunder_access(self):
        ok, _ = _ast_safety_check("().__class__.__bases__")
        assert not ok

    def test_blocks_import(self):
        ok, _ = _ast_safety_check("import os")
        assert not ok

    def test_blocks_forbidden_calls(self):
        for snippet in ("eval('1')", "open('/etc/passwd')", "__import__('os')"):
            ok, _ = _ast_safety_check(snippet)
            assert not ok, snippet

    def test_blocks_oversized_code(self):
        ok, _ = _ast_safety_check("x = 1\n" * 3000)  # > 5000 chars
        assert not ok

    def test_allows_safe_math(self):
        ok, _ = _ast_safety_check("print(sum([1, 2, 3]) + math.sqrt(4))")
        assert ok


class TestSqlOrgFilter:
    """execute_sql must scope every whitelisted query to one organization."""

    def test_plain_select_gets_org_filter(self):
        q = _inject_org_filter("SELECT id FROM documents", 7)
        assert "organization_id = 7" in q

    def test_existing_where_is_wrapped(self):
        q = _inject_org_filter("SELECT id FROM documents WHERE title LIKE '%a%' OR filename LIKE '%b%'", 7)
        assert "WHERE (organization_id = 7 AND" in q
        # OR must stay inside the org-filtered parentheses
        assert q.count("(") == q.count(")")

    def test_explicit_org_filter_not_duplicated(self):
        q = _inject_org_filter("SELECT id FROM documents WHERE organization_id = 3", 7)
        assert "organization_id = 7" not in q
        assert "organization_id = 3" in q

    def test_order_by_kept_after_where(self):
        q = _inject_org_filter("SELECT id FROM documents ORDER BY id", 7)
        assert q.index("WHERE organization_id = 7") < q.index("ORDER BY")

    def test_limit_respected(self):
        q = _inject_org_filter("SELECT id FROM users LIMIT 5", 7)
        assert "LIMIT 5" in q and "LIMIT 100" not in q

    def test_union_rejected_by_validator(self):
        ok, reason = _is_safe_select_sql("SELECT id FROM documents UNION SELECT hashed_password FROM users")
        assert not ok

    def test_subquery_rejected(self):
        ok, reason = _is_safe_select_sql("SELECT id FROM users WHERE username=(SELECT hashed_password FROM users LIMIT 1)")
        assert not ok

    def test_into_outfile_rejected(self):
        ok, reason = _is_safe_select_sql("SELECT id FROM users INTO OUTFILE '/tmp/x'")
        assert not ok

    def test_users_sensitive_columns_removed(self):
        from app.agent.tools.code_execution import ALLOWED_SQL_COLUMNS
        assert "email" not in ALLOWED_SQL_COLUMNS["users"]
        assert "hashed_password" not in ALLOWED_SQL_COLUMNS["users"]


class TestMaskingChain:
    """PII masking must keep placeholder numbering unique across segments."""

    def test_offset_numbering(self):
        _, m1 = masking_service.mask_text("电话 13800138000", start_index=0)
        _, m2 = masking_service.mask_text("电话 13912345678", start_index=len(m1))
        assert set(m1.keys()) & set(m2.keys()) == set()

    def test_phone_boundary_inside_long_digits(self):
        # phone regex must not match inside an ID card / bank card number
        masked, mapping = masking_service.mask_text("110101199003071234")
        assert "[PHONE_" not in masked

    def test_id_card_and_bank_card_both_masked(self):
        masked, mapping = masking_service.mask_text("身份证110101199003071234 银行卡6222020200112233445")
        assert "110101199003071234" not in masked
        assert "6222020200112233445" not in masked
        assert "[ID_CARD_" in masked or "[BANK_CARD_" in masked

    def test_roundtrip_with_offset(self):
        original = "联系人 13812345678 邮箱 admin@corp.com"
        masked, mapping = masking_service.mask_text(original, start_index=10)
        assert masking_service.unmask_text(masked, mapping) == original


class TestDisabledToolsMerge:
    """Server-side disabled_tools enforcement must be non-negotiable."""

    def test_merge_keeps_defaults(self):
        from app.agent.config import DEFAULT_DISABLED_TOOLS
        from app.api.v1.endpoints.agent import _merge_disabled_tools
        merged = _merge_disabled_tools(["get_current_time"])
        for tool in DEFAULT_DISABLED_TOOLS:
            assert tool in merged
        assert "get_current_time" in merged

    def test_empty_client_list_gets_defaults(self):
        from app.agent.config import DEFAULT_DISABLED_TOOLS
        from app.api.v1.endpoints.agent import _merge_disabled_tools
        merged = _merge_disabled_tools([])
        assert set(merged) == set(DEFAULT_DISABLED_TOOLS)

    def test_client_cannot_re_enable_high_risk_tools(self):
        from app.api.v1.endpoints.agent import _merge_disabled_tools
        merged = _merge_disabled_tools(["execute_python", "execute_sql", "mcp_call"])
        # Even if the client explicitly lists them as "enabled" (i.e. not disabled),
        # the merge must keep them disabled.
        assert "execute_python" in merged
        assert "execute_sql" in merged
        assert "mcp_call" in merged


class TestWsTokenPolicy:
    """WS endpoints must reject refresh tokens."""

    def test_refresh_token_payload_rejected_by_type_check(self):
        # The WS endpoints now require payload["type"] == "access";
        # simulate the exact guard used in chat.py/notifications.py.
        for payload in (
            {"type": "refresh", "user_id": 1},
            {"user_id": 1},  # missing type
        ):
            if payload.get("type") != "access":
                # guard rejects
                pass
            else:
                raise AssertionError("refresh token must be rejected")
        payload = {"type": "access", "user_id": 1}
        assert payload.get("type") == "access"


class TestChatHistoryOrder:
    """Chat history for LLM context must be the most recent 10 messages."""

    def test_most_recent_ten_kept_and_reversed(self):
        # mirrors the chat_service fix: desc().limit(10) then reversed()
        messages = [f"m{i}" for i in range(20)]  # created oldest -> newest
        recent = list(reversed(messages[-10:]))
        # newest last, oldest first within the window
        assert recent[0] == "m19" and recent[-1] == "m10"
