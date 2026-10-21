"""门禁 `scripts/check_rbac_admin_bypass.py` 的鉴别力测试。

证明门禁「有牙」且不误伤：
- ``current_user.role != "admin"`` / ``getattr(user, "role", "")`` /
  ``(u.role or "").lower() == "admin"`` 等身份判定必须红（退出码 1）；
- 请求体字段校验 ``body.role == "admin"``、普通局部变量
  ``role == "admin"``、注释/文档字符串不得误报；
- 真实 ``backend/app`` 树必须绿；
- 扫描根不存在时退出码 2（与既有检查器同约定）。

用 importlib 按路径加载脚本，CI hygiene job 以 ``--noconftest`` 运行时同样成立。
"""

import importlib.util
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "check_rbac_admin_bypass.py"


def _load_checker():
    spec = importlib.util.spec_from_file_location("check_rbac_admin_bypass", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _write(tmp_path: Path, source: str) -> Path:
    app_dir = tmp_path / "app"
    app_dir.mkdir(exist_ok=True)
    (app_dir / "sample.py").write_text(source, encoding="utf-8")
    return app_dir


def test_identity_role_checks_are_red(tmp_path):
    app_dir = _write(
        tmp_path,
        "def a(current_user):\n"
        "    if current_user.role != 'admin':\n"
        "        raise RuntimeError\n"
        "def b(user):\n"
        "    return user.role == 'admin'\n"
        "def c(u):\n"
        "    return (u.role or '').lower() == 'admin'\n"
        "def d(u):\n"
        "    return getattr(u, 'role', '') != 'admin'\n"
        "def e(User):\n"
        "    return User.role == 'admin'\n",
    )
    violations = checker.find_violations(app_dir)
    assert len(violations) == 5, violations
    assert checker.main(["--root", str(app_dir)]) == 1


def test_method_chain_alias_and_constant_variants_are_red(tmp_path):
    """门禁必须覆盖链式字符串方法、别名传播、常量折叠与 SQLAlchemy in_。"""
    app_dir = _write(
        tmp_path,
        "ADMIN = 'Admin'\n"
        "ADMIN_ROLES = {'admin', 'superuser'}\n"
        "def a(u):\n"
        "    return u.role.casefold() == 'admin'\n"
        "def b(u):\n"
        "    return (u.role or '').strip().lower() == ADMIN\n"
        "def c(u):\n"
        "    alias = u.role\n"
        "    return alias == 'admin'\n"
        "def d(role_expr):\n"
        "    return role_expr.role in ADMIN_ROLES\n"
        "def e(User):\n"
        "    return User.role.in_(['admin'])\n"
        "def f(u):\n"
        "    return u.role.startswith('admin')\n"
        "def g(u):\n"
        "    return 'admin' in (u.role,)\n",
    )
    violations = checker.find_violations(app_dir)
    # a-g 各一处；ADMIN_ROLES 赋值不单独计违规
    assert len(violations) == 7, violations
    assert all("role" in message for _, _, _, message in violations)


def test_input_field_validation_is_green(tmp_path):
    app_dir = _write(
        tmp_path,
        "def f(body):\n"
        "    if body.role == 'admin':\n"
        "        return 'admin'\n"
        "    return 'user'\n"
        "def g(payload):\n"
        "    return payload.role in ('admin', 'user')\n"
        "def h(role):\n"
        "    if role != 'admin':\n"
        "        return None\n",
    )
    assert checker.find_violations(app_dir) == []
    assert checker.main(["--root", str(app_dir)]) == 0


def test_syntax_error_fails_closed(tmp_path):
    app_dir = _write(tmp_path, "def broken(:\n    pass\n")
    violations = checker.find_violations(app_dir)
    assert violations and "无法解析" in violations[0][3]
    assert checker.main(["--root", str(app_dir)]) == 1


def test_comments_and_docstrings_are_green(tmp_path):
    app_dir = _write(
        tmp_path,
        '"""历史：current_user.role != "admin" 曾用于鉴权。"""\n'
        "# current_user.role == 'admin' 已废弃\n"
        "x = 1\n",
    )
    assert checker.find_violations(app_dir) == []


def test_real_backend_app_is_green():
    assert checker.find_violations(BACKEND_ROOT / "app") == []


def test_missing_root_returns_2(tmp_path):
    assert checker.main(["--root", str(tmp_path / "nope")]) == 2
