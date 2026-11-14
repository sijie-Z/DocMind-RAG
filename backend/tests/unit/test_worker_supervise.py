"""worker 重投监督器：坏文档不得让进程永久退出（否则整个租户入库停摆）。"""

from __future__ import annotations

from app.worker.consumer import RedeliveryHalt, supervise


class _Runner:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome


def _noop_sleep(_: float) -> None:
    return None


def test_success_returns_zero_without_retry() -> None:
    runner = _Runner([None])
    assert supervise(runner, sleep=_noop_sleep) == 0
    assert runner.calls == 1


def test_halt_is_retried_in_process() -> None:
    runner = _Runner([RedeliveryHalt("bad doc"), RedeliveryHalt("bad doc"), None])
    assert supervise(runner, sleep=_noop_sleep) == 0
    assert runner.calls == 3


def test_gives_up_after_budget() -> None:
    runner = _Runner([RedeliveryHalt("bad doc")] * 10)
    assert supervise(runner, max_halts=2, sleep=_noop_sleep) == 1
    assert runner.calls == 3  # 首次 + 2 次重试


def test_backoff_is_awaited_between_attempts() -> None:
    slept: list[float] = []
    runner = _Runner([RedeliveryHalt("x"), None])
    supervise(runner, backoff=1.5, sleep=slept.append)
    assert slept == [1.5]
