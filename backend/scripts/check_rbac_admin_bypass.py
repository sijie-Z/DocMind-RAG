"""CI 门禁：禁止把 ``users.role == "admin"`` 再当作管理员判据。

背景（W0-1 / ADR-001 D3）：历史上 ``role="admin"`` 被隐式映射到全局
SysAdmin，组织资源端点也散落 ``current_user.role != "admin"`` 判定，导致
任何 ``users.role='admin'`` 账号跨组织提权。W0-1 起：

- 平台级只认显式 ``is_superuser``（``core.security.is_platform_admin``）；
- 组织管理员只认 ``user_organization_role_association`` 的 OrgAdmin 角色
  （``core.security.is_org_admin`` / ``require_org_admin``）；
- ``users.role`` 仅作展示标签，**不得参与任何鉴权判断**。

本检查器用 AST（零第三方依赖）扫描 ``backend/app`` 生产代码中的
「用户对象 / 身份对象的 ``.role`` 与 ``"admin"`` 比较」：

- 命中：``current_user.role != "admin"``、``user.role == "admin"``、
  ``(u.role or "").lower() != "admin"``、``getattr(u, "role", "")``、
  ``User.role == "admin"``、``"admin" in (...)`` 等；
- 不命中：请求体字段校验 ``body.role == "admin"``（``body`` / ``payload``
  / ``data`` / ``request`` 等输入对象在允许名单内），以及普通局部变量
  ``role == "admin"``（不是身份属性读取）。

用法::

    python scripts/check_rbac_admin_bypass.py            # 扫描 backend/app
    python scripts/check_rbac_admin_bypass.py --root DIR # 扫描指定目录（测试用）

退出码：0 = 通过；1 = 命中违规；2 = 扫描根不存在。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ROOT = BACKEND_DIR / "app"

# 请求/输入对象的 role 字段属于参数校验，不是身份判定，允许与 "admin" 比较。
INPUT_ROOTS = {
    "body",
    "payload",
    "data",
    "request",
    "params",
    "query",
    "form",
    "schema",
}


def _root_name(node: ast.AST) -> str | None:
    """返回属性链的根变量名：``a.b.role`` → ``a``。"""
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def _contains_admin_literal(node: ast.AST) -> bool:
    """比较项/容器中是否出现字符串常量 "admin"。"""
    if isinstance(node, ast.Constant) and node.value == "admin":
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_contains_admin_literal(elt) for elt in node.elts)
    return False


def _role_roots(node: ast.AST) -> set[str]:
    """收集比较表达式中「读取身份对象 role 属性」的根变量名。

    覆盖 ``x.role``、``x.role.lower()``、``(x.role or "").lower()``、
    ``getattr(x, "role", "")`` 等形态。
    """
    roots: set[str] = set()

    def walk(current: ast.AST) -> None:
        if isinstance(current, ast.Compare):
            walk(current.left)
            for comparator in current.comparators:
                walk(comparator)
            return
        if isinstance(current, ast.BoolOp):
            for value in current.values:
                walk(value)
            return
        if isinstance(current, ast.UnaryOp):
            walk(current.operand)
            return
        if isinstance(current, ast.IfExp):
            walk(current.body)
            walk(current.orelse)
            return
        if isinstance(current, ast.Call):
            func = current.func
            if isinstance(func, ast.Attribute) and func.attr == "lower":
                walk(func.value)
                return
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(current.args) >= 2
                and isinstance(current.args[1], ast.Constant)
                and current.args[1].value == "role"
            ):
                root = _root_name(current.args[0])
                if root:
                    roots.add(root)
                return
            return
        if isinstance(current, ast.Attribute) and current.attr == "role":
            root = _root_name(current)
            if root:
                roots.add(root)

    walk(node)
    return roots


def _check_tree(tree: ast.AST, path: Path) -> list[tuple[str, int, int, str]]:
    violations: list[tuple[str, int, int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        comparands = [node.left, *node.comparators]
        if not any(_contains_admin_literal(item) for item in comparands):
            continue
        roots = set()
        for item in comparands:
            if _contains_admin_literal(item) and not _role_roots(item):
                continue
            roots |= _role_roots(item)
        offending = sorted(roots - INPUT_ROOTS)
        if offending:
            violations.append(
                (
                    str(path),
                    node.lineno,
                    node.col_offset,
                    "role 属性与 \"admin\" 比较（身份判定）："
                    + ", ".join(offending)
                    + "；平台管理员用 is_platform_admin()，"
                    "组织管理员用 is_org_admin()/require_org_admin()",
                )
            )
    return violations


def find_violations(root: Path) -> list[tuple[str, int, int, str]]:
    violations: list[tuple[str, int, int, str]] = []
    if not root.is_dir():
        return violations
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (OSError, SyntaxError):
            continue
        violations.extend(_check_tree(tree, path))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="禁止 users.role 参与管理员鉴权（W0-1 / ADR-001 D3）"
    )
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help="扫描根目录（默认 backend/app）",
    )
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"[rbac-admin-bypass] 扫描根不存在: {root}", file=sys.stderr)
        return 2

    violations = find_violations(root)
    if not violations:
        print(f"[rbac-admin-bypass] OK：{root} 未发现 role 与 \"admin\" 的鉴权比较")
        return 0
    for file_path, lineno, col, message in violations:
        print(f"{file_path}:{lineno}:{col}: {message}", file=sys.stderr)
    print(
        f"[rbac-admin-bypass] 发现 {len(violations)} 处违规："
        "users.role 不得作为管理员判据（见 ADR-001 D3）",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
