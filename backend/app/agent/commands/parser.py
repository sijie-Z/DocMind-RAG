"""命令文本解析 —— ``/name arg1 arg2`` → (name, tokens) → 校验后的参数字典。

契约（issue #498）：
- ``line`` 是 v1 规范形态；前导 ``/`` **可选**（``help`` 与 ``/help`` 等价，
  解析时统一归一为带 ``/`` 的内部形态）。
- ``command + args`` 不是 v1 契约，只是 HTTP 兼容层：端点用 ``compose_line``
  把 ``{"command","args"}`` 拼回一行再走同一解析/绑定路径。

刻意保持简单：命令是"一行文本"，参数按声明顺序位置绑定、空白分隔（不支持
引号/flag）。解析失败全部抛 ``CommandSyntaxError``（带 error_code + field），
由 ``execute()`` 收敛为字段级错误——绝不把半解析结果交给 handler。

变异点（tests/unit/test_user_commands.py 锁定）：
- 跳过 required / choices / 多余参数校验 → 三个字段级用例红；
- 缺失命令名返回空解析而不是显式错误 → missing_command 用例红；
- 去掉前导 / 归一 → 裸 ``help`` 用例红（issue #498）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.agent.commands.types import (
    ERROR_INVALID_ARGUMENT,
    ERROR_MISSING_ARGUMENT,
    ERROR_MISSING_COMMAND,
    ERROR_UNEXPECTED_ARGUMENT,
    Command,
)


class CommandSyntaxError(Exception):
    """命令文本 / 参数非法（字段级）。"""

    def __init__(self, code: str, message: str, *, field_name: str = ""):
        self.code = code
        self.message = message
        self.field_name = field_name
        super().__init__(message)


def parse_line(line: str) -> tuple[str, list[str]]:
    """把一行文本解析为 (规范命令名, 原始参数 tokens)。

    - 前导 ``/`` 可选（``help`` 与 ``/help`` 等价，issue #498）；
    - 命令名大小写归一为小写（``/Help`` == ``/help``）；
    - 只按空白切分，不做 shell 展开——命令文本从不经过 shell。
    """
    text = (line or "").strip()
    if text.startswith("/"):
        text = text[1:].strip()
    if not text:
        raise CommandSyntaxError(
            ERROR_MISSING_COMMAND, "缺少命令名；输入 /help 查看可用命令", field_name="name"
        )
    tokens = text.split()
    return tokens[0].lower(), tokens[1:]


def compose_line(
    command: str,
    args: Sequence[Any] | Mapping[str, Any] | None = None,
    *,
    arg_specs: Sequence[Any] = (),
) -> str:
    """兼容层：把 ``{"command","args"}`` 拼回规范 ``/<name> <args...>`` 一行。

    - ``command`` 可带可不带前导 ``/``；带参数文本（如 ``export json``）时原样保留；
    - ``args`` 为 list/tuple → 按顺序拼接；为 mapping 时按命令声明的参数顺序
      （``arg_specs``）**连续前缀**绑定：提供后面的参数而缺少前面的位置参数时，
      若前面的参数有默认值则用默认值补齐，否则显式报 ``missing_argument``——
      绝不把后面的值错绑到前面的位置（评审 m2）；未知键显式报错；
    - ``arg_specs`` 为空时退回映射自身顺序（仅用于命令未解析的兼容路径）；
    - ``None`` 值跳过（与"未提供"同义）。
    """
    name = (command or "").strip().lstrip("/").strip()
    if not name:
        raise CommandSyntaxError(
            ERROR_MISSING_COMMAND, "缺少命令名；输入 /help 查看可用命令", field_name="name"
        )
    tokens: list[str] = []
    if isinstance(args, Mapping):
        if arg_specs:
            positions = {spec.name: index for index, spec in enumerate(arg_specs)}
            unknown = [key for key in args if key not in positions]
            if unknown:
                raise CommandSyntaxError(
                    ERROR_UNEXPECTED_ARGUMENT,
                    f"未知参数：{', '.join(unknown)}",
                    field_name=unknown[0],
                )
            provided = [positions[key] for key in args if args[key] is not None]
            if provided:
                for spec in arg_specs[: max(provided) + 1]:
                    if spec.name in args and args[spec.name] is not None:
                        tokens.append(str(args[spec.name]))
                    elif spec.default is not None:
                        tokens.append(str(spec.default))
                    else:
                        raise CommandSyntaxError(
                            ERROR_MISSING_ARGUMENT,
                            f"缺少前置位置参数：{spec.name}（位置参数不能跳过）",
                            field_name=spec.name,
                        )
        else:
            tokens = [str(args[key]) for key in args if args[key] is not None]
    elif isinstance(args, (list, tuple)):
        tokens = [str(value) for value in args if value is not None]
    elif args is not None:
        tokens = [str(args)]
    return "/" + " ".join([name, *tokens])


def bind_args(command: Command, tokens: list[str]) -> dict[str, str]:
    """按声明顺序把 tokens 绑定到命令参数，并做字段级校验。"""
    if len(tokens) > len(command.args):
        extra = tokens[len(command.args)]
        raise CommandSyntaxError(
            ERROR_UNEXPECTED_ARGUMENT,
            f"命令 /{command.name} 不接受多余参数：{extra}",
            field_name=command.args[-1].name if command.args else "args",
        )

    bound: dict[str, str] = {}
    for index, arg in enumerate(command.args):
        if index < len(tokens):
            value = tokens[index]
            if arg.choices:
                # choices 声明均为小写；归一后比较，`/export JSON` 与
                # `/export json` 等价（评审 n1）。
                normalized = value.lower()
                if normalized in arg.choices:
                    value = normalized
                else:
                    choices = " / ".join(arg.choices)
                    raise CommandSyntaxError(
                        ERROR_INVALID_ARGUMENT,
                        f"参数 {arg.name} 取值非法：{value}（可选：{choices}）",
                        field_name=arg.name,
                    )
            bound[arg.name] = value
        elif arg.required:
            raise CommandSyntaxError(
                ERROR_MISSING_ARGUMENT,
                f"缺少必填参数：{arg.name}",
                field_name=arg.name,
            )
        elif arg.default is not None:
            bound[arg.name] = arg.default
    return bound
