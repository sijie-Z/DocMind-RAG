"""CI 门禁：禁止把 ``users.role == "admin"`` 再当作管理员判据。

背景（W0-1 / ADR-001 D3）：历史上 ``role="admin"`` 被隐式映射到全局
SysAdmin，组织资源端点也散落 ``current_user.role != "admin"`` 判定，导致
任何 ``users.role='admin'`` 账号跨组织提权。W0-1 起：

- 平台级只认显式 ``is_superuser``（``core.security.is_platform_admin``）；
- 组织管理员只认 ``user_organization_role_association`` 的 OrgAdmin 角色
  （``core.security.is_org_admin`` / ``require_org_admin``）；
- ``users.role`` 仅作展示标签，**不得参与任何鉴权判断**。

本检查器用 AST（零第三方依赖）扫描 ``backend/app`` 生产代码中的
「身份对象的 role 属性与管理员字面量比较」：

- 命中：``current_user.role != "admin"``、``user.role == "admin"``、
  ``(u.role or "").lower() != "admin"``、``u.role.casefold()``、
  ``u.role.strip() == "admin"``、``getattr(u, "role", "")``、
  ``User.role == "admin"``、``User.role.in_(["admin"])``、
  ``alias = u.role; alias == "admin"``、``ADMIN = "admin"; u.role == ADMIN``、
  ``ROLES = {"admin"}; u.role in ROLES`` 等；
- 不命中：请求体字段校验 ``body.role == "admin"``（``body`` / ``payload``
  / ``data`` / ``request`` 等输入对象在允许名单内），以及普通局部变量
  ``role == "admin"``（不是身份属性读取）。
- 语法损坏的文件按违规处理（fail closed），不静默跳过。

覆盖边界：本门禁针对直接表达式与同模块简单赋值传播；跨函数 helper
（``def is_admin(r): return r == "admin"; is_admin(u.role)``）、跨模块导入
常量、f-string/海象等间接形态不在静态可判范围——它是回归护栏，不构成
「穷尽禁止」的证明。

用法::

    python scripts/check_rbac_admin_bypass.py            # 扫描 backend/app
    python scripts/check_rbac_admin_bypass.py --root DIR # 扫描指定目录（测试用）

退出码：0 = 通过；1 = 命中违规/扫描失败；2 = 扫描根不存在。
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


def _is_admin_constant(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.lower() == "admin"
    )


def _contains_admin_literal(node: ast.AST, admin_names: set[str]) -> bool:
    """比较项/容器中是否出现管理员字面量（大小写不敏感）或指向它的名字。"""
    if _is_admin_constant(node):
        return True
    if isinstance(node, ast.Name) and node.id in admin_names:
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return any(_contains_admin_literal(elt, admin_names) for elt in node.elts)
    if isinstance(node, ast.Dict):
        return any(
            _contains_admin_literal(item, admin_names)
            for item in [*node.keys, *node.values]
            if item is not None
        )
    return False


def _role_roots(node: ast.AST, role_aliases: set[str]) -> set[str]:
    """收集表达式中「读取身份对象 role 属性」的根变量名。

    覆盖 ``x.role``、任意链式字符串方法（``lower/strip/casefold``…）、
    ``(x.role or "").lower()``、``getattr(x, "role", "")``、``x.role.in_(...)``、
    以及经简单赋值传播的别名（``alias = x.role``）。
    """
    roots: set[str] = set()

    def walk(current: ast.AST) -> None:
        if isinstance(current, ast.Name):
            if current.id in role_aliases:
                roots.add(current.id)
            return
        if isinstance(current, ast.Compare):
            walk(current.left)
            for comparator in current.comparators:
                walk(comparator)
            return
        if isinstance(current, ast.BoolOp):
            for value in current.values:
                walk(value)
            return
        if isinstance(current, (ast.UnaryOp,)):
            walk(current.operand)
            return
        if isinstance(current, ast.IfExp):
            walk(current.body)
            walk(current.orelse)
            return
        if isinstance(current, ast.Subscript):
            walk(current.value)
            walk(current.slice)
            return
        if isinstance(current, (ast.Tuple, ast.List, ast.Set)):
            for elt in current.elts:
                walk(elt)
            return
        if isinstance(current, ast.Dict):
            for item in [*current.keys, *current.values]:
                if item is not None:
                    walk(item)
            return
        if isinstance(current, ast.Call):
            func = current.func
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
            # 链式方法（lower/strip/casefold/...）：继续向下追来源。
            if isinstance(func, ast.Attribute):
                walk(func.value)
            elif isinstance(func, ast.Name):
                # str(...) / bool(...) 等包裹：参数里可能含 role 读取。
                pass
            for arg in current.args:
                walk(arg)
            return
        if isinstance(current, ast.Attribute) and current.attr == "role":
            root = _root_name(current)
            if root:
                roots.add(root)

    walk(node)
    return roots


def _collect_symbols(tree: ast.AST) -> tuple[set[str], set[str]]:
    """简单符号传播：身份 role 别名、以及值为 "admin" 的常量/容器名。

    只做单模块、单次赋值的最小子集——足以覆盖门禁绕过最常见的两种写法：
    ``alias = current_user.role; alias == "admin"`` 与
    ``ADMIN = "admin"; user.role == ADMIN`` / ``ROLES = {"admin"}``。
    """
    role_aliases: set[str] = set()
    admin_names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        name = target.id
        if _role_roots(node.value, role_aliases):
            role_aliases.add(name)
        value = node.value
        if isinstance(value, ast.Constant):
            is_admin_value = _is_admin_constant(value)
        elif isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            is_admin_value = any(_is_admin_constant(elt) for elt in value.elts)
        elif isinstance(value, ast.Dict):
            is_admin_value = any(
                _is_admin_constant(item)
                for item in [*value.keys, *value.values]
                if item is not None
            )
        else:
            is_admin_value = False
        if is_admin_value:
            admin_names.add(name)
    return role_aliases, admin_names


def _check_tree(
    tree: ast.AST, path: Path
) -> list[tuple[str, int, int, str]]:
    violations: list[tuple[str, int, int, str]] = []
    role_aliases, admin_names = _collect_symbols(tree)

    def _add(node: ast.AST, detail: str) -> None:
        violations.append(
            (
                str(path),
                getattr(node, "lineno", 0),
                getattr(node, "col_offset", 0),
                detail,
            )
        )

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            comparands = [node.left, *node.comparators]
            if not any(
                _contains_admin_literal(item, admin_names) for item in comparands
            ):
                continue
            roots: set[str] = set()
            for item in comparands:
                if _contains_admin_literal(item, admin_names) and not _role_roots(
                    item, role_aliases
                ):
                    continue
                roots |= _role_roots(item, role_aliases)
            offending = sorted(roots - INPUT_ROOTS)
            if offending:
                _add(
                    node,
                    "role 属性与 \"admin\" 比较（身份判定）："
                    + ", ".join(offending)
                    + "；平台管理员用 is_platform_admin()，"
                    "组织管理员用 is_org_admin()/require_org_admin()",
                )
        elif isinstance(node, ast.Call):
            # 方法形态：User.role.in_(["admin"]) / user.role.startswith("admin")
            # / u.role.casefold() == ...（后者由 Compare 分支覆盖）。
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            roots = _role_roots(func.value, role_aliases)
            if not roots:
                continue
            arg_has_admin = any(
                _contains_admin_literal(arg, admin_names) for arg in node.args
            )
            # in_/notin_ 是 SQLAlchemy 的身份查询；startswith/endswith/contains
            # 等字符串方法一旦与 admin 字面量组合，同样是在按角色名做鉴权。
            if arg_has_admin and (
                func.attr in ("in_", "notin_")
                or func.attr in (
                    "startswith",
                    "endswith",
                    "contains",
                    "find",
                    "count",
                    "index",
                )
            ):
                offending = sorted(roots - INPUT_ROOTS)
                if offending:
                    _add(
                        node,
                        f"role.{func.attr}(...) 包含 \"admin\"（身份判定）："
                        + ", ".join(offending),
                    )
    return violations


def find_violations(root: Path) -> list[tuple[str, int, int, str]]:
    violations: list[tuple[str, int, int, str]] = []
    if not root.is_dir():
        return violations
    for path in sorted(root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append((str(path), 0, 0, f"无法读取（fail closed）：{exc}"))
            continue
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            # 语法损坏的文件可能藏有鉴权代码——静默跳过等于门禁 fail-open。
            violations.append(
                (str(path), exc.lineno or 0, exc.offset or 0, f"无法解析（fail closed）：{exc.msg}")
            )
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
