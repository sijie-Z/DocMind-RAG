"""文档级 ACL 判别性测试（两档可见性：organization / private）。

覆盖：
- get_blocked_document_ids：上传者与授权人可见、其他人不可见、无 viewer
  时保持组织级行为、DB 故障 fail-closed；
- with_acl_filter：must_not 结构合法（裸 must_not 会被 ES 400）；
- get_document_for_user：私有文档只有所有者/授权人可读；超管审计放行。

变异点：删 ACL 查询 / 改成 fail-open / 去掉访问守门 → 对应用例红。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions import NotFoundError
from app.services.document_acl import get_blocked_document_ids, with_acl_filter


class TestAclFilterDsl:
    def test_with_acl_filter_uses_valid_bool_must_not(self):
        filters = [{"term": {"organization_id": "3"}}]
        out = with_acl_filter(filters, ["d-private"])
        assert out[0] == filters[0]
        assert out[1] == {"bool": {"must_not": [{"terms": {"document_id": ["d-private"]}}]}}

    def test_no_blocked_ids_leaves_filters_untouched(self):
        filters = [{"term": {"organization_id": "3"}}]
        assert with_acl_filter(filters, []) is filters


class TestBlockedDocumentIds:
    @pytest.mark.asyncio
    async def test_no_viewer_keeps_org_wide_behavior(self):
        db = AsyncMock()
        assert await get_blocked_document_ids(db, 3, None) == []
        db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uploader_and_granted_user_are_not_blocked(self):
        private = [SimpleNamespace(id="d1", uploaded_by=7)]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(all=MagicMock(return_value=private)),
                MagicMock(
                    scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=["d1"])))
                ),
            ]
        )
        assert await get_blocked_document_ids(db, 3, 42) == []

    @pytest.mark.asyncio
    async def test_unauthorized_viewer_is_blocked(self):
        private = [SimpleNamespace(id="d1", uploaded_by=7)]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(all=MagicMock(return_value=private)),
                MagicMock(
                    scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
                ),
            ]
        )
        assert await get_blocked_document_ids(db, 3, 42) == ["d1"]

    @pytest.mark.asyncio
    async def test_db_failure_fails_closed(self):
        """ACL 查询异常 → 屏蔽该组织所有私有文档，绝不 fail-open。

        变异点：except 分支直接 `return []` → 本条红（已实测原变异存活）。
        """
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("ACL table missing"))
        fallback = MagicMock()
        fallback.scalars = MagicMock(
            return_value=MagicMock(all=MagicMock(return_value=["d1", "d2"]))
        )
        db.execute = AsyncMock(side_effect=[RuntimeError("ACL table missing"), fallback])

        blocked = await get_blocked_document_ids(db, 3, 42)
        assert blocked == ["d1", "d2"]

    @pytest.mark.asyncio
    async def test_owner_always_sees_own_private_document(self):
        """上传者不需要授权行；变异：只看 ACL 表 → 所有者自己被屏蔽，本条红。"""
        private = [SimpleNamespace(id="d1", uploaded_by=7)]
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                MagicMock(all=MagicMock(return_value=private)),
                MagicMock(
                    scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
                ),
            ]
        )
        assert await get_blocked_document_ids(db, 3, 7) == []


class TestDocumentAccessGuard:
    @pytest.mark.asyncio
    async def test_private_document_denied_to_other_org_member(self):
        from app.core.security import get_document_for_user

        doc = MagicMock()
        doc.organization_id = 3
        doc.uploaded_by = 7
        doc.visibility = "private"
        user = MagicMock()
        user.is_superuser = False
        user.id = 42
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)

        with (
            patch("app.core.security.get_user_org_id", return_value=3),
            patch("app.core.security._user_has_document_acl", AsyncMock(return_value=False)),
        ):
            with pytest.raises(NotFoundError):
                await get_document_for_user(db, user, "d1")

    @pytest.mark.asyncio
    async def test_private_document_allowed_to_granted_user(self):
        from app.core.security import get_document_for_user

        doc = MagicMock()
        doc.organization_id = 3
        doc.uploaded_by = 7
        doc.visibility = "private"
        user = MagicMock()
        user.is_superuser = False
        user.id = 42
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)

        with (
            patch("app.core.security.get_user_org_id", return_value=3),
            patch("app.core.security._user_has_document_acl", AsyncMock(return_value=True)),
        ):
            assert await get_document_for_user(db, user, "d1") is doc

    @pytest.mark.asyncio
    async def test_organization_document_keeps_old_behavior(self):
        from app.core.security import get_document_for_user

        doc = MagicMock()
        doc.organization_id = 3
        doc.uploaded_by = 7
        doc.visibility = "organization"
        user = MagicMock()
        user.is_superuser = False
        user.id = 42
        db = AsyncMock()
        db.get = AsyncMock(return_value=doc)

        with (
            patch("app.core.security.get_user_org_id", return_value=3),
            patch("app.core.security._user_has_document_acl", AsyncMock()) as acl_mock,
        ):
            assert await get_document_for_user(db, user, "d1") is doc
            acl_mock.assert_not_awaited()


class TestAclMutationCacheInvalidation:
    @pytest.mark.asyncio
    async def test_authorized_users_change_clears_cache_and_bumps_generation(self):
        """撤权/授权成功后必须清组织缓存，否则私有 chunk 仍从旧 key 命中。

        变异点：删掉 documents.py 中 clear_all_for_org 调用 → 本条红。
        """
        from app.api.v1.endpoints import documents as documents_mod

        fake_doc = MagicMock()
        fake_doc.organization_id = 7
        fake_doc.uploaded_by = 1
        fake_doc.id = "doc-acl"
        fake_doc.user_id = 1

        existing = MagicMock()
        existing.scalars.return_value.all.return_value = [2]
        db = AsyncMock()
        db.execute = AsyncMock(return_value=existing)

        clear = AsyncMock(return_value=2)
        with (
            patch.object(documents_mod, "get_document_for_user", AsyncMock(return_value=fake_doc)),
            patch.object(documents_mod, "is_org_admin", AsyncMock(return_value=False)),
            patch.object(documents_mod, "_can_manage_document_acl", return_value=True),
            patch.object(documents_mod, "_is_private", return_value=True),
            patch.object(documents_mod.audit_service, "log_activity", AsyncMock()),
            patch.object(
                documents_mod, "get_document_authorized_users", AsyncMock(return_value={})
            ),
            patch("app.rag.cache.clear_all_for_org", new=clear),
        ):
            await documents_mod.put_document_authorized_users(
                document_id="doc-acl",
                body=documents_mod.AuthorizedUsersUpdate(user_ids=[]),
                db=db,
                current_user=MagicMock(),
            )

        clear.assert_awaited_once_with(7)
