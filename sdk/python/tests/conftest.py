"""让 `python -m pytest sdk/python/tests`（仓库根目录）能找到 docmind 包。"""

import sys
from pathlib import Path

_SDK_ROOT = Path(__file__).resolve().parents[1]
if str(_SDK_ROOT) not in sys.path:
    sys.path.insert(0, str(_SDK_ROOT))
