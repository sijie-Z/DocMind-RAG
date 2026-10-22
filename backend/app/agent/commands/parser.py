"""命令文本解析 —— ``/name arg1 arg2`` → (name, tokens) → 校验后的参数字典。

刻意保持简单：命令是"一行文本"，参数按声明顺序位置绑定、空白分隔（不支持
引号/flag）。解析失败全部抛 ``CommandSyntaxError``（带 error_code + field），
由 ``execute()`` 收敛为字段级错误——绝不把半解析结果交给 handler。

变异点（tests/unit/test_user_commands.py 锁定）：
- 跳过 required / choices / 多余参数校验 → 三个字段级用例红；
- 缺失命令名返回空解析而不是显式错误 → missing_command 用例红。
"""

from __future__ import annotations

from app.agent.commands.types import (
    ERROR_INVALID_ARGUMENT,
    ERROR_MISSING_ARGUMENT,
    ERROR_MISSING_COMMAND,
    ERROR_NOT_A_COMMAND,
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

    - 必须以 ``/`` 开头（否则 ``not_a_command``）；
    - 命令名大小写归一为小写（``/Help`` == ``/help``）；
    - 只按空白切分，不做 shell 展开——命令文本从不经过 shell。
    """
    text = (line or "").strip()
    if not text.startswith("/"):
        raise CommandSyntaxError(ERROR_NOT_A_COMMAND, "命令必须以 / 开头；输入 /help 查看可用命令")
    body = text[1:].strip()
    if not body:
        raise CommandSyntaxError(
            ERROR_MISSING_COMMAND, "缺少命令名；输入 /help 查看可用命令", field_name="name"
        )
    tokens = body.split()
    return tokens[0].lower(), tokens[1:]


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
