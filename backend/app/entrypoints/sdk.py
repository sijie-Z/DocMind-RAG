"""sdk 入口桩（issue #480 实现；本期 exit 2）。"""

from __future__ import annotations

import argparse
import sys


def run(args: argparse.Namespace | None = None, profile: str = "sdk") -> None:
    print("sdk entrypoint not implemented (issue #480)", file=sys.stderr)
    sys.exit(2)
