"""acp 入口桩（issue #481 实现；本期 exit 2）。"""

from __future__ import annotations

import argparse
import sys


def run(args: argparse.Namespace | None = None, profile: str = "acp") -> None:
    print("acp entrypoint not implemented (issue #481)", file=sys.stderr)
    sys.exit(2)
