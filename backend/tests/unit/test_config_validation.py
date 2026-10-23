"""配置结构校验：ProfileSpec / PatchOp / patch 语义（issue #479 §8）。"""

from __future__ import annotations

import pytest

from app.core.config.schema import (
    ConfigError,
    PatchOp,
    ProfileSpec,
    apply_patches,
    validate_profile_name,
)


def _op(op: str, path: str, value=None) -> PatchOp:
    return PatchOp(op=op, path=path, value=value)


class TestProfileSpec:
    def test_unknown_settings_key_reports_dotted_path(self):
        with pytest.raises(ConfigError) as excinfo:
            ProfileSpec(entrypoint="web", bundles=[], settings={"NOPE": 1})
        assert excinfo.value.path == "settings.NOPE"

    def test_unknown_bundle_rejected(self):
        with pytest.raises(ConfigError) as excinfo:
            ProfileSpec(entrypoint="web", bundles=["nope"], settings={})
        assert excinfo.value.path == "bundles.nope"

    def test_duplicate_bundles_rejected(self):
        with pytest.raises(ConfigError):
            ProfileSpec(entrypoint="web", bundles=["core", "core"], settings={})

    def test_plaintext_secret_rejected(self):
        with pytest.raises(ConfigError) as excinfo:
            ProfileSpec(
                entrypoint="web",
                bundles=["core"],
                settings={"MINIO_SECRET_KEY": "plaintext"},
            )
        assert excinfo.value.path == "settings.MINIO_SECRET_KEY"

    def test_empty_secret_allowed(self):
        spec = ProfileSpec(entrypoint="web", bundles=["core"], settings={"MINIO_SECRET_KEY": ""})
        assert spec.settings["MINIO_SECRET_KEY"] == ""


class TestPatchPathValidation:
    @pytest.mark.parametrize(
        "path",
        [
            "HOOKS_ENABLED",  # 没有 settings. 前缀
            "settings.NOT_EXIST..X",  # 含 ..
            "settings./etc/passwd",  # 段以 / 开头（绝对路径）
            "/settings.X",  # 整体以 / 开头
            "settings.__class__",  # 含 __
            "settings.",  # 空段
        ],
    )
    def test_illegal_paths_rejected(self, path):
        with pytest.raises(ConfigError):
            PatchOp(op="replace", path=path, value=1)

    def test_path_must_be_under_settings(self):
        with pytest.raises(ConfigError) as excinfo:
            PatchOp(op="replace", path="PORT", value=1)
        assert "settings." in excinfo.value.message


class TestApplyPatches:
    BASE = {"PORT": 8000, "DEBUG": False, "NESTED": {"A": 1}}

    def test_replace_requires_existing_path(self):
        with pytest.raises(ConfigError) as excinfo:
            apply_patches(self.BASE, [_op("replace", "settings.NOT_EXIST", 1)])
        assert excinfo.value.path == "settings.NOT_EXIST"

    def test_insert_requires_absent_path(self):
        with pytest.raises(ConfigError) as excinfo:
            apply_patches(self.BASE, [_op("insert", "settings.PORT", 9000)])
        assert excinfo.value.path == "settings.PORT"

    def test_delete_requires_existing_path(self):
        with pytest.raises(ConfigError) as excinfo:
            apply_patches(self.BASE, [_op("delete", "settings.NOT_EXIST")])
        assert excinfo.value.path == "settings.NOT_EXIST"

    def test_replace_insert_delete_semantics(self):
        result = apply_patches(
            self.BASE,
            [
                _op("replace", "settings.PORT", 9000),
                _op("insert", "settings.HOST_ALIAS", "127.0.0.1"),
                _op("delete", "settings.DEBUG"),
                _op("replace", "settings.NESTED.A", 2),
            ],
        )
        assert result == {"PORT": 9000, "HOST_ALIAS": "127.0.0.1", "NESTED": {"A": 2}}
        # 输入不被原地修改
        assert self.BASE == {"PORT": 8000, "DEBUG": False, "NESTED": {"A": 1}}

    def test_insert_of_secret_field_rejected(self):
        with pytest.raises(ConfigError) as excinfo:
            apply_patches(
                {"DEEPSEEK_API_KEY": "${DEEPSEEK_API_KEY}"},
                [_op("replace", "settings.DEEPSEEK_API_KEY", "sk-plain")],
            )
        assert excinfo.value.path == "settings.DEEPSEEK_API_KEY"

    def test_nested_parent_must_exist(self):
        with pytest.raises(ConfigError):
            apply_patches(self.BASE, [_op("replace", "settings.MISSING.CHILD", 1)])


class TestProfileName:
    def test_base_rejected(self):
        with pytest.raises(ConfigError) as excinfo:
            validate_profile_name("base")
        assert excinfo.value.path == "profile.base"

    def test_unknown_rejected(self):
        with pytest.raises(ConfigError) as excinfo:
            validate_profile_name("nope")
        assert excinfo.value.path == "profile.nope"

    @pytest.mark.parametrize("name", ["web", "worker", "headless", "sdk", "acp"])
    def test_valid_profiles(self, name):
        assert validate_profile_name(name) == name
