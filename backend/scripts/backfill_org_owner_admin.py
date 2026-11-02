"""为存量组织的 owner 回填 per-org OrgAdmin（Batch 16 迁移脚本）。

背景：修复前注册/建组织的用户没有组织角色（User 系统角色不含
``document:update``），登记核验身份/建案/改文档全部 403。新代码只对
此后创建的组织自动授权；本脚本为**存量** ``organizations.owner_id``
幂等补授权。

- 只写 ``user_organization_role_association``（per-org OrgAdmin），
  不改用户、不改组织、不删除任何数据；
- 可重复执行：已有该关联的组织计入 ``already``；
- ``--dry-run`` 只报告将处理哪些组织，不写库。

用法::

    python scripts/backfill_org_owner_admin.py --dry-run
    python scripts/backfill_org_owner_admin.py
    python scripts/backfill_org_owner_admin.py --organization-id 3
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select  # noqa: E402

from app.core.database import AsyncSessionLocal  # noqa: E402
from app.models.organization import Organization  # noqa: E402
from app.models.rbac import (  # noqa: E402
    ORG_ADMIN_ROLE_NAME,
    Role,
    user_organization_role_association,
)
from app.services.permission_service import permission_service  # noqa: E402


async def _has_org_admin(session, user_id: int, organization_id: int) -> bool:
    role_id = (
        await session.execute(select(Role.id).where(Role.name == ORG_ADMIN_ROLE_NAME))
    ).scalar_one_or_none()
    if role_id is None:
        return False
    row = (
        await session.execute(
            select(user_organization_role_association.c.user_id).where(
                user_organization_role_association.c.user_id == user_id,
                user_organization_role_association.c.organization_id == organization_id,
                user_organization_role_association.c.role_id == role_id,
            )
        )
    ).first()
    return row is not None


async def backfill_org_owners(
    session,
    *,
    dry_run: bool = False,
    organization_id: int | None = None,
) -> dict:
    """为组织 owner 幂等补 OrgAdmin。返回 {granted, already, skipped} 分类 id 列表。"""
    stmt = select(Organization.id, Organization.owner_id)
    if organization_id is not None:
        stmt = stmt.where(Organization.id == organization_id)
    rows = (await session.execute(stmt.order_by(Organization.id))).all()

    granted: list[int] = []
    already: list[int] = []
    skipped: list[int] = []
    for org_id, owner_id in rows:
        if owner_id is None:
            skipped.append(org_id)
            continue
        if await _has_org_admin(session, owner_id, org_id):
            already.append(org_id)
            continue
        if not dry_run:
            await permission_service.set_org_admin(session, owner_id, org_id, True)
        granted.append(org_id)
    if not dry_run:
        await session.commit()
    return {"granted": granted, "already": already, "skipped": skipped}


async def _main(args) -> int:
    async with AsyncSessionLocal() as session:
        report = await backfill_org_owners(
            session,
            dry_run=args.dry_run,
            organization_id=args.organization_id,
        )
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"{mode}: granted={report['granted']} already={report['already']} "
        f"skipped(no owner)={report['skipped']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="为存量组织 owner 回填 OrgAdmin（幂等）")
    parser.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    parser.add_argument("--organization-id", type=int, default=None, help="只处理指定组织")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
