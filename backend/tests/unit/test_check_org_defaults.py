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
    (app_dir / "bad.py").write_text(
        "def f(organization_id:int=1):\n    pass\n", encoding="utf-8"
    )
    assert checker.find_violations(app_dir), "必须容忍空白差异"


def test_non_matching_values_are_not_flagged(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "good.py").write_text(
        "def a(organization_id: int = 10):\n    pass\n"
        "def b(organization_id: int | None = None):\n    pass\n"
        "def c(organization_id: int):\n    pass\n",
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
