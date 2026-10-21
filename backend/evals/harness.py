"""评估编排：golden set × 检索调用 → 逐条判定（纯逻辑，检索以函数注入）。

检索调用以 ``RetrieveFn`` 协议注入：
- runner.py 注入真实 HybridRetriever（live）；
- 单元测试注入假件（fake），无需 ES / 网络即可判别 harness 行为。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from .golden import GoldenItem
from .metrics import ItemOutcome, evaluate_item


@dataclass
class RetrievalObservation:
    """一次检索调用的观测结果（live 适配器与测试假件共用）。"""

    results: list[dict] = field(default_factory=list)
    strategy: str | None = None
    degraded: list[str] = field(default_factory=list)


class RetrieveFn(Protocol):
    async def __call__(self, query: str, top_k: int) -> RetrievalObservation: ...


@dataclass
class ItemRun:
    """单条 golden 的完整运行记录（判定 + 原始观测，供报告与诊断）。"""

    item: GoldenItem
    outcome: ItemOutcome
    results: list[dict]
    elapsed_ms: float = 0.0
    degraded: list[str] = field(default_factory=list)


async def run_eval(
    items: tuple[GoldenItem, ...] | list[GoldenItem],
    retrieve: RetrieveFn,
    top_k: int,
) -> list[ItemRun]:
    """跑完整集。单条查询失败 → status=error（如实记录，不当作 miss）。"""
    runs: list[ItemRun] = []
    for item in items:
        start = time.perf_counter()
        try:
            obs = await retrieve(item.query, top_k)
        except Exception as e:  # noqa: BLE001 — 单条失败不中断全集，如实记录
            elapsed = (time.perf_counter() - start) * 1000
            outcome = evaluate_item(item, [], status="error", error=f"{type(e).__name__}: {e}")
            runs.append(ItemRun(item=item, outcome=outcome, results=[], elapsed_ms=elapsed))
            continue
        elapsed = (time.perf_counter() - start) * 1000
        outcome = evaluate_item(item, obs.results, strategy=obs.strategy)
        runs.append(
            ItemRun(
                item=item,
                outcome=outcome,
                results=obs.results,
                elapsed_ms=elapsed,
                degraded=list(obs.degraded),
            )
        )
    return runs
