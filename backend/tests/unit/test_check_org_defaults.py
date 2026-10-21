"""检查器 `scripts/check_org_defaults.py` 的鉴别力测试。

证明门禁「有牙」：
- 含 `organization_id: int = 1` 的合成树必须红（退出码 1）；
- `= 10` / `None` / 注释之外的正常签名不得误报；
- 真实 ``backend/app`` 树必须绿；
- 扫描根不存在时退出码 2（与既有检查器同约定）。

用 importlib 按路径加载脚本，CI hygiene job 以 ``--noconftest`` 运行时同样成立。
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "check_org_defaults.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_org_defaults", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def test_violation_sample_is_red(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        "async def tool(query: str, organization_id: int = 1, **_: object) -> str:\n"
        "    return query\n",
        encoding="utf-8",
    )
    assert checker.main(["--root", str(app_dir)]) == 1


def test_spacing_variant_is_red(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text("def f(organization_id:int=1):\n    pass\n", encoding="utf-8")
    assert checker.find_violations(app_dir), "必须容忍空白差异"


def test_optional_annotations_are_red(tmp_path):
    """`int | None = 1` 与 `Optional[int] = 1` 是同义 fail-open 写法。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        "def a(organization_id: int | None = 1):\n    pass\n"
        "def b(organization_id: Optional[int] = 1):\n    pass\n",
        encoding="utf-8",
    )
    hits = checker.find_violations(app_dir)
    assert len(hits) == 2


def test_default_kwarg_is_red(tmp_path):
    """`Field(default=1)` / `Query(default=1)` 形态。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        "def a(organization_id: int = Field(default=1)):\n    pass\n"
        "def b(organization_id: int = Query(default=1)):\n    pass\n",
        encoding="utf-8",
    )
    assert len(checker.find_violations(app_dir)) == 2


def test_or_one_fallback_is_red(tmp_path):
    """运行时 `organization_id or 1` 兜底。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        "def a(user):\n    return user.organization_id or 1\n", encoding="utf-8"
    )
    assert len(checker.find_violations(app_dir)) == 1


def test_zero_int_default_is_red(tmp_path):
    """`organization_id: int = 0` 与 `= -1` 是 0 号组织的同义 fail-open 写法。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        "def a(organization_id: int = 0):\n    pass\ndef b(organization_id: int = -1):\n    pass\n",
        encoding="utf-8",
    )
    assert len(checker.find_violations(app_dir)) == 2


def test_zero_string_default_is_red(tmp_path):
    """`organization_id: str = "0"` / `= '0'` 是同义 fail-open 写法。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        'def a(organization_id: str = "0"):\n    pass\n'
        "def b(organization_id: str = '0'):\n    pass\n",
        encoding="utf-8",
    )
    assert len(checker.find_violations(app_dir)) == 2


def test_or_zero_and_negative_fallback_are_red(tmp_path):
    """运行时 `organization_id or 0` / `or -1` 兜底。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        "def a(user):\n    return user.organization_id or 0\n"
        "def b(user):\n    return user.organization_id or -1\n",
        encoding="utf-8",
    )
    assert len(checker.find_violations(app_dir)) == 2


def test_getattr_default_one_is_red(tmp_path):
    """`getattr(obj, "organization_id", 1)` 形态。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        'def a(user):\n    return getattr(user, "organization_id", 1)\n',
        encoding="utf-8",
    )
    assert len(checker.find_violations(app_dir)) == 1


def test_zero_patterns_do_not_flag_lookalikes(tmp_path):
    """`= 10` / `or 10` / `= None` 不得误报。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "good.py").write_text(
        "def a(organization_id: int = 10):\n    pass\n"
        "def b(organization_id: int | None = None):\n    pass\n"
        "def c(user):\n    return user.organization_id or 10\n"
        'def d(user):\n    return getattr(user, "organization_id", None)\n',
        encoding="utf-8",
    )
    assert checker.find_violations(app_dir) == []


def test_fstring_literal_text_is_masked_but_expressions_still_match(tmp_path):
    """f-string 的文本段不是可执行代码，不得误报；表达式段照常扫描。

    Python 3.12 起 f-string 文本段是 FSTRING_MIDDLE token（不再是 STRING），
    必须显式遮蔽；3.11 及更早整段是 STRING，早已被遮蔽。
    """
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "mixed.py").write_text(
        "def a(organization_id):\n"
        '    return f"literal organization_id or 1 text"\n'
        "def b(organization_id):\n"
        '    return f"{organization_id or 0}"\n',
        encoding="utf-8",
    )
    hits = checker.find_violations(app_dir)
    if sys.version_info >= (3, 12):
        assert len(hits) == 1, hits
        assert hits[0][1] == 4
    else:  # pragma: no cover - CI 使用 3.12
        assert hits == []


def test_comment_only_mentions_are_not_flagged(tmp_path):
    """注释里描述历史缺陷不算违规（避免误伤说明文字）。"""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "ok.py").write_text(
        "# 历史写法 organization_id: int = 1 与 organization_id or 1 已清除\n"
        "def a(organization_id: int | None = None):\n    pass\n",
        encoding="utf-8",
    )
    assert checker.find_violations(app_dir) == []


def test_non_matching_values_are_not_flagged(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "good.py").write_text(
        "def a(organization_id: int = 10):\n    pass\n"
        "def b(organization_id: int | None = None):\n    pass\n"
        "def c(organization_id: int):\n    pass\n"
        "def d(user):\n    return user.organization_id or 10\n",
        encoding="utf-8",
    )
    assert checker.main(["--root", str(app_dir)]) == 0


def test_real_app_tree_is_green():
    assert checker.main(["--root", str(BACKEND_ROOT / "app")]) == 0


def test_missing_root_exits_2(tmp_path):
    assert checker.main(["--root", str(tmp_path / "nope")]) == 2


def test_end_to_end_subprocess(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "bad.py").write_text(
        "def f(organization_id: int = 1):\n    pass\n", encoding="utf-8"
    )
    proc = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--root", str(app_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1
    assert "bad.py" in proc.stderr
