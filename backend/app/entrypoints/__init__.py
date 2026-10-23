"""入口实现：每个 profile 对应一个模块（issue #479）。

模块约定：``run(args: argparse.Namespace) -> int | None``。
``web`` / ``worker`` 阻塞运行（返回 ``None`` 表示正常退出）；
``headless`` 返回进程退出码；``sdk`` / ``acp`` 本期为桩（exit 2）。
"""
