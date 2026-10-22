"""配置合并优先级与加载器行为（issue #479 §8 第一条）。"""

from __future__ import annotations

import pytest

from app.core.config import loader
from app.core.config.schema import ConfigError, deep_merge

FIELD = "RATE_LIMIT_WINDOW_SECONDS"  # int 字段，默认 60，避免与其他测试互相影响


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def config_tree(tmp_path, monkeypatch):
    """临时 config 目录 + 隔离的进程环境；不依赖仓库内任何 `.env` 内容。"""
    config_dir = tmp_path / "config"
    for bundle in ("core", "rag", "agent", "observability"):
        _write(config_dir / "bundles" / f"{bundle}.yaml", "settings: {}\n")
    _write(
        config_dir / "profiles" / "web.yaml",
        "entrypoint: web\nbundles: [core, rag, agent, observability]\nsettings: {}\n",
    )
    _write(config_dir / "base.yaml", "settings: {}\n")

    monkeypatch.setattr(loader, "CONFIG_DIR", config_dir)
    # 环境层用 monkeypatch 显式构造；ENABLE_LOCAL_EMBEDDING=true 让硬件档位不介入，
    # 保证用例只受被测优先级影响。
    monkeypatch.setenv("SECRET_KEY", "a" * 32)
    monkeypatch.setenv("JWT_SECRET_KEY", "b" * 32)
    monkeypatch.setenv("DATABASE_URL", "mysql+asyncmy://u:p@localhost:3306/whatever")
    monkeypatch.setenv("ENABLE_LOCAL_EMBEDDING", "true")
    monkeypatch.delenv(FIELD, raising=False)
    # `.env` 相对 CWD 读取：切到临时目录，隔离仓库里的 backend/.env。
    monkeypatch.chdir(tmp_path)
    loader.reset_settings()
    yield config_dir
    loader.reset_settings()


def _set_profile_settings(config_dir, settings_yaml: str) -> None:
    _write(
        config_dir / "profiles" / "web.yaml",
        f"entrypoint: web\nbundles: [core, rag, agent, observability]\nsettings:\n{settings_yaml}\n",
    )


def test_priority_matrix_base_bundle_profile_patch_env(config_tree, monkeypatch):
    """base=1 < bundle=2 < profile=3 < patch=4 < env=5 ⇒ 最终为 5。"""
    config_dir = config_tree
    _write(config_dir / "base.yaml", f"settings: {{{FIELD}: 1}}\n")
    _write(config_dir / "bundles" / "core.yaml", f"settings: {{{FIELD}: 2}}\n")
    _set_profile_settings(config_dir, f"  {FIELD}: 3")
    patch_file = config_dir / "patch.yaml"
    _write(
        patch_file,
        f"patch:\n  - op: replace\n    path: settings.{FIELD}\n    value: 4\n",
    )

    # 环境层（monkeypatch.setenv）压过 patch。
    monkeypatch.setenv(FIELD, "5")
    assert loader.load_settings("web", [str(patch_file)]).RATE_LIMIT_WINDOW_SECONDS == 5

    # 去掉 env → patch 生效。
    loader.reset_settings()
    monkeypatch.delenv(FIELD)
    assert loader.load_settings("web", [str(patch_file)]).RATE_LIMIT_WINDOW_SECONDS == 4

    # 去掉 patch → profile 生效。
    loader.reset_settings()
    assert loader.load_settings("web").RATE_LIMIT_WINDOW_SECONDS == 3

    # 去掉 profile 覆盖 → bundle 生效。
    loader.reset_settings()
    _set_profile_settings(config_dir, "  {}")
    assert loader.load_settings("web").RATE_LIMIT_WINDOW_SECONDS == 2

    # 去掉 bundle 覆盖 → base 生效。
    loader.reset_settings()
    _write(config_dir / "bundles" / "core.yaml", "settings: {}\n")
    assert loader.load_settings("web").RATE_LIMIT_WINDOW_SECONDS == 1


def test_dotenv_is_environment_layer_and_env_beats_it(config_tree, monkeypatch, tmp_path):
    """`.env` 压过 YAML，真实环境变量压过 `.env`（§0.3 裁定）。"""
    config_dir = config_tree
    _set_profile_settings(config_dir, f"  {FIELD}: 3")
    (tmp_path / ".env").write_text(f"{FIELD}=7\n", encoding="utf-8")

    assert loader.load_settings("web").RATE_LIMIT_WINDOW_SECONDS == 7

    loader.reset_settings()
    monkeypatch.setenv(FIELD, "9")
    assert loader.load_settings("web").RATE_LIMIT_WINDOW_SECONDS == 9


def test_deep_merge_nested_dicts_and_list_replace():
    base = {"a": {"b": 1, "c": 2}, "items": [1, 2], "scalar": "x"}
    override = {"a": {"c": 3, "d": 4}, "items": [9], "scalar": "y"}
    merged = deep_merge(base, override)
    assert merged == {"a": {"b": 1, "c": 3, "d": 4}, "items": [9], "scalar": "y"}
    # 入参不得被原地修改。
    assert base == {"a": {"b": 1, "c": 2}, "items": [1, 2], "scalar": "x"}


def test_same_profile_without_explicit_sources_matches_deep_merge(config_tree):
    """只有 base 与 profile 时，结果等于两者深合并（§8 第二条，list 替换）。"""
    config_dir = config_tree
    _write(
        config_dir / "base.yaml",
        "settings:\n  RATE_LIMIT_EXCLUDE_PATHS: ['/only-base']\n",
    )
    _set_profile_settings(
        config_dir,
        "  RATE_LIMIT_EXCLUDE_PATHS: ['/only-profile']",
    )
    settings_obj = loader.load_settings("web")
    assert settings_obj.RATE_LIMIT_EXCLUDE_PATHS == ["/only-profile"]


def test_missing_base_yaml_is_config_error(config_tree):
    (config_tree / "base.yaml").unlink()
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("web")
    assert "base.yaml" in excinfo.value.message


def test_missing_profile_file_is_config_error(config_tree):
    (config_tree / "profiles" / "web.yaml").unlink()
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("web")
    assert "web.yaml" in excinfo.value.message


def test_missing_bundle_file_is_config_error(config_tree):
    (config_tree / "bundles" / "rag.yaml").unlink()
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("web")
    assert "rag.yaml" in excinfo.value.message


def test_profile_base_is_rejected(config_tree):
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("base")
    assert excinfo.value.path == "profile.base"


def test_unknown_profile_is_rejected(config_tree):
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("nope")
    assert excinfo.value.path == "profile.nope"


def test_cached_load_rejects_different_patches(config_tree):
    """已缓存且同 profile：显式传入的 patch 列表与已应用不一致 ⇒ ConfigError。

    patches=None 表示调用方未指定（如 build_app 内部），不触发该检查。
    """
    config_dir = config_tree
    _set_profile_settings(config_dir, "  PORT: 8000")
    patch_a = config_dir / "patch-a.yaml"
    patch_b = config_dir / "patch-b.yaml"
    _write(
        patch_a,
        "patch:\n  - op: replace\n    path: settings.PORT\n    value: 9000\n",
    )
    _write(
        patch_b,
        "patch:\n  - op: replace\n    path: settings.PORT\n    value: 9001\n",
    )

    loader.load_settings("web", [str(patch_a)])
    assert loader.load_settings("web", [str(patch_a)]) is loader.get_settings()

    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("web", [str(patch_b)])
    assert excinfo.value.path == "patch"

    # 未指定 patches 的内部调用不受影响
    assert loader.load_settings("web") is loader.get_settings()


def test_unknown_settings_key_reports_dotted_path(config_tree):
    _set_profile_settings(config_tree, "  NOPE: 1")
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("web")
    assert excinfo.value.path == "settings.NOPE"


@pytest.mark.parametrize("secret_value", ["sk-plaintext", "password123"])
def test_plaintext_secrets_rejected_in_bundles(config_tree, secret_value):
    _write(
        config_tree / "bundles" / "agent.yaml",
        f"settings:\n  DEEPSEEK_API_KEY: {secret_value}\n",
    )
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("web")
    assert excinfo.value.path == "settings.DEEPSEEK_API_KEY"


def test_secret_placeholder_is_allowed(config_tree):
    # 选一个测试进程环境里未设置的 secret 字段，避免环境层覆盖掉占位符本身。
    _write(
        config_tree / "bundles" / "agent.yaml",
        "settings:\n  LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY}\n",
    )
    settings_obj = loader.load_settings("web")
    assert settings_obj.LANGFUSE_SECRET_KEY == "${LANGFUSE_SECRET_KEY}"


def test_patch_file_plaintext_secret_rejected(config_tree):
    patch_file = config_tree / "patch.yaml"
    _write(
        patch_file,
        "patch:\n  - op: replace\n    path: settings.DEEPSEEK_API_KEY\n    value: sk-live\n",
    )
    with pytest.raises(ConfigError) as excinfo:
        loader.load_settings("web", [str(patch_file)])
    assert excinfo.value.path == "settings.DEEPSEEK_API_KEY"
