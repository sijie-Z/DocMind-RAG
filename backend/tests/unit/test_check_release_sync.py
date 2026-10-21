"""检查器 scripts/check_release_sync.py 的鉴别力测试（第二轮 §7.2⑦）。

证明这条门禁「有牙」：
- 只加 CHANGELOG 发布标题、不同步 APP_VERSION → 必红（复现 1.22.0 事故）；
- 两者同 PR bump 到同一版本 → 绿；
- 只 bump APP_VERSION 且与既有标题一致（branding-sweep 场景）→ 绿；
- 普通 PR 只在 [Unreleased] 加条目 → 不误红；
- rc 写法归一（rc.1/rc1/RC1、日期改动）→ 不误红；
- base 引用坏 → exit 2（工具失败，与「不一致」的 1 区分）。

用 importlib 按路径加载脚本（CI hygiene job 以 --noconftest 跑本文件同样
成立）；端到端用例在 tmp_path 建真实临时 git 仓库，子进程跑脚本本体。
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "check_release_sync.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_release_sync", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 必须登记进 sys.modules：dataclasses 解析字符串注解时会按模块名回查
    # （与 test_check_except_control_flow.py 同款防回归）。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


# ---- 纯函数：版本等价（R1） --------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1.22.1-rc.1", "1.22.1-rc.1"),
        ("1.22.1-rc.1", "1.22.1-rc1"),
        ("1.22.1-RC1", "1.22.1-rc.1"),
        ("1.22.1_rc_1", "1.22.1-rc.1"),
        ("v1.22.1", "1.22.1"),
        ("1.22.1", "1.22.1"),
        ("2", "2"),
    ],
)
def test_versions_equivalent_true(left, right):
    assert checker.versions_equivalent(left, right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1.22.1-rc.1", "1.22.1"),
        ("1.22.1-rc.1", "1.22.1-rc.2"),
        ("1.21.0", "1.22.1-rc.1"),
        ("1.22.1", "1.22.10"),
        ("1.22.1", "1.23.0"),
    ],
)
def test_versions_equivalent_false(left, right):
    assert not checker.versions_equivalent(left, right)


# ---- 纯函数：标题提取（R2） --------------------------------------------------


def test_top_versioned_heading_skips_unreleased_and_takes_first():
    text = (
        "# Changelog\n\n## [Unreleased]\n\n## [1.23.0] - 2026-10-20\n\n"
        "## [1.22.1-rc.1] - 2026-10-05\n"
    )
    assert checker.top_versioned_heading(text) == "1.23.0"


def test_top_versioned_heading_ignores_date_changes():
    before = "## [Unreleased]\n\n## [1.22.1-rc.1] - 2026-10-05\n"
    after = "## [Unreleased]\n\n## [1.22.1-rc.1] - 2026-10-18\n"
    assert checker.top_versioned_heading(before) == checker.top_versioned_heading(after)


def test_parse_app_version():
    source = 'class S:\n    APP_VERSION: str = "1.22.1-rc.1"\n'
    assert checker.parse_app_version(source) == "1.22.1-rc.1"
    assert checker.parse_app_version("nothing here") is None


# ---- 纯函数：判据（R3 / R4） -------------------------------------------------


def test_evaluate_frozen_baseline_mismatch_is_not_red():
    # 冻结基线自身不一致（1.21.0 vs 1.22.1-rc.1）：本 PR 未动发布版本 → 不红。
    verdict = checker.evaluate("1.21.0", "1.22.1-rc.1", "1.21.0", "1.22.1-rc.1")
    assert verdict.ok
    assert not verdict.triggered


def test_evaluate_app_bump_to_existing_heading_passes():
    verdict = checker.evaluate("1.21.0", "1.22.1-rc.1", "1.22.1-rc.1", "1.22.1-rc.1")
    assert verdict.ok
    assert verdict.triggered


def test_evaluate_release_heading_only_is_red():
    verdict = checker.evaluate("1.21.0", "1.22.1-rc.1", "1.21.0", "1.23.0")
    assert not verdict.ok


def test_evaluate_mismatched_bump_is_red():
    verdict = checker.evaluate("1.21.0", "1.22.1-rc.1", "1.22.0", "1.23.0")
    assert not verdict.ok


# ---- 端到端：临时 git 仓库 ---------------------------------------------------

_CHANGELOG_BASE = "# Changelog\n\n## [Unreleased]\n\n## [1.22.1-rc.1] - 2026-10-05\n\n- 基线\n"
_CHANGELOG_UNRELEASED_ONLY = (
    "# Changelog\n\n## [Unreleased]\n\n- 新条目\n\n## [1.22.1-rc.1] - 2026-10-05\n\n- 基线\n"
)
_CHANGELOG_NEW_RELEASE = (
    "# Changelog\n\n## [1.23.0] - 2026-10-20\n\n- 发布 1.23.0\n\n"
    "## [1.22.1-rc.1] - 2026-10-05\n\n- 基线\n"
)


def _git(repo: Path, *args: str):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "ci@test.local")
    _git(repo, "config", "user.name", "ci")
    return repo


def _commit_state(repo: Path, changelog: str, app_version: str) -> str:
    (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    config = repo / "backend" / "app" / "core" / "config" / "base.py"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f'APP_VERSION: str = "{app_version}"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "state")
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_checker(repo: Path, *extra: str):
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(repo), *extra],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


class TestEndToEnd:
    def test_unreleased_only_pr_passes(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        base = _commit_state(repo, _CHANGELOG_BASE, "1.21.0")
        _commit_state(repo, _CHANGELOG_UNRELEASED_ONLY, "1.21.0")
        result = _run_checker(repo, "--base", base)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_release_heading_without_app_bump_is_red(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        base = _commit_state(repo, _CHANGELOG_BASE, "1.21.0")
        _commit_state(repo, _CHANGELOG_NEW_RELEASE, "1.21.0")
        result = _run_checker(repo, "--base", base)
        assert result.returncode == 1
        assert "1.23.0" in result.stdout + result.stderr

    def test_same_pr_bump_passes(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        base = _commit_state(repo, _CHANGELOG_BASE, "1.21.0")
        _commit_state(repo, _CHANGELOG_NEW_RELEASE, "1.23.0")
        result = _run_checker(repo, "--base", base)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_app_bump_to_existing_rc_heading_passes(self, tmp_path: Path):
        # branding-sweep 场景：CHANGELOG 标题已是 1.22.1-rc.1，本 PR 只把
        # APP_VERSION 从 1.21.0 bump 到 1.22.1-rc.1 —— 应绿。
        repo = _make_repo(tmp_path)
        base = _commit_state(repo, _CHANGELOG_BASE, "1.21.0")
        _commit_state(repo, _CHANGELOG_BASE, "1.22.1-rc.1")
        result = _run_checker(repo, "--base", base)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_mismatched_bump_is_red(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        base = _commit_state(repo, _CHANGELOG_BASE, "1.21.0")
        _commit_state(repo, _CHANGELOG_NEW_RELEASE, "1.22.0")
        result = _run_checker(repo, "--base", base)
        assert result.returncode == 1

    def test_missing_base_skips(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit_state(repo, _CHANGELOG_BASE, "1.21.0")
        result = _run_checker(repo)
        assert result.returncode == 0
        assert "跳过" in result.stdout

    def test_unresolvable_base_is_tool_failure(self, tmp_path: Path):
        repo = _make_repo(tmp_path)
        _commit_state(repo, _CHANGELOG_BASE, "1.21.0")
        result = _run_checker(repo, "--base", "no-such-ref")
        assert result.returncode == 2
