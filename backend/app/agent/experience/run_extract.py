#!/usr/bin/env python3
"""Extract experiences from benchmark failure/partial cases and store them.

This is the bootstrap script for the Experience Memory system。定位（#242）：
dev-only 引导工具——无运行时调用者；于 benchmark 跑批后手动执行。

It scans the case dumps written by benchmark runs (``benchmark/cases/failure/``
与 ``benchmark/cases/partial/``，见 ``benchmark/scorer.save_case``)；干净检出中
``benchmark/cases/`` 不存在（cases 由跑批本地生成），缺失目录会被跳过、抽取为 0。
The rule-based extractor writes the resulting experiences to:
    - Redis (for runtime access)
    - benchmark/experiences.json (for reproducibility)

Usage:
    cd DocMind/backend
    python -m app.agent.experience.run_extract
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Ensure backend/ is importable（本文件位于 backend/app/agent/experience/）
BACKEND_DIR = str(Path(__file__).resolve().parents[3])
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("experience_extract")


async def main():
    print("=" * 60)
    print("  Experience Memory — Bootstrap Extraction")
    print("=" * 60)

    from app.agent.experience import extract_all_from_benchmark, get_experience_store

    # ── Step 1: Extract from benchmark failures ──
    print("\n[1/3] Scanning benchmark failure/partial cases...")
    if not (Path("benchmark/cases/failure").exists() or Path("benchmark/cases/partial").exists()):
        print("  ⚠️  benchmark/cases/{failure,partial} 不存在（cases 由跑批生成）——")
        print("      先跑一次 benchmark（python -m benchmark.run），否则本次抽取为 0（no-op）。")
    count = await extract_all_from_benchmark()
    print(f"  → Extracted {count} new experiences")

    # ── Step 2: Show what we extracted ──
    store = get_experience_store()
    print(f"\n[2/3] Total in store: {await store.count()}")
    all_exps = await store.list_all()
    for exp in all_exps:
        print(f"  [{exp.confidence:.0%}] {exp.scenario:25s} | {exp.lesson[:70]}...")

    # ── Step 3: Verify persistence ──
    print("\n[3/3] Persistence check:")
    local_path = Path("benchmark/experiences.json")
    if local_path.exists():
        data = local_path.read_text(encoding="utf-8")
        import json
        parsed = json.loads(data)
        print(f"  ✅ Local JSON: {len(parsed)} experiences → {local_path}")
    else:
        print(f"  ⚠️  Local JSON not found at {local_path}")

    print("\n" + "=" * 60)
    print("  Bootstrap complete. Experiences ready for Planner injection.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
