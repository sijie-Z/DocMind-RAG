"""`--api-key-file` 身份接线（issue #480 §5.5 / 验收补充）。

覆盖：
- key 有效且 user_id/org_id 与 CLI 一致 → 通过；
- key 属于他人 / 组织不匹配 / 无效 → 拒绝；
- sdk 入口在任何不匹配时 stderr 说明 + exit 2，stdout 无协议帧；
- 匹配成功时先校验身份、再进入 stdio。
"""

import argparse

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.entrypoints.sdk as sdk_module
import app.models  # noqa: F401 — create_all 需要全部表
import app.rpc.server as rpc_server
from app.core.database import Base
from app.models.user import User
from app.services.api_key_service import api_key_service

USER_ID = 7
ORG_ID = 1


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'api_key_file.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def key_file(db_factory, tmp_path):
    async with db_factory() as session:
        session.add(
            User(
                id=USER_ID,
                username="sdkuser",
                email="sdkuser@example.com",
                hashed_password="x",
                is_active=True,
                organization_id=ORG_ID,
            )
        )
        await session.commit()
        user = await session.get(User, USER_ID)
        _, raw = await api_key_service.create_key(
            session, user=user, name="sdk-entry", org_id=ORG_ID
        )
    path = tmp_path / "key.txt"
    path.write_text(raw + "\n", encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_validate_accepts_matching_identity(db_factory, key_file):
    raw = key_file.read_text(encoding="utf-8").strip()
    error = await sdk_module._validate_api_key_identity(
        raw, org_id=ORG_ID, user_id=USER_ID, db_factory=db_factory
    )
    assert error is None


@pytest.mark.asyncio
async def test_validate_rejects_user_and_org_mismatch(db_factory, key_file):
    raw = key_file.read_text(encoding="utf-8").strip()

    user_error = await sdk_module._validate_api_key_identity(
        raw, org_id=ORG_ID, user_id=USER_ID + 1, db_factory=db_factory
    )
    assert user_error is not None and "user_id" in user_error

    org_error = await sdk_module._validate_api_key_identity(
        raw, org_id=ORG_ID + 1, user_id=USER_ID, db_factory=db_factory
    )
    assert org_error is not None and "org_id" in org_error


@pytest.mark.asyncio
async def test_validate_rejects_invalid_key(db_factory):
    error = await sdk_module._validate_api_key_identity(
        "dm_not-a-real-key", org_id=ORG_ID, user_id=USER_ID, db_factory=db_factory
    )
    assert error is not None and "无效" in error


def _args(key_file: str | None) -> argparse.Namespace:
    return argparse.Namespace(
        org_id=ORG_ID, user_id=USER_ID, api_key_file=key_file, is_superuser=False
    )


def test_entrypoint_exits_2_with_empty_stdout_on_mismatch(tmp_path, monkeypatch, capsys):
    path = tmp_path / "key.txt"
    path.write_text("dm_whatever\n", encoding="utf-8")

    async def _mismatch(*_args, **_kwargs):
        return "--api-key-file 的 API Key 属于其他用户（user_id 不匹配）"

    monkeypatch.setattr(sdk_module, "_validate_api_key_identity", _mismatch)
    with pytest.raises(SystemExit) as exc:
        sdk_module.run(_args(str(path)))
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == "", "失败路径 stdout 不得有协议帧"
    assert "user_id" in captured.err


def test_entrypoint_exits_2_when_key_file_missing(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        sdk_module.run(_args(str(tmp_path / "nope.txt")))
    assert exc.value.code == 2
    assert capsys.readouterr().out == ""


def test_entrypoint_validates_before_starting_stdio(tmp_path, monkeypatch):
    path = tmp_path / "key.txt"
    path.write_text("dm_whatever\n", encoding="utf-8")
    seen = {}

    async def _ok(raw_key, *, org_id, user_id, db_factory=None):
        seen.update(raw=raw_key, org_id=org_id, user_id=user_id)
        return

    async def _fake_stdio(**kwargs):
        seen["stdio"] = kwargs
        return 0

    monkeypatch.setattr(sdk_module, "_validate_api_key_identity", _ok)
    monkeypatch.setattr(rpc_server, "run_stdio", _fake_stdio)

    with pytest.raises(SystemExit) as exc:
        sdk_module.run(_args(str(path)))
    assert exc.value.code == 0
    assert seen["raw"] == "dm_whatever"
    assert seen["org_id"] == ORG_ID and seen["user_id"] == USER_ID
    assert seen["stdio"] == {
        "organization_id": ORG_ID,
        "user_id": USER_ID,
        "is_superuser": False,
    }
